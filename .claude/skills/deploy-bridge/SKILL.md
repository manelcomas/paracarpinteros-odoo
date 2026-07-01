---
name: deploy-bridge
description: Desplegar el correos-cr-bridge al VPS — push local + git pull y rebuild Docker en /opt/paracarpinteros-odoo/correos-cr-bridge, con verificación /health/deep (detecta key Odoo expirada). Usar tras cambiar código del bridge (panel-envios, generación de guías Pymexpress, endpoints).
---

# Deploy del correos-cr-bridge

El bridge (FastAPI) corre en el VPS Contabo en `/opt/paracarpinteros-odoo/correos-cr-bridge/` DENTRO del clon del monorepo. Se entera de un cambio **solo por `git pull`** — no hay CI/CD.

## Flujo

1. **Commit local** (español, sin prefijos, sin Co-Authored-By — ver Conventions en CLAUDE.md). El panel `panel-envios.html` es un symlink a `correos-cr-bridge/panel-envios.html`, así que un cambio de panel entra por el mismo git pull.

2. **Push a main** — requiere confirmación explícita del usuario con la frase **"si push"** (lo exige el classifier). Antes de pushear, preguntar si la sesión Claude del VPS tiene cambios sin commitear (ver "Multi-session workflow").

3. **En el VPS** (SSH):
   ```bash
   ssh root@66.94.99.220 'cd /opt/paracarpinteros-odoo/correos-cr-bridge && git pull && docker compose up -d --build && docker compose logs --tail=30 bridge'
   ```

## Verificación post-deploy (SIEMPRE)

```bash
curl -s https://panel.paracarpinteros.com/health          # plano: 200 aunque Odoo esté caído
curl -s https://panel.paracarpinteros.com/health/deep      # fuerza execute_kw real → detecta key Odoo expirada
```

`/health` sigue en 200 aunque Odoo muera (authenticate cachea el uid). **Usar `/health/deep`**: si sale `odoo: auth_failed` o 500, la API key del bridge expiró → poner la key del baúl en `correos-cr-bridge/.env` del VPS + restart (mismo fallo que tumbó bridge y wa-bot el 2026-06-13).

## Gotchas

- **`data/` debe existir en el host** antes del `--build` (SQLite `panel.sqlite` vive en el volumen `./data:/app/data`; antes del 25-may-2026 se perdía con cada build).
- El bridge corre con `WORKER_AUTO=0`: el polling automático está apagado, las guías se generan solo desde el panel.
- El VPS **solo hace git pull, nunca edita** — si en prod ves algo que no está en git, es drift; commitéalo desde local, no lo edites en el VPS.
