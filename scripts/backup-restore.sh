#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# backup-restore.sh — Listar y restaurar backups del VPS desde Cloudflare R2
#
# Usa el remote rclone 'r2' que ya dejó configurado backup-r2-setup.sh.
# NUNCA sobreescribe las bases de datos vivas: descarga a un directorio de
# staging y verifica. Poner una DB restaurada en producción es un paso MANUAL
# (hay que parar el container antes) que el script te explica al final.
#
# USO (en el VPS):
#   bash scripts/backup-restore.sh fechas                # lista las fechas disponibles
#   bash scripts/backup-restore.sh ls <fecha>            # lista los archivos de esa fecha
#   bash scripts/backup-restore.sh get <fecha> <archivo> [destino_dir]
#                                                        # descarga 1 archivo (lo descomprime + verifica)
#   bash scripts/backup-restore.sh verify <fecha>        # baja TODOS los SQLite y corre integrity_check
#
# Ejemplos:
#   bash scripts/backup-restore.sh fechas
#   bash scripts/backup-restore.sh get 2026-06-09 panel.sqlite.gz
#   bash scripts/backup-restore.sh verify 2026-06-09
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

R2_REMOTE="r2"
R2_BUCKET="backups-paracarpinteros"
HOSTTAG="$(hostname -s 2>/dev/null || echo vps)"
BASE="${R2_REMOTE}:${R2_BUCKET}/${HOSTTAG}"

# Mapa: nombre de artefacto → ruta de la DB viva (para mostrar el "cómo restaurar")
live_path_for() {
  case "$1" in
    panel.sqlite|panel.sqlite.gz)         echo "/opt/paracarpinteros-odoo/correos-cr-bridge/data/panel.sqlite" ;;
    conversations.db|conversations.db.gz) echo "/opt/whatsapp-bot/data/conversations.db" ;;
    buzon.db|buzon.db.gz)                 echo "/opt/paracarpinteros-odoo/fe-signer/storage/buzon.db" ;;
    *) echo "" ;;
  esac
}
container_for() {  # container a parar antes de pisar la DB viva
  case "$1" in
    panel.sqlite*)        echo "bridge (cd /opt/paracarpinteros-odoo/correos-cr-bridge)" ;;
    conversations.db*)    echo "wa-bot-paracarpinteros (cd /opt/whatsapp-bot)" ;;
    buzon.db*)            echo "fe-signer (cd /opt/paracarpinteros-odoo/fe-signer)" ;;
    *) echo "" ;;
  esac
}

command -v rclone >/dev/null 2>&1 || { echo "!! rclone no está instalado. Corré antes backup-r2-setup.sh." >&2; exit 1; }

integrity_check() {  # $1=ruta a un .sqlite/.db descomprimido
  local f="$1"
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "   (sqlite3 no instalado: salto integrity_check de $(basename "$f"))"; return 0
  fi
  local res
  res="$(sqlite3 "$f" 'PRAGMA integrity_check;' 2>&1 | head -1)"
  if [[ "$res" == "ok" ]]; then
    echo "   ✅ integrity_check OK: $(basename "$f")"
  else
    echo "   ❌ integrity_check FALLÓ: $(basename "$f") → $res"; return 1
  fi
}

# Descomprime si es .gz y verifica si es SQLite. Imprime la ruta final.
post_process() {  # $1=ruta del archivo descargado
  local f="$1"
  if [[ "$f" == *.gz ]]; then
    gunzip -f "$f" && f="${f%.gz}"
  fi
  case "$f" in
    *.sqlite|*.db) integrity_check "$f" ;;
  esac
  echo "   → $f"
}

cmd="${1:-}"
case "$cmd" in
  fechas|dates)
    echo "Fechas disponibles en ${BASE}/ :"
    rclone lsf "${BASE}/" --dirs-only 2>/dev/null | sed 's#/$##' | sort || {
      echo "!! No pude listar ${BASE}/ (¿bucket vacío o sin acceso?)" >&2; exit 1; }
    ;;

  ls|list)
    fecha="${2:-}"; [[ -z "$fecha" ]] && { echo "Uso: $0 ls <fecha>" >&2; exit 1; }
    echo "Archivos en ${BASE}/${fecha}/ :"
    rclone ls "${BASE}/${fecha}/" 2>/dev/null || { echo "!! Sin archivos para ${fecha}" >&2; exit 1; }
    ;;

  get)
    fecha="${2:-}"; archivo="${3:-}"; destino="${4:-./restore-${fecha}}"
    [[ -z "$fecha" || -z "$archivo" ]] && { echo "Uso: $0 get <fecha> <archivo> [destino_dir]" >&2; exit 1; }
    mkdir -p "$destino"
    echo "Descargando ${BASE}/${fecha}/${archivo} → ${destino}/ …"
    rclone copy "${BASE}/${fecha}/${archivo}" "${destino}/" --progress || { echo "!! Falló la descarga" >&2; exit 1; }
    post_process "${destino}/${archivo}"
    live="$(live_path_for "$archivo")"
    cont="$(container_for "$archivo")"
    if [[ -n "$live" ]]; then
      echo
      echo "── Para poner esta copia EN PRODUCCIÓN (manual, con cuidado) ──"
      echo "  1. Parar el container: ${cont} && docker compose stop"
      echo "  2. Respaldar la viva:  cp '${live}' '${live}.antes-restore'"
      echo "  3. Copiar la restaurada: cp '${destino}/${archivo%.gz}' '${live}'"
      echo "  4. Arrancar: docker compose up -d"
    fi
    ;;

  verify)
    fecha="${2:-}"; [[ -z "$fecha" ]] && { echo "Uso: $0 verify <fecha>" >&2; exit 1; }
    tmp="$(mktemp -d /tmp/r2verify.XXXXXX)"
    trap 'rm -rf "$tmp"' EXIT
    echo "Verificando SQLite del backup ${fecha} (staging en $tmp)…"
    rc=0
    for art in panel.sqlite.gz conversations.db.gz buzon.db.gz; do
      if rclone copy "${BASE}/${fecha}/${art}" "${tmp}/" 2>/dev/null && [[ -f "${tmp}/${art}" ]]; then
        gunzip -f "${tmp}/${art}"
        integrity_check "${tmp}/${art%.gz}" || rc=1
      else
        echo "   -- ${art} no está en ${fecha} (se salta)"
      fi
    done
    [[ $rc -eq 0 ]] && echo "Resultado: todos los SQLite verificados OK." || echo "Resultado: ⚠️  algún SQLite falló la verificación."
    exit $rc
    ;;

  ""|-h|--help|help)
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    ;;

  *)
    echo "Comando desconocido: '$cmd'. Usá: fechas | ls <fecha> | get <fecha> <archivo> | verify <fecha>" >&2
    exit 1
    ;;
esac
