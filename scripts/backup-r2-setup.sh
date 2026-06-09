#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# backup-r2-setup.sh — Instala y configura backups diarios del VPS a Cloudflare R2
#
# Qué respalda (copia CONSISTENTE de los SQLite con `sqlite3 .backup`, no `cp`):
#   - Bridge:   /opt/paracarpinteros-odoo/correos-cr-bridge/data/panel.sqlite
#   - WA bot:   /opt/whatsapp-bot/data/conversations.db
#   - Buzón FE: /opt/paracarpinteros-odoo/fe-signer/storage/buzon.db (si existe)
#   - Código + panel: tar del repo monorepo + del wa-bot (sin data/, .git, venvs)
#
# Destino: r2://backups-paracarpinteros/<host>/YYYY-MM-DD/
# Retención: 60 días.
# Cron: diario 03:15.
#
# USO (en el VPS, como root):
#   nano scripts/backup-r2-setup.sh   # editar las 3 variables R2_* de abajo
#   bash scripts/backup-r2-setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  EDITAR ESTAS 3 VARIABLES  (R2 → Manage R2 API Tokens → Object R&W)        ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
R2_ACCESS_KEY="PEGA_AQUI_ACCESS_KEY_ID"
R2_SECRET_KEY="PEGA_AQUI_SECRET_ACCESS_KEY"
R2_ACCOUNT_ID="PEGA_AQUI_ACCOUNT_ID"      # el del endpoint <ACCOUNT_ID>.r2.cloudflarestorage.com

# ── Constantes (no suele hacer falta tocarlas) ──────────────────────────────
R2_BUCKET="backups-paracarpinteros"
R2_REMOTE="r2"
RETENTION_DAYS=60
CRON_TIME="15 3"   # 03:15
RUNNER="/usr/local/bin/backup-paracarpinteros.sh"

echo "==> 1/6  Comprobando dependencias (rclone, sqlite3)…"
if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "    Instalando sqlite3…"
  if   command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq sqlite3
  elif command -v dnf     >/dev/null 2>&1; then dnf install -y -q sqlite
  elif command -v yum     >/dev/null 2>&1; then yum install -y -q sqlite
  else echo "    !! No sé instalar sqlite3 en esta distro. Instalalo a mano." >&2; exit 1
  fi
fi
if ! command -v rclone >/dev/null 2>&1; then
  echo "    Instalando rclone…"
  curl -fsSL https://rclone.org/install.sh | bash
fi

echo "==> 2/6  Configurando remote rclone '${R2_REMOTE}' (Cloudflare R2)…"
if [[ "$R2_ACCESS_KEY" == PEGA_AQUI* || "$R2_SECRET_KEY" == PEGA_AQUI* || "$R2_ACCOUNT_ID" == PEGA_AQUI* ]]; then
  echo "    !! Editá primero las 3 variables R2_* al principio del script." >&2
  exit 1
fi
rclone config delete "${R2_REMOTE}" 2>/dev/null || true
rclone config create "${R2_REMOTE}" s3 \
  provider Cloudflare \
  access_key_id "${R2_ACCESS_KEY}" \
  secret_access_key "${R2_SECRET_KEY}" \
  endpoint "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  region auto \
  acl private \
  --non-interactive >/dev/null
echo "    OK. Probando acceso al bucket…"
rclone lsd "${R2_REMOTE}:${R2_BUCKET}" >/dev/null && echo "    Bucket '${R2_BUCKET}' accesible."

echo "==> 3/6  Escribiendo el runner ${RUNNER}…"
# El runner NO lleva credenciales (viven en la config de rclone). Sólo constantes.
cat > "${RUNNER}" <<EOF
#!/usr/bin/env bash
# Generado por backup-r2-setup.sh — backup diario a Cloudflare R2.
set -uo pipefail

R2_REMOTE="${R2_REMOTE}"
R2_BUCKET="${R2_BUCKET}"
RETENTION_DAYS=${RETENTION_DAYS}
EOF
cat >> "${RUNNER}" <<'EOF'

HOSTTAG="$(hostname -s 2>/dev/null || echo vps)"
STAMP="$(date +%F)"                     # 2026-06-09
TS="$(date +%F_%H%M%S)"                 # 2026-06-09_031500
DEST="${R2_REMOTE}:${R2_BUCKET}/${HOSTTAG}/${STAMP}"
WORK="$(mktemp -d /tmp/pcbk.XXXXXX)"
log() { echo "[$(date +%H:%M:%S)] $*"; }
trap 'rm -rf "$WORK"' EXIT

BRIDGE_DB="/opt/paracarpinteros-odoo/correos-cr-bridge/data/panel.sqlite"
WABOT_DB="/opt/whatsapp-bot/data/conversations.db"
BUZON_DB="/opt/paracarpinteros-odoo/fe-signer/storage/buzon.db"

# ── 1. Copia consistente de los SQLite (.backup respeta WAL/locks) ──────────
backup_sqlite() {  # $1=ruta origen  $2=nombre destino
  local src="$1" name="$2"
  if [[ -f "$src" ]]; then
    if sqlite3 "$src" ".backup '${WORK}/${name}'" 2>/dev/null; then
      gzip -9 "${WORK}/${name}"
      log "SQLite OK: ${name} ($(du -h "${WORK}/${name}.gz" | cut -f1))"
    else
      log "!! Falló .backup de ${src} (¿corrupto/bloqueado?) — lo salto"
    fi
  else
    log "-- No existe ${src} — lo salto"
  fi
}
backup_sqlite "$BRIDGE_DB" "panel.sqlite"
backup_sqlite "$WABOT_DB"  "conversations.db"
backup_sqlite "$BUZON_DB"  "buzon.db"

# ── 2. Tar del código + panel (sin data/, .git, caches, venvs) ──────────────
tar_dir() {  # $1=ruta  $2=nombre.tar.gz
  local dir="$1" out="$2"
  if [[ -d "$dir" ]]; then
    tar czf "${WORK}/${out}" \
      --exclude='*/data' --exclude='*/__pycache__' --exclude='*.pyc' \
      --exclude='*/.git' --exclude='*/node_modules' --exclude='*/venv' \
      --exclude='*/.venv' --exclude='*/logs' --exclude='*.bak-*' \
      -C "$(dirname "$dir")" "$(basename "$dir")" 2>/dev/null \
      && log "TAR OK: ${out} ($(du -h "${WORK}/${out}" | cut -f1))" \
      || log "!! Falló tar de ${dir}"
  else
    log "-- No existe ${dir} — lo salto"
  fi
}
tar_dir "/opt/paracarpinteros-odoo" "monorepo.tar.gz"
tar_dir "/opt/whatsapp-bot"         "whatsapp-bot.tar.gz"

# ── 3. Subir a R2 ───────────────────────────────────────────────────────────
if ! ls "${WORK}/"* >/dev/null 2>&1; then
  log "!! No se generó ningún artefacto — abortando sin subir."
  exit 1
fi
log "Subiendo a ${DEST}/ …"
if rclone copy "${WORK}/" "${DEST}/" --s3-no-check-bucket --transfers 4; then
  log "Subida OK."
else
  log "!! Falló la subida a R2."; exit 1
fi

# ── 4. Retención: borrar > RETENTION_DAYS días ──────────────────────────────
log "Aplicando retención (${RETENTION_DAYS}d)…"
rclone delete "${R2_REMOTE}:${R2_BUCKET}" --min-age "${RETENTION_DAYS}d" 2>/dev/null || true
rclone rmdirs "${R2_REMOTE}:${R2_BUCKET}" --leave-root 2>/dev/null || true

log "Backup completado: ${DEST}/"
EOF
chmod +x "${RUNNER}"
echo "    Runner escrito y ejecutable."

echo "==> 4/6  Instalando cron diario (${CRON_TIME} → 03:15)…"
CRON_LINE="${CRON_TIME} * * * root ${RUNNER} >> /var/log/backup-paracarpinteros.log 2>&1"
echo "${CRON_LINE}" > /etc/cron.d/backup-paracarpinteros
chmod 644 /etc/cron.d/backup-paracarpinteros
echo "    /etc/cron.d/backup-paracarpinteros instalado."

echo "==> 5/6  Lanzando una ejecución de prueba…"
"${RUNNER}"

echo "==> 6/6  Contenido actual del bucket:"
rclone tree "${R2_REMOTE}:${R2_BUCKET}" 2>/dev/null || rclone ls "${R2_REMOTE}:${R2_BUCKET}"

echo
echo "✅ Listo. Backups diarios 03:15 → r2://${R2_BUCKET}/<host>/<fecha>/  (retención ${RETENTION_DAYS}d)"
echo "   Log: /var/log/backup-paracarpinteros.log   ·   Manual: ${RUNNER}"
