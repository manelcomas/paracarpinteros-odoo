#!/usr/bin/env bash
# deploy.sh — Despliegue seguro de los servicios del VPS desde main.
#
# Uso (en el VPS, dentro de /opt/paracarpinteros-odoo):
#   bash scripts/deploy.sh                  # pull + rebuild solo de los servicios cambiados
#   bash scripts/deploy.sh bridge           # forzar rebuild solo del bridge
#   bash scripts/deploy.sh fe-signer        # forzar rebuild solo del fe-signer
#   bash scripts/deploy.sh all              # forzar rebuild de todos
#
# Qué hace:
#   1. Aborta si hay cambios sin commitear en el VPS (evita perder código
#      como pasó con /ocr/tavo y alibaba-rx en mayo 2026).
#   2. git pull --ff-only — sin merge commits, sin sorpresas.
#   3. Detecta qué servicios cambiaron en los commits nuevos.
#   4. Rebuild + restart solo de esos servicios (o de los pedidos por arg).
#   5. Espera al healthcheck y muestra las últimas líneas del log.
#
# Lo que NO hace:
#   - No commitea nada (eso se hace en local y se pushea).
#   - No toca el código del módulo Odoo (eso lo despliega Odoo.sh solo).
#   - No reinicia servicios que no cambiaron (evita downtime innecesario).

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

c_red()    { printf '\033[31m%s\033[0m\n' "$*" >&2; }
c_green()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
c_bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

die() {
    c_red "✗ $*"
    exit 1
}

# Lista de servicios desplegables y sus paths (relativos al repo root).
declare -A SERVICES=(
    [bridge]='correos-cr-bridge'
    [fe-signer]='fe-signer'
    [calculadora]='calculadora'
)

# ─────────────────────────────────────────────────────────────────────
# 1. Comprobar que no hay cambios sin commitear
# ─────────────────────────────────────────────────────────────────────

c_bold "[1/5] Comprobando estado del working tree..."

DIRTY="$(git status --porcelain)"
if [[ -n "$DIRTY" ]]; then
    c_red "✗ Hay cambios sin commitear en el VPS:"
    echo "$DIRTY" >&2
    echo "" >&2
    c_red "ABORTO. El VPS no debería editar código. Si hay cambios urgentes:"
    c_red "  1. Anotar los cambios para llevártelos a local."
    c_red "  2. Hacer 'git stash' para guardarlos temporalmente."
    c_red "  3. Replicarlos en local + commitear + pushear."
    c_red "  4. Volver al VPS y correr este script de nuevo."
    exit 1
fi

c_green "✓ Working tree limpio."

# ─────────────────────────────────────────────────────────────────────
# 2. Pull fast-forward
# ─────────────────────────────────────────────────────────────────────

c_bold "[2/5] Bajando cambios de origin/main..."

OLD_SHA="$(git rev-parse HEAD)"
git fetch --quiet origin main
git merge --ff-only origin/main || die "No es fast-forward. Algo divergió. Revisa manualmente."
NEW_SHA="$(git rev-parse HEAD)"

if [[ "$OLD_SHA" == "$NEW_SHA" ]]; then
    c_yellow "= No hay commits nuevos. Working tree ya está al día."
    if [[ $# -eq 0 ]]; then
        echo "Si querés forzar rebuild de algún servicio:"
        echo "  bash scripts/deploy.sh <bridge|fe-signer|calculadora|all>"
        exit 0
    fi
else
    COMMITS_NEW="$(git log --oneline "$OLD_SHA..$NEW_SHA" | wc -l)"
    c_green "✓ $COMMITS_NEW commit(s) nuevo(s):"
    git log --oneline "$OLD_SHA..$NEW_SHA" | sed 's/^/    /'
fi

# ─────────────────────────────────────────────────────────────────────
# 3. Decidir qué servicios reconstruir
# ─────────────────────────────────────────────────────────────────────

c_bold "[3/5] Decidiendo qué servicios redeployar..."

declare -a TO_DEPLOY=()

if [[ $# -gt 0 ]]; then
    # Argumentos explícitos del usuario
    if [[ "$1" == "all" ]]; then
        TO_DEPLOY=("${!SERVICES[@]}")
    else
        for arg in "$@"; do
            [[ -n "${SERVICES[$arg]:-}" ]] || die "Servicio desconocido: '$arg'. Válidos: ${!SERVICES[*]} all"
            TO_DEPLOY+=("$arg")
        done
    fi
    c_yellow "→ Redeploy forzado: ${TO_DEPLOY[*]}"
elif [[ "$OLD_SHA" != "$NEW_SHA" ]]; then
    # Auto-detección por archivos cambiados
    CHANGED="$(git diff --name-only "$OLD_SHA..$NEW_SHA")"
    for name in "${!SERVICES[@]}"; do
        path="${SERVICES[$name]}"
        if echo "$CHANGED" | grep -q "^$path/"; then
            TO_DEPLOY+=("$name")
        fi
    done
    if [[ ${#TO_DEPLOY[@]} -eq 0 ]]; then
        c_yellow "= Los commits no tocan ningún servicio docker. Nada que rebuildear."
        exit 0
    fi
    c_green "✓ Servicios con cambios: ${TO_DEPLOY[*]}"
fi

# ─────────────────────────────────────────────────────────────────────
# 4. Rebuild + restart
# ─────────────────────────────────────────────────────────────────────

c_bold "[4/5] Reconstruyendo containers..."

for name in "${TO_DEPLOY[@]}"; do
    path="${SERVICES[$name]}"
    echo ""
    c_bold "  ── $name ($path) ──"

    # Asegurar directorios de volúmenes (defensivo: bridge necesita data/, etc.)
    mkdir -p "$path/data" "$path/logs" 2>/dev/null || true

    (cd "$path" && docker compose up -d --build) || die "Fallo al levantar $name"
done

# ─────────────────────────────────────────────────────────────────────
# 5. Verificación + logs
# ─────────────────────────────────────────────────────────────────────

c_bold "[5/5] Verificando que arrancaron..."

sleep 4

for name in "${TO_DEPLOY[@]}"; do
    path="${SERVICES[$name]}"
    echo ""
    c_bold "  ── $name ──"
    (cd "$path" && docker compose ps)
    echo ""
    (cd "$path" && docker compose logs --tail=15 2>&1) || true
done

echo ""
c_green "✓ Deploy completado."
c_yellow "  Para ver logs en streaming:  cd <servicio> && docker compose logs -f"
