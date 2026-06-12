---
name: wa-panel-review
description: Revisión visual/funcional del panel admin de wa.paracarpinteros.com con navegador (Playwright MCP) — login, recorrido de pestañas, errores de consola, screenshots. Usar cuando haya que verificar la UI del panel del bot de WhatsApp, reproducir un bug visual, o validar un cambio de static/ tras deploy.
---

# Revisión del panel wa.paracarpinteros.com con navegador

El panel es una SPA servida por el propio bot FastAPI: `GET /` devuelve `LOGIN_HTML` sin cookie válida o `PANEL_HTML` con ella. Fuentes locales: `whatsapp-bot/static/panel.html`, `panel.js`, `panel.css`, `login.html` (el VPS puede ir por detrás — deploy manual).

## Flujo de revisión

1. Cargar las tools de navegador con ToolSearch (`select:mcp__playwright__browser_navigate,mcp__playwright__browser_snapshot,...`). Si Playwright MCP no responde en WSL, usar chrome-devtools MCP como alternativa.
2. Navegar a `https://wa.paracarpinteros.com/` → aparece el login.
3. Login: un solo campo password. La contraseña es `WA_PANEL_PASSWORD` del `.env` raíz del repo:
   ```bash
   grep '^WA_PANEL_PASSWORD=' /home/manel/proyectos/paracarpinteros-odoo/.env | cut -d= -f2-
   ```
   Rellenar el campo y enviar (POST /login, deja cookie `session`).
4. Recorrer y verificar:
   - **Lista de conversaciones**: filas cargan, filtros (Atención 🙋, no leídas), stats de cabecera (`/api/stats`).
   - **Una conversación**: abrirla limpia `unread` y `needs_attention`; verificar render de audios (`<audio>` para media_path), imágenes, marcadores internos (`💰 PAGO`, `👤 Cliente`, `🙋 Necesita atención`).
   - **Respuesta manual + botón birrete** (enseñar al bot → modal → POST /api/knowledge).
   - **Interruptor modo bot** (auto/manual, `/api/bot/mode`).
   - **Editor de conocimiento** (`/api/knowledge` CRUD).
   - **Backups** (lista + run-now si se pide).
5. Revisar la consola del navegador (errores JS) y peticiones de red fallidas (4xx/5xx en `/api/*`).
6. Screenshot de lo relevante para el reporte.

## Gotchas

- **No enviar mensajes reales a clientes** desde el panel durante una revisión (botones de respuesta/reply-image apuntan a la Cloud API real). Probar envíos solo contra el número de Manel y solo si lo pide.
- Si un bug de UI se confirma: el fix en `static/` se despliega con hot-reload sin rebuild (ver skill `wa-bot-deploy`), pero los cambios en `main.py` requieren rsync + rebuild.
- Cache: tras un deploy de static, recargar con Ctrl+Shift+R; el panel es PWA (service worker `sw.js`) y puede servir versión vieja.
- Comparar siempre versión desplegada vs local antes de "arreglar" algo que en realidad ya está arreglado en local y sin desplegar.
