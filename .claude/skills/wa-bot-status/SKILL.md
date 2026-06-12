---
name: wa-bot-status
description: Triaje rápido del wa-bot (wa.paracarpinteros.com) por API y SSH — health, stats, modo bot, backups, logs del container y DB de conversaciones. Usar cuando haya que revisar si el bot está vivo, diagnosticar un fallo, o ver el estado general sin abrir el navegador.
---

# Triaje del wa-bot por API + SSH

El bot es FastAPI en el VPS Contabo (`root@66.94.99.220`), container `wa-bot-paracarpinteros` en `/opt/whatsapp-bot/`, expuesto en `https://wa.paracarpinteros.com/`. Código local de referencia en `whatsapp-bot/main.py` (¡el VPS puede tener otra versión — el deploy es rsync manual!).

## 1. Health sin auth

```bash
curl -s https://wa.paracarpinteros.com/health
```

## 2. Login y endpoints autenticados

La contraseña es `WA_PANEL_PASSWORD` del `.env` raíz del repo. El login devuelve cookie `session`:

```bash
PASS=$(grep '^WA_PANEL_PASSWORD=' /home/manel/proyectos/paracarpinteros-odoo/.env | cut -d= -f2-)
JAR=$(mktemp)
curl -s -c "$JAR" -X POST https://wa.paracarpinteros.com/login -d "password=$PASS" -o /dev/null
# Con la cookie:
curl -s -b "$JAR" https://wa.paracarpinteros.com/api/stats | jq
curl -s -b "$JAR" https://wa.paracarpinteros.com/api/meta/health | jq      # token Meta / Cloud API
curl -s -b "$JAR" https://wa.paracarpinteros.com/api/bot/mode | jq         # modos: normal / conservative / escalate_all
curl -s -b "$JAR" https://wa.paracarpinteros.com/api/push/status | jq     # Web Push VAPID
curl -s -b "$JAR" https://wa.paracarpinteros.com/api/backups | jq         # backups recientes
curl -s -b "$JAR" 'https://wa.paracarpinteros.com/api/conversations' | jq '.[:5]'
curl -s -b "$JAR" https://wa.paracarpinteros.com/api/knowledge | jq       # bot_knowledge vivo
```

## 3. Container y logs (SSH)

```bash
ssh root@66.94.99.220 'docker ps --filter name=wa-bot-paracarpinteros; cd /opt/whatsapp-bot && docker compose logs --tail=100 whatsapp-bot'
```

Buscar en logs: errores de Anthropic/OpenAI (créditos), `[session check err]`, fallos del webhook de Meta, excepciones en `_respond_to_buffered`.

## 4. DB de conversaciones (solo lectura)

```bash
ssh root@66.94.99.220 'docker exec wa-bot-paracarpinteros python3 -c "
import sqlite3
c = sqlite3.connect(\"/opt/whatsapp-bot/data/conversations.db\")
print(\"convos:\", c.execute(\"SELECT COUNT(*) FROM conversations\").fetchone())
print(\"escaladas:\", c.execute(\"SELECT COUNT(*) FROM conversations WHERE escalated=1\").fetchone())
print(\"atencion:\", c.execute(\"SELECT COUNT(*) FROM conversations WHERE needs_attention=1\").fetchone())
print(\"ult msg:\", c.execute(\"SELECT phone, direction, substr(body,1,60), datetime(created_at,\\\"unixepoch\\\") FROM messages ORDER BY id DESC LIMIT 5\").fetchall())
"'
```

(Si el esquema difiere, listar tablas primero: `SELECT name FROM sqlite_master`.)

## Gotchas

- `escalated=1` = bot apagado para esa conversación; `needs_attention=1` = marca suave, el bot sigue respondiendo. No confundirlos.
- La "verdad" de cara al cliente (horarios, números, pagos) vive en la tabla `bot_knowledge`, no en el código — editar el seed de `main.py` NO cambia producción.
- Antes de depurar comportamiento, comparar versión local vs VPS: el deploy es rsync manual, puede haber drift.
- No escribir en la DB ni reiniciar el container sin que Manel lo pida — un restart pierde el buffer de debounce en memoria (mensajes encolados sin responder).
