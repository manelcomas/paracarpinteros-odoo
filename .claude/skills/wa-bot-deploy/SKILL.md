---
name: wa-bot-deploy
description: Desplegar cambios del wa-bot al VPS — rsync + rebuild para main.py, hot-reload sin downtime para static/, con verificación post-deploy. Usar cuando haya que subir cambios del bot de WhatsApp o verificar que un cambio llegó a producción.
---

# Deploy del wa-bot

`whatsapp-bot/` vive en el VPS en `/opt/whatsapp-bot/` (FUERA del clon del monorepo) — **no se despliega con git pull, solo rsync**.

## Caso A: cambios solo en `static/` (panel.html/js/css) — hot-reload sin downtime

```bash
rsync -av whatsapp-bot/static/ root@66.94.99.220:/opt/whatsapp-bot/static/
ssh root@66.94.99.220 'docker cp /opt/whatsapp-bot/static/. wa-bot-paracarpinteros:/app/static/'
```

Sin rebuild ni restart. Verificar con Ctrl+Shift+R (PWA cachea vía service worker).

## Caso B: cambios en `main.py` u otros .py — rsync + rebuild

```bash
rsync -av --delete \
  --exclude='.env' --exclude='.env.bak*' \
  --exclude='data/' --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='*.bak.*' --exclude='*.bak-*' \
  whatsapp-bot/ root@66.94.99.220:/opt/whatsapp-bot/

ssh root@66.94.99.220 'cd /opt/whatsapp-bot && docker compose up -d --build && docker compose logs --tail=30 whatsapp-bot'
```

**Aviso**: el rebuild reinicia el container → se pierde el buffer de debounce en memoria (mensajes entrantes de los últimos ~3s sin responder). Preferir momentos de poco tráfico; si hay duda, avisar a Manel antes.

## Verificación post-deploy (siempre)

```bash
curl -s https://wa.paracarpinteros.com/health
# Confirmar que el cambio llegó (grep de un marcador del cambio dentro del container):
ssh root@66.94.99.220 'docker exec wa-bot-paracarpinteros grep -c "MARCADOR_DEL_CAMBIO" /app/main.py'
```

No hay CI/CD: si el comportamiento en prod no coincide con el código local, lo primero es comparar versiones (local vs `/opt/whatsapp-bot/` vs dentro del container).

## Gotchas

- **Nunca** tocar `data/` ni `.env` del VPS con rsync (los excludes del comando lo cubren — no quitarlos).
- Editar `bot_knowledge` (datos oficiales del negocio) NO requiere deploy: es `UPDATE` sobre la DB viva o el editor del panel; el seed del código solo aplica a instalaciones nuevas.
- Si la otra sesión Claude (VPS) está trabajando, coordinar antes de un rebuild — ver "Multi-session workflow" en CLAUDE.md.
