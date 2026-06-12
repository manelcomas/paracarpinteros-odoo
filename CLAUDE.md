# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Monorepo del negocio Paracarpinteros (carpintería CR, Gabriela Brenes Solano). Una sola persona (Manel) desarrolla esto y suele tener **dos sesiones Claude en paralelo**: una local en WSL y otra en el VPS por SSH. Cuidado con sincronización — leer la sección "Multi-session workflow" antes de actuar.

Contiene **un módulo Odoo + cuatro microservicios independientes**, deployados a **dos targets distintos**:

| Pieza | Lenguaje | Target de deploy | Cómo se despliega |
|---|---|---|---|
| `delivery_correos_cr/` | Python (Odoo addon) | Odoo.sh (paracarpinteros.odoo.com) | `git push origin main` → Odoo.sh reconstruye. Luego en Odoo: **Apps → Update list → Install**. |
| `correos-cr-bridge/` | Python (FastAPI) | VPS Contabo `66.94.99.220` | SSH al VPS, `git pull && docker compose up -d --build` en `/opt/paracarpinteros-odoo/correos-cr-bridge/` |
| `calculadora/` | Python (HTTP server + HTML SPA) | Local del usuario + opcionalmente VPS | `docker compose up -d` en `calculadora/` |
| `fe-signer/` | PHP (Apache + CRLibre) | VPS Contabo | `git pull && docker compose up -d --build` en `/opt/paracarpinteros-odoo/fe-signer/` |
| `whatsapp-bot/` | Python (FastAPI) | VPS Contabo, vive en `/opt/whatsapp-bot/` (fuera del clon del monorepo) | Ver "wa-bot deploy" abajo |

El panel web (`https://panel.paracarpinteros.com`) lo sirve nginx del host VPS desde `/var/www/html/`. El archivo principal `panel-envios.html` es un **symlink a `correos-cr-bridge/panel-envios.html`** del repo, así que editarlo local + `git pull` en VPS lo refresca. Resto de assets (backups `.bak-*`) son históricos.

## Critical context for safe edits

### 1. El módulo `delivery_correos_cr` NO está instalado en producción

Está aquí por historia y para staging, pero el Odoo Online actual usa **Studio fields** para la geografía CR de los partners:

| Campo del módulo (este repo) | Campo Studio real en producción |
|---|---|
| `res.partner.correos_cr_provincia_code` | `state_id` (estándar, valor tipo `[1110, "San José (CR)"]`) |
| `res.partner.correos_cr_canton_code` | `x_studio_canton_cr` (Many2one → `x_canton_cr`, valor `[3, "Desamparados"]`) |
| `res.partner.correos_cr_distrito_code` | `x_studio_distrito_cr` (Many2one → `x_distrito_cr`) |
| `res.partner.correos_cr_zip` | `zip` (estándar) |
| — | `x_studio_senas` (text con las señas exactas) |

**Por eso `correos-cr-bridge/app/odoo_client.py:read_partner()` lee los Studio fields**, no los del módulo. Cualquier referencia a `correos_cr_*` en el bridge contra Odoo Online va a fallar con `KeyError`. Verificá siempre con `fields_get` antes de añadir campos nuevos al `read`.

### 2. El bridge corre con `WORKER_AUTO=0` por defecto

El polling automático de pickings está desactivado en producción. La generación de guías Pymexpress se dispara **solo desde el panel** (`POST /api/picking/{id}/generar`). El worker auto (`processor._process_one_locked`) existe y funciona, pero no se ejecuta a menos que `WORKER_AUTO=1` en el `.env`.

### 3. Estado del bridge en SQLite, no en Odoo

`correos-cr-bridge/data/panel.sqlite` guarda:
- `line_prepared` — checkboxes "preparado" por línea de picking
- `envio_manual` — guías Tavo/Dual generadas fuera de Pymexpress
- `entrega_mano` — entregas que no pasaron por courier

El archivo **vive en un volumen Docker** (`./data:/app/data`). Antes del 25 de mayo 2026, no estaba en volumen y se perdía con cada `--build`. Si trabajás con esta DB, asegurate que `data/` exista en el host antes de subir el container.

### 4. `correos-cr-bridge` y `delivery_correos_cr` cada uno tiene su `correos_cr_client.py`

Son **dos clientes SOAP distintos para el mismo WS de Correos**:
- `correos-cr-bridge/app/correos_client.py` — usado por el bridge (microservicio)
- `delivery_correos_cr/models/correos_cr_client.py` — usado por el módulo Odoo (no instalado en prod)

Cambios en el WS hay que reflejarlos en ambos. El bridge es el que se usa en producción.

### 5. Correos CR está en modo `production`

El `.env` real del bridge tiene `CORREOS_ENV=production` (el `.env.example` muestra `test`). **Scripts locales que llamen a `correos_client.py` pegan a Pymexpress real**. Para pruebas, exportar `CORREOS_ENV=test` antes del comando. Las guías generadas no cuestan dinero hasta que se entregan físicamente, pero generan ruido en el sistema de Correos.

## Common commands

### Develop locally with Python scripts (`scripts/`)

```bash
# Los scripts leen automáticamente el .env raíz (vía scripts/_env.py)
python3 scripts/crear_producto_emf9030.py --image /ruta/foto.jpg

# Test rápido de conexión a Odoo
python3 -c "
import sys, os; sys.path.insert(0, 'scripts')
from _env import load_project_env; load_project_env()
import xmlrpc.client
c = xmlrpc.client.ServerProxy(os.environ['ODOO_URL']+'/xmlrpc/2/common', allow_none=True)
print('uid:', c.authenticate(os.environ['ODOO_DB'], os.environ['ODOO_USERNAME'], os.environ['ODOO_API_KEY'], {}))
"
```

### Bridge (`correos-cr-bridge/`)

```bash
# Local: el código no se ejecuta fuera de Docker porque requiere zeep/pydantic-settings.
# Para testear sintaxis solamente:
python3 -m py_compile correos-cr-bridge/app/*.py

# En el VPS:
cd /opt/paracarpinteros-odoo/correos-cr-bridge
docker compose up -d --build
docker compose logs -f bridge

# Probar endpoints (requieren X-API-Token o sesión panel):
curl -s https://panel.paracarpinteros.com/health
curl -s -H "X-API-Token: $TOKEN" https://panel.paracarpinteros.com/test-odoo | jq
curl -s -H "X-API-Token: $TOKEN" https://panel.paracarpinteros.com/test-correos | jq
```

### Calculadora (`calculadora/`)

```bash
cd calculadora
docker compose up -d
# Sirve en http://127.0.0.1:8001
```

Para regenerar el `data/pedidos.db` desde cero: borrar `calculadora/data/pedidos.db` (gitignorado).

### Odoo module (`delivery_correos_cr/`)

```bash
# No hay deploy manual. Odoo.sh detecta el push y reconstruye.
git push origin main   # requiere "si push" para destrabar el classifier

# Despues en Odoo Online:
# Apps → Update list → buscar "Correos CR" → Install/Upgrade
```

### fe-signer (`fe-signer/`)

```bash
# En el VPS:
cd /opt/paracarpinteros-odoo/fe-signer
docker compose up -d --build
docker compose logs -f fe-signer

# Test de firma:
P12_B64=$(base64 -w0 /ruta/certificado.p12)
XML_B64=$(base64 -w0 factura_sin_firma.xml)
curl -s -X POST https://panel.paracarpinteros.com/sign \
  -H "X-API-Key: $SIGNER_API_KEY" \
  -d "{\"xmlBase64\":\"$XML_B64\",\"p12Base64\":\"$P12_B64\",\"pin\":\"1234\",\"tipoDoc\":\"01\"}"
```

### Sub-modules del fe-signer

- `fe-signer/buzon-rx/` — Recepción de FE recibidas desde Hacienda vía Gmail polling. Estados: pending/accepted/rejected/partial. Maneja Mensaje Receptor (MR-05/06/07).
- `fe-signer/alibaba-rx/` — Mismo patrón pero para parsear pedidos de Alibaba que llegan por Gmail.

Ambos comparten el patrón: `oauth-start.php` → `oauth-callback.php` → `poll.php` (cron) → `list.php` (lectura por panel) → `mr.php` o equivalente (acción).

**OAuth Gmail — token y caducidad (gotcha):** el refresh token se guarda en **SQLite cifrado** (AES-256-GCM), no en `.env` ni `token.json`: tabla `oauth_tokens` (fila `id=1`, columna `refresh_token_enc`) de `buzon.db` (`BUZON_DB_PATH`, default `/var/www/html/buzon-rx/storage/buzon.db`, volumen Docker `./storage`). La clave de cifrado se deriva de `SIGNER_API_KEY` + `OAUTH_ENC_KEY_DERIVE` — si esos env cambian, el descifrado falla. **Si la app OAuth está en modo "Testing" (no publicada) en Google Cloud Console, Google revoca los refresh tokens cada 7 días** → `invalid_grant` / "Token has been expired or revoked" en "Revisar correo ahora". Pasó el 2026-05-29 (la app estaba sin publicar). Fix: publicar la app a **"In production"** (los scopes Gmail son *restricted*, así que sale aviso de "app no verificada" — para uso personal se sigue igual). Importante: **publicar no des-caduca el token ya emitido en Testing**; hay que re-autorizar (`oauth-start`) **después** de publicar para que el token nuevo nazca sin reloj de 7 días. Regenerar = abrir `https://panel.paracarpinteros.com/buzon-rx/oauth-start` (ó `/alibaba-rx/oauth-start`) logueado en `envios@` → el callback hace UPSERT en SQLite. `bx_get_access_token()` en `lib.php` es quien refresca/lanza la excepción.

### FE Converter (emisión de factura electrónica) — vive en Odoo, no en el repo

La **factura electrónica de emisión** (FE Hacienda CR) **NO la genera Odoo nativo**
(no hay módulo CR instalado: los campos de Hacienda en `account.move` están vacíos)
ni el repo. La arma un **conversor HTML/JS que vive DENTRO de Odoo** como
`ir.attachment` **37459** (`fe_converter_v22.html`, ~530KB), servido en la página
website **`/fe-converter`** (`website.page` 64 → `ir.ui.view` 7307, un wrapper con
un iframe que carga `/web/content/37459`). El panel (`panel.paracarpinteros.com`)
lo **embebe por iframe**. `fe-signer` **solo firma** (CRLibre `/sign`); el conversor
le manda el XML y reenvía a Hacienda.

- **Deploy:** NO es `git pull`. Se sube el HTML al attachment 37459 **por XML-RPC**
  (write `datas` base64). Copia versionada + instrucciones en
  [`fe-signer/fe-converter/`](fe-signer/fe-converter/README.md). Tras subir,
  Ctrl+Shift+R (el iframe cachea).
- **Gotcha tarifa IVA:** el `<TotalDesgloseImpuesto>` (resumen) debe llevar el
  **mismo `CodigoTarifaIVA` que las líneas**. UCR paga **2% (tarifa 03**, Ley 9635
  Art. 11.4); hardcodear `08` (13%) en el resumen → rechazo Hacienda **-488**.
- **Gotcha CAByS:** el código CAByS de cada línea sale del campo
  `product.product.x_cabys_code` en Odoo (el converter lo lee y lo puede escribir
  desde su modal de búsqueda). Si el código **no existe** en el catálogo BCCR,
  Hacienda rechaza con error **-400** ("no se encuentra en el Catálogo CAByS").
  Validar contra `https://api.hacienda.go.cr/fe/cabys?codigo=XXX` (vacío = no
  existe; también acepta `?q=texto`). El 2026-06-12 se saneó todo el catálogo:
  38 productos tenían 3 códigos inventados (`3199900990000`, `4299299990000`,
  `4449903000000`) que se remapearon a códigos reales. La respuesta de Hacienda
  de cada FE queda como adjunto `FE_<clave>_respuesta_hacienda.xml` en el chatter
  del `account.move` — ahí está el motivo exacto de un rechazo.

### wa-bot deploy

`whatsapp-bot/` **no se despliega con `git pull`** porque vive en `/opt/whatsapp-bot/` (fuera del clon del monorepo en `/opt/paracarpinteros-odoo/`). El layout en VPS quedó así por historia: nació antes que el monorepo y se mantiene ahí porque tiene su volumen `./data` (media + `conversations.db`) y `/var/backups/whatsapp-bot` mapeado fuera.

Flujo de despliegue actual (manual):

```bash
# Desde local, sincronizar el código al VPS sin tocar data/ ni .env:
rsync -av --delete \
  --exclude='.env' --exclude='.env.bak*' \
  --exclude='data/' --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='*.bak.*' --exclude='*.bak-*' \
  whatsapp-bot/ root@66.94.99.220:/opt/whatsapp-bot/

# En VPS:
cd /opt/whatsapp-bot && docker compose up -d --build
docker compose logs -f whatsapp-bot
```

Container: `wa-bot-paracarpinteros`, expone `127.0.0.1:8002`. Nginx del host lo expone públicamente en `https://wa.paracarpinteros.com/` (config `/etc/nginx/sites-enabled/wa-bot.conf`, proxy directo a `127.0.0.1:8002` sin auth a nivel nginx — el bot maneja su propio login con cookie).

### wa-bot: superficie expuesta

El bot sirve **dos audiencias** desde el mismo proceso FastAPI:

**Webhook público (Meta WhatsApp Cloud API)**:
- `GET /webhook` — verificación con `hub.verify_token` (= `WA_VERIFY_TOKEN` del `.env`)
- `POST /webhook` — entrega de mensajes entrantes

**Panel admin (login con `WA_PANEL_PASSWORD`)**: HTML servido en `GET /` (LOGIN_HTML si no hay cookie válida, PANEL_HTML si la hay). Endpoints `/api/*` requieren cookie de sesión vía `require_auth`:
- `POST /login`, `POST /logout`
- `GET /api/stats`, `GET /api/conversations`
- `GET|POST /api/conversation/{phone}` y sub-rutas (`/status`, `/reply`, `/reply-image`, `/wizard`, `/ask-balance`, `/quote-shipping`, `/set-carrier`, `/manual-quote`, `/confirm-order`, `/escalate`)
- `POST /api/conversation/create`
- `POST /api/partner/{partner_id}/update`, `GET /api/partner/{partner_id}/full`
- `GET /api/odoo/carriers`, `POST /api/odoo/carriers/{carrier_id}/quote`
- `GET /api/backups`, `GET /api/backups/{filename}`, `POST /api/backups/run-now`
- `GET|POST /api/bot/mode` — interruptor auto/manual del bot
- `GET /api/knowledge` — vuelca el bloque que se inyecta a Claude como contexto
- `GET /api/push/vapid-key`, `GET /api/push/status` — Web Push (VAPID)
- `GET /api/meta/health`

**PWA + estáticos**: `/manifest.webmanifest`, `/manifest.json`, `/sw.js`, `/pwa/{filename}`, `/apple-touch-icon*.png`, `/media/{filename}` (sirve archivos del volumen `data/media/`).

**Sin auth**: `GET /health`.

### wa-bot: procesamiento de mensajes entrantes (dedup + debounce + async)

El `POST /webhook` **no responde inline**: persiste el mensaje y devuelve `200` al instante; la respuesta de Claude ocurre en segundo plano. Esto evita que Meta reintente el webhook (su causa #1 de baneo es spam/duplicados) mientras Claude piensa. Piezas (todo en `main.py`):

- **Dedup por `wa_msg_id`** (`_is_duplicate_inbound` / `_mark_seen_inbound`): Meta reenvía duplicados; si un id entrante ya se vio (set en memoria, respaldo `SELECT ... direction='in'` en DB que sobrevive reinicios), se ignora. Sin esto el cliente recibía la respuesta dos veces.
- **Debounce / coalescing** (`_enqueue_inbound` → `_debounced_process` → `_respond_to_buffered`): varias burbujas seguidas del mismo teléfono ("hola" / "busco sierra" / "circular") se juntan en **una sola** respuesta. Espera `WA_DEBOUNCE_SECONDS` (default 3s) tras el último mensaje. **Invariante clave:** `_debounced_process` saca su task de `_debounce_tasks` **antes** del primer `await` de la respuesta, para que un mensaje nuevo nunca cancele una respuesta ya en curso (solo cancela el timer de debounce). El partner, escalado, fuera de horario y la llamada a Claude viven ahora en `_respond_to_buffered`, no en el webhook.
- **Read receipt + typing** (`mark_read_and_typing`): un POST a la Cloud API con `status=read` + `typing_indicator` marca el doble-check azul y muestra "escribiendo…" antes de pensar la respuesta (comodidad del cliente). Cosmético: falla en silencio.
- **Throttle fuera de horario** (`_ooh_throttled`): el aviso fijo nocturno no se reenvía más de una vez cada `OOH_THROTTLE_SECONDS` (default 6h) por conversación (clave `ooh_notice:{phone}` en `app_settings`). Antes se reenviaba idéntico en cada mensaje.

Env nuevas (opcionales, con default): `WA_DEBOUNCE_SECONDS`, `OOH_THROTTLE_SECONDS`. El buffer de debounce es **en memoria**: un restart del container pierde mensajes encolados aún no respondidos (ventana de pocos segundos).

### wa-bot: dos flags distintos en `conversations` — `escalated` vs `needs_attention`

No confundirlos (ambos cuelgan de la tabla `conversations`):

- **`escalated`** = bot APAGADO. Se activa al "Tomar conversación" en el panel (`/api/conversation/{phone}/escalate`) o por el modo global `escalate_all`. Mientras esté en 1, `_respond_to_buffered` hace `return` temprano y el bot no auto-responde.
- **`needs_attention`** (+ `attention_reason`) = **marca suave**, el bot SIGUE respondiendo. La pone el propio bot vía la tool **`pasar_a_humano`** (en `CLAUDE_TOOLS`) cuando hay un handoff real (sin info, asesoría técnica, reclamo, cliente pide humano). NO se pone con la frase de rutina "un compañero le confirma" (envíos/pagos), por eso es tool explícita y no detección por texto. En el panel resalta la fila en naranja + 🙋 + filtro/stat "Atención". Se limpia sola al **abrir** la conversación (`get_conversation` hace `needs_attention=0` junto a `unread=0`). El handler de la tool guarda un marcador interno `🙋 Necesita atención: …` con `_save_outbound` (no se envía al cliente, igual que `💰 PAGO`/`👤 Cliente`).

### wa-bot: respuestas por voz (TTS)

El bot contesta por **nota de voz** cuando el cliente le escribe **por audio** (espejo de modalidad). Piezas (en `main.py`):

- `tts_speak(text)` — POST a OpenAI `/v1/audio/speech`, modelo `gpt-4o-mini-tts`, `response_format="opus"`. **Clave:** WhatsApp solo muestra una respuesta como nota de voz (onda + play) si es **OGG/Opus**; otro formato sale como adjunto. OpenAI devuelve ogg/opus nativo.
- Trigger en `_respond_to_buffered`: `client_sent_voice = any(it["is_voice"] …)` (el flag `is_voice` se setea en el item encolado cuando `mtype=="audio"`) **Y** `_reply_is_voice_safe(reply)`. Esto último cae a texto si la respuesta lleva **₡, links, códigos (`SM-5007`, `S0####`), listas (≥2 saltos) o >600 chars**, porque eso suena mal hablado. Si TTS o la subida fallan, cae a texto.
- Reusa `upload_media_to_meta(..., mime="audio/ogg")` (ahora acepta mime) + `send_wa_audio_by_id` (type `audio`). El audio se guarda en `data/media/out_audio_*.ogg` y en la DB con `media_path`; el panel **ya** lo reproduce (`<audio>` para cualquier `media_path` de audio, in/out).
- Costo: `gpt-4o-mini-tts` ≈ **$0.015/min** (~$0.007 por respuesta corta), reusa la cuenta OpenAI del Whisper (`OPENAI_API_KEY`).
- Env (opcionales): `WA_VOICE_REPLIES` (default `1`; `0` lo apaga), `TTS_MODEL` (default `gpt-4o-mini-tts`), `TTS_VOICE` (default `shimmer`). El audio **entrante** ya se transcribía con Whisper (`transcribe_audio`).

### wa-bot: la "verdad" de cara al cliente vive en SQLite, no en el código

Los datos oficiales que el bot da a clientes (números de WhatsApp, horarios, ubicación, métodos de pago, envíos) **NO** están en el `SYSTEM_PROMPT` de `main.py` sino en la tabla **`bot_knowledge`** de `data/conversations.db` (volumen Docker). `_knowledge_block()` la lee **fresca en cada mensaje** y la concatena al system prompt como bloque "INFORMACIÓN OFICIAL DE LA EMPRESA". El bloque está marcado como verdad absoluta en el prompt.

**Gotcha:** el seed de `bot_knowledge` en `main.py` (`_init_db`) **solo se inserta si la tabla está vacía** (`COUNT(*)==0`). En producción la tabla ya tiene filas (editables por el panel en `/api/knowledge`), así que **editar el seed en el código NO cambia nada en prod** — hay que hacer `UPDATE` sobre la DB viva:

```bash
ssh root@66.94.99.220 'docker exec wa-bot-paracarpinteros python3 -c "
import sqlite3, time
c=sqlite3.connect(\"/opt/whatsapp-bot/data/conversations.db\")
c.execute(\"UPDATE bot_knowledge SET content=?, updated_at=? WHERE id=?\", (NUEVO, int(time.time()), ID))
c.commit()"'
```

Como se lee fresca por mensaje, el cambio en la DB **es inmediato sin rebuild**. Editá igual el seed del código para mantener coherencia en instalaciones nuevas. El `SYSTEM_PROMPT` de `main.py` sí gobierna el comportamiento/tono (eso sí requiere rsync+rebuild). Pasó el 2026-06-04: el bot solo conocía un número (8606-9717) y negaba el segundo (6104-3421, su propia línea); el fix fue `UPDATE` a la fila `ubicacion` + ajuste del pie del prompt.

**"Enseñar al bot" desde el panel:** el panel tiene un botón (icono birrete) junto a la respuesta manual en el `chat-foot`. Abre un modal (título + dato) que hace `POST /api/knowledge` con `category="aprendido"`, `active=1` → inserta una fila en `bot_knowledge`, así que el dato queda vivo al instante para todas las conversaciones futuras (no retroactivo a la actual) y es editable/borrable en el editor de conocimiento. El canal es solo la respuesta manual (lo enseña el equipo, no el cliente) para evitar envenenamiento. El backend (`POST/PUT/DELETE /api/knowledge`) ya existía; lo nuevo es solo UI en `static/panel.{html,js}`, desplegable por static hot-reload (rsync + `docker cp`, sin rebuild).

### SEO / Google Merchant (feed + schema)

- **Feed Merchant Center**: `scripts/generate_feed.py` (solo lee Odoo por XML-RPC,
  stdlib puro) genera RSS 2.0 Google Shopping con los `product.template`
  publicados (`g:id` = `default_code`; omite sin ref o sin imagen). Corre **en el
  VPS** vía `/etc/cron.d/feed-google` (diario 3am hora del VPS) cargando las
  credenciales del `.env` del bridge, y escribe directo a
  `/var/www/html/feed-google.xml` → `https://panel.paracarpinteros.com/feed-google.xml`.
  La copia del script en el VPS está en `/opt/paracarpinteros-odoo/scripts/`
  (subida por scp; cuando se commitee y haga `git pull` quedará versionada).
- **Schema Product**: Odoo 19 genera el JSON-LD de las fichas **en Python, no en
  QWeb** (no hay vista que tocar). Emite todo menos `sku`; lo añade el snippet
  `pc-sku-jsonld` del `custom_code_footer` del website 3 leyendo el `Ref:` del DOM
  (script `scripts/inject_sku_schema.py`, idempotente, backup en `scripts/_backups/`).
- **Search Console**: `scripts/add_gsc_txt.py TOKEN [--apply]` crea el TXT de
  verificación en Cloudflare (zona paracarpinteros.com). El `.env` no tiene
  `CF_API_TOKEN`; usa `CLOUDFLARE_EMAIL` + `CLOUDFLARE_GLOBAL_API_KEY`.

## The `.env` baúl pattern

El proyecto tiene **un `.env` raíz** que centraliza credenciales para los scripts en `scripts/`:

- `/.env` — baúl (chmod 600, gitignored). Estructura en `.env.example`.
- `/scripts/_env.py` — cargador sin deps externas. Cualquier script en `scripts/` hace `from _env import load_project_env; load_project_env()`.

Cada servicio Docker tiene **su propio `.env`** dentro de su carpeta (`correos-cr-bridge/.env`, `fe-signer/.env`), porque vive dentro de su container y se monta con `env_file:` en su `docker-compose.yml`. No se intenta unificar — el bridge `.env` puede tener configuración que el baúl raíz no necesita y viceversa.

## Multi-session workflow

Manel suele tener una sesión Claude local (este repo) y otra Claude por SSH en el VPS al mismo tiempo. **Riesgos a evitar:**

1. **Editar código directamente en el VPS** — pasó histórico que el VPS tenía `/ocr/tavo`, alibaba-rx y otras cosas sin commitear. Causa drift y pérdida de cambios. **Regla: el VPS solo hace `git pull`**, nunca edita.
2. **Pushear cambios mientras la otra sesión edita** — antes de `git push`, preguntar al usuario si la otra sesión ha hecho cambios en el VPS sin commitear.
3. **Coordinar vía memoria** — el directorio `~/.claude/projects/-home-manel-proyectos-paracarpinteros-odoo/memory/` tiene `MEMORY.md` y notas que ambas sesiones pueden leer. Apuntar ahí cualquier hallazgo crítico (p.ej., qué está en producción y no en git).
4. **Push a `main` requiere confirmación explícita** del usuario con la frase "si push" — el clasificador del harness lo exige.

## Deployment targets summary

| Servicio | Donde corre | Cómo se entera de un cambio | Estado tras push |
|---|---|---|---|
| `delivery_correos_cr` | Odoo.sh staging/prod | Auto al push a main | Hay que ir a UI Odoo: Apps → Update list |
| `correos-cr-bridge` | VPS Contabo Docker | NO automatico | SSH + `git pull && docker compose up -d --build` |
| `fe-signer` | VPS Contabo Docker | NO automatico | SSH + `git pull && docker compose up -d --build` |
| `whatsapp-bot` | VPS Contabo Docker (`/opt/whatsapp-bot/`, fuera del monorepo) | NO automatico | `rsync` desde local → SSH + `docker compose up -d --build`. Ver "wa-bot deploy" |
| `calculadora` | Local del usuario | NO automatico (es local) | `docker compose up -d` |

**No hay CI/CD que despliegue al VPS**. El único workflow GitHub Actions es `.github/workflows/uptime.yml` que solo monitorea `https://panel.paracarpinteros.com/health` cada 5 minutos y abre un issue si cae.

## Conventions

- **Lengua**: comentarios, mensajes de commit y strings de UI en **español**. Identificadores de código en español (no en inglés).
- **Commits**: una sola línea de scope + descripción, sin prefijos `feat:`/`fix:`. Ejemplos: `correos-cr-bridge: lock por picking`, `fe-signer/buzon-rx: emitir headers CORS`. Sin "Co-Authored-By: Claude" (el classifier lo rechaza).
- **No PRs**: solo dev, commits directos a `main`. Las branches solo se usan para Odoo.sh staging.
- **Permisos del `.env`**: siempre `chmod 600`. El gitignore ya cubre `.env`, `*.bak-*`.
