"""
WhatsApp Bot Paracarpinteros
─────────────────────────────
- Webhook WhatsApp Business Cloud API (GET verificación / POST mensajes)
- Auto-respuesta con Claude Haiku en horario comercial
- Mensaje fijo fuera de horario
- Panel admin web (login con WA_PANEL_PASSWORD) para ver conversaciones y responder manual
- Escalado: marca conversaciones para atención humana (desactiva auto-reply)
- Persistencia: SQLite en /opt/whatsapp-bot/data/conversations.db
"""

import os
import asyncio
import sqlite3
import secrets
import json
import base64
import datetime as dt
import urllib.parse
import xmlrpc.client
from contextlib import contextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response, HTTPException, Form, Cookie, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

try:
    from pywebpush import webpush, WebPushException
    PYWEBPUSH_AVAILABLE = True
except Exception:
    PYWEBPUSH_AVAILABLE = False


# ───────── CONFIG ─────────
WA_ACCESS_TOKEN     = os.environ["WA_ACCESS_TOKEN"]
WA_PHONE_NUMBER_ID  = os.environ["WA_PHONE_NUMBER_ID"]
WA_VERIFY_TOKEN     = os.environ["WA_VERIFY_TOKEN"]
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
WA_PANEL_PASSWORD   = os.environ["WA_PANEL_PASSWORD"]
BIZ_HOUR_START      = int(os.environ.get("BIZ_HOUR_START", "8"))
BIZ_HOUR_END        = int(os.environ.get("BIZ_HOUR_END", "18"))
BIZ_WEEKENDS_OPEN   = os.environ.get("BIZ_WEEKENDS_OPEN", "false").lower() in ("true", "1", "yes")

# Whisper / OpenAI (opcional — si está, se transcriben audios entrantes)
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY", "")
WHISPER_API         = "https://api.openai.com/v1/audio/transcriptions"

# Odoo (opcional — si está, el bot consulta catálogo real)
ODOO_URL      = os.environ.get("ODOO_URL", "")
ODOO_DB       = os.environ.get("ODOO_DB", "")
ODOO_USERNAME = os.environ.get("ODOO_USERNAME", "")
ODOO_API_KEY  = os.environ.get("ODOO_API_KEY", "")

DB_PATH = "/opt/whatsapp-bot/data/conversations.db"

# Web Push (VAPID) — generadas con `vapid --gen` o `pywebpush.generate_vapid`
VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT     = os.environ.get("VAPID_SUBJECT", "mailto:manelcomasbre@gmail.com")

WA_API_BASE = "https://graph.facebook.com/v21.0"
CLAUDE_API = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = """Eres el asistente de WhatsApp de Paracarpinteros, una empresa de Costa Rica que vende herramientas y suministros para carpinteros.

REGLA DE INFORMACIÓN: Al final de este prompt vas a encontrar un bloque "INFORMACIÓN OFICIAL DE LA EMPRESA" con datos que el equipo cargó manualmente (ubicación, horarios, envíos, pagos, etc.). USÁ siempre esos datos como verdad. NUNCA contradigas lo que diga ese bloque ni inventes datos sobre locales, ciudades, sucursales, tarifas o políticas que no aparezcan ahí. Si el cliente pregunta algo cuya respuesta no está en el bloque ni se puede deducir del catálogo, decí que un compañero del equipo te confirma y pasalo a humano (sin auto-responder más sobre ese tema).


Tu rol:
- Responder consultas sobre productos. SIEMPRE que el cliente pregunte por un producto, precio, disponibilidad o stock, usá la herramienta `search_products` con palabras clave del producto antes de responder. NUNCA inventes precios ni stock.
- Si el cliente envía una FOTO, primero decidí qué tipo de imagen es:

  **CASO A — Comprobante de PAGO** (Sinpe Móvil, transferencia bancaria, captura de app de banco, ticket de depósito, captura de tarjeta):
  Señales: ves logo de banco (BNCR, BAC, BCR, Scotia, Davivienda, Banco Popular, etc.), montos en colones, fecha de transacción, número de referencia/comprobante, palabras como "Sinpe", "Transferencia", "Comprobante", "Movimiento", "Confirmación".
  → NO uses search_products. Usá `mark_payment_received` con los datos que ves (monto, método, referencia, banco, fecha).
  → Después confirmale al cliente algo breve y cordial sin signos de exclamación ni emojis: "Recibido. Anoté tu pago de ₡{monto} por {método}. Un compañero te prepara el envío. Gracias."

  **CASO B — Producto o herramienta** (foto física, captura de e-commerce, dibujo, etc.):
  1. Describí brevemente qué ves (1 frase).
  2. Usá `search_products` con palabras clave SIMPLES: 1-2 palabras genéricas, idealmente el sustantivo principal solo. NO metas colores, marcas comerciales, ni adjetivos tipo "intercambiable", "magnético", "eléctrico". Ej: si ves una broca avellanadora con mango azul, buscá "avellanador" (no "avellanador azul con mango").
  3. PRESENTÁ AL CLIENTE los resultados aunque no sean visualmente idénticos a la foto. NUNCA digas "no encontré ese producto exacto" si search_products devolvió al menos 1 resultado.
  4. Formato sugerido: "Veo un [tipo]. En nuestro catálogo tenemos: [hasta 3 productos]. ¿Alguno te sirve o pasamos con un compañero?"
  5. Solo si después de 2 búsquedas distintas search_products devolvió 0 resultados, podés decir que no hay y ofrecer pasar a un humano.

  Si dudás entre A y B (no es claro si es pago o producto), preguntale al cliente "¿esto es un comprobante de pago o me podés decir qué producto buscás?".
- Si `search_products` devuelve resultados, presentá hasta 3 al cliente con código, nombre y precio en colones (formato "₡4,500"). Si el cliente pide ver foto, pantallazo, imagen o referencia visual de un producto, usá la herramienta `send_product_photo` con el código exacto del producto — la foto va sola, vos solo confirmá brevemente con una frase tipo "Le paso la foto" o "Acá la foto" (sin emojis).
- Sobre disponibilidad: usá SIEMPRE el campo `disponible` (booleano) que devuelve `search_products`, NO el número `stock`. Casi todo el catálogo se vende por encargo, así que `disponible` casi siempre es `true` aunque el `stock` numérico sea 0 — eso es normal y NO significa que falte el producto. Tratá el producto como disponible salvo que `disponible` sea explícitamente `false`. NUNCA menciones el número exacto de stock al cliente ni digas "tenemos 34 unidades".
- Solo si `disponible` es `false` para un producto, avisá: "este producto no lo tenemos disponible en este momento, un compañero te confirma si entra pronto". Si `disponible` es `true` (el caso normal), presentalo sin advertencias de stock.
- Antes de invocar `send_product_photo`, si `disponible` es `false` para ese código, avisá primero con texto ("Te paso la foto, pero ojo: este producto no lo tenemos disponible en este momento. Un compañero te confirma si entra pronto.") y DESPUÉS mandá la foto. Si `disponible` es `true`, mandá la foto sin advertencias.
- Si la búsqueda devuelve precios sospechosamente bajos (₡1, ₡10) significa que el producto no tiene precio cargado: NO se lo muestres al cliente, decile "déjame confirmar el precio con un compañero" y ofrecé pasarlo al equipo.
- Si la búsqueda devuelve vacío, decí amablemente que no encontraste ese producto exacto y ofrecé pasarlo al equipo humano.
- Dar información sobre envíos por Pymexpress, Encomienda Nacional Correos CR, Tavo Encomiendas o Dual Global a todo el país.

ENVÍOS — leer con atención:
- Cuando el cliente pregunte cuánto cuesta el envío, qué opciones tiene, o pida comparar precios entre servicios, usá la herramienta `calculate_shipping_quote` con el peso aproximado del pedido en kilos. Si no sabés el peso, preguntale al cliente cuánto pesa aproximadamente el pedido (1 kg, 5 kg, etc.).
- Presentá las opciones devueltas al cliente como una lista breve con precios, cada una en su propia línea y con el precio en negrita. Dejá una línea en blanco entre la frase de entrada y la lista. Ej:
  "Para X kg, estas son las opciones de envío:

  - Pymexpress (entrega a domicilio): *₡8.400*
  - Encomienda Nacional (retira en oficina Correos): *₡5.300*
  - Transtusa/Tavo: *₡2.500*
  - Dual Global (retira en agencia): *₡3.000*
  - Retirada en almacén Santa Cruz, Turrialba: *gratis*"
- Si la respuesta de `calculate_shipping_quote` trae `needs_human_quote: true` o algún carrier tiene `price_crc: null`, decile al cliente que ese servicio específico lo cotiza un compañero (no inventes precio).
- Para Dual Global, si el cliente eligió Dual o pregunta por la agencia más cercana, USÁ la herramienta `find_dual_agency` con la provincia del cliente (y cantón si lo dijo). La tool devuelve agencias reales con dirección, horario y Google Maps. Presentale al cliente hasta 2-3 opciones. Si el cliente no te dijo la provincia, preguntásela primero (Dual tiene sucursales en las 7 provincias). NUNCA inventes direcciones de agencias Dual.
- El precio del envío SIEMPRE va aparte del precio del producto. Cuando armes el total final, listá ambos (producto + envío) por separado.
- Indicar horario de atención: Lunes a Viernes de 8am a 6pm hora Costa Rica.
- Si la consulta es compleja (devolución, problema con pedido, precio especial, mayoreo, cotización formal), respondé brevemente y avisá que un humano va a contactar pronto.
FLUJO DE COMPRA — leer con atención:
- Cuando el cliente confirma intención de compra (frases como "lo llevo", "envíamelo", "hacé el pedido", "reservámelo", "me los llevo", "quiero comprar N de X"), tu ÚNICA acción posible es invocar la herramienta `create_quotation`. NO inventes números de cotización ni anuncies que la cotización está creada antes de invocarla.
- Antes de invocarla, asegurate de tener el código exacto (`default_code`) que devolvió `search_products` y la cantidad clara. Si no tenés alguno, preguntale al cliente y NO invoques nada todavía.
- NO pidas "dirección" antes de crear la cotización. Tampoco pidas teléfono, ni nombre, ni datos personales. El equipo coordina envío y datos después por otros medios.
- IMPORTANTE sobre precios: el `total_crc` que devuelve `create_quotation` es el subtotal de productos SIN envío ni impuestos finales. Cuando comuniques el total al cliente, aclará siempre que NO incluye envío, ej: "₡6,000 (sin envío). El envío lo confirma un compañero según destino."

PROHIBICIONES ABSOLUTAS sobre cotizaciones (nunca las rompas, ni siquiera "para ser amable"):
1. PROHIBIDO mencionar un número de cotización (formato S0####, S#####, SO#####, o cualquier código) que NO venga textualmente de un tool_result reciente de `create_quotation` con `ok: true`. Si no recibiste ese tool_result en ESTA conversación, no existe número que puedas mencionar.
2. PROHIBIDO decir frases tipo "te armé/anoté/creé la cotización/orden/pedido" si no acabás de recibir tool_result con `ok: true`.
3. PROHIBIDO copiar números o ejemplos del prompt. Los códigos de cotización los inventa Odoo, vos solo los repetís cuando los recibís en el tool_result.
4. Si `create_quotation` devuelve `ok: false` o error, NO le digas al cliente que se creó. Decile algo neutral tipo "Un compañero te confirma los detalles enseguida" y dejá que un humano resuelva.
5. Si dudás de si crear cotización, NO la crees y respondé en texto pidiendo confirmación al cliente.

PROHIBICIONES ABSOLUTAS adicionales (cada una es bloqueante, NO las rompas):

A. **LOCAL FÍSICO**: NUNCA digas que tenemos local físico, sucursal, tienda, "podés venir a verla", "te esperamos", o cualquier mención a atención presencial. Paracarpinteros NO atiende público presencial — solo online + envío. Base operativa privada en Turrialba (no es local de visita). Si el cliente pregunta si puede ir a verlo: "No tenemos local de visita al público, somos solo online con envíos a todo el país."

B. **FOTOS — SÍ podés enviarlas**: NUNCA digas "no puedo enviar fotos por este chat", "te recomiendo verlas en la web", "escribí al correo para fotos" o frases similares. SÍ tenés la herramienta `send_product_photo(codigo)` que envía la foto del producto. Si el cliente pide foto/imagen/pantallazo y tenés el código del producto (de un `search_products` reciente), INVOCÁ `send_product_photo` SIN excepciones y respondé en UNA frase corta tipo "Te paso la foto" (sin emojis, sin signos de exclamación).

C. **DATOS PERSONALES — captura voluntaria SOLAMENTE**:
   - NUNCA pidas proactivamente nombre, email, dirección, cédula, calle/número/referencias, teléfono u otros datos sensibles.
   - EXCEPCIÓN ÚNICA: si el cliente eligió envío a domicilio (Pymexpress) podés preguntar la dirección porque es necesaria para generar la guía. Para Tavo/Dual/Retiro NO la pidas.
   - SÍ guardá lo que el cliente comparta VOLUNTARIAMENTE: si dice "soy Juan Pérez", "vivo en Liberia, Guanacaste", "mi correo es x@y.com", "mi cédula es 1-1234-5678", invocá `update_partner_info` con los datos que dio (solo los que mencionó). Esto enriquece su ficha en Odoo para que Gabriela tenga la info al confirmar el pedido.
   - NUNCA pidas cédula/vat — solo guardala si el cliente la mencionó él mismo.

G. **ENVÍO SIEMPRE en la cotización** (no hay excepciones salvo Retirada en almacén):
   - ANTES de invocar `create_quotation`, el cliente DEBE haber elegido método de envío. Si no lo eligió todavía, preguntale "¿Cómo te lo enviamos? Te paso las opciones" y usá `calculate_shipping_quote` para mostrar precios. Esperá que elija ANTES de crear la cotización.
   - Al invocar `create_quotation`, SIEMPRE pasá `envio_carrier_short` + `envio_precio_crc`. Excepción única: si eligió "Retirada en almacén Santa Cruz" (Retiro), no hace falta porque no hay envío.
   - El `total_crc` que devuelve el tool_result ya incluye producto + envío. Comunicale al cliente ese total, NO uno calculado mentalmente.

H. **DIFERENCIAR productos similares**: si `search_products` devuelve 2-3 productos con nombre/precio muy parecidos (ej: "Tapeteadora A704" ₡285k y "Tapeteadora A2197 110V WJS-480" ₡285k), NO los listes en seco — explicá la diferencia real (voltaje, modelo, accesorios) y/o preguntá un requisito al cliente que ayude a desambiguar:
   - "Tengo dos modelos al mismo precio: la A704 estándar y la A2197 compatible con mesa WJS-480 (110V). ¿Querés que un compañero te asesore para elegir, o sabés cuál prefieres?"
   - Si el cliente da un requisito (voltaje, marca, uso), volvé a buscar con esa palabra adicional o explicale cuál encaja mejor.
   - NO listes 3 productos en seco como "Tengo 1. A 2. B 3. C". Ofrecé contexto.

I. **TONO**: amable, claro y profesional. No "compa", no robot.
   - PROHIBIDOS los emojis (👇 ✅ 🚚 📦 ⚠ 👋 🙌 🎉 etc.). Cero emojis en respuestas al cliente.
   - PROHIBIDAS las exclamaciones efusivas tipo "¡Genial!", "¡Buenísimo!", "¡Listo!", "¡Perfecto!". Si necesitás confirmar, usá "Perfecto." (con punto) o "Listo." una sola vez por turno, o simplemente avanzá al siguiente paso sin frase de relleno.
   - PROHIBIDAS las muletillas tipo "Mirá", "Buenísimo", "Acá te la paso", "Te cuento". Hablá directo, como un asistente discreto, no como un amigo entusiasta.
   - Cuando mandes una card de producto con `send_product_photo`, NO repitas el código, nombre, ni precio en el texto. Una frase corta tipo "Te paso la foto." o "La paso." y silencio.
   - Variaciones cortas y formales: "Perfecto.", "Bien.", "Anotado." (sin signos de exclamación).
   - ESCRITURA NATURAL — escribí como una persona del equipo por WhatsApp, no como un texto pulido de IA:
     · No uses raya ni guion largo (—) para pausas dramáticas; usá punto o coma.
     · Evitá la "regla de tres" (no enumeres siempre tres cosas); si una frase basta, no hagas lista.
     · Evitá paralelismos negativos tipo "no es solo X, sino Y" o "no se trata de X, sino de Y".
     · Nada de adjetivos inflados ni publicitarios ("excelente", "increíble", "amplia gama", "la mejor opción"). Describí seco y concreto.
     · Variá la frase de escalado: no repitas siempre "Un compañero te confirma". Alterná, p.ej. "Lo verifico con el equipo y te digo", "Dejá que lo confirme y te aviso", "Eso lo coordina Gabriela, te contacta ella".

J. **EN PROCESO**: Si ya estás en medio de un flujo de compra (cotizaste, peso, envío) y el cliente cambia de tema bruscamente, retomá pero recordale dónde quedamos: "Claro, te ayudo con eso. Y sobre la tapeteadora que estábamos viendo, ¿seguís interesado o lo dejamos para otro momento?"

D. **REPETICIÓN**: Si notás que el cliente está enviando la MISMA pregunta 2-3 veces seguidas (porque no le diste lo que quería), NO repitas la misma respuesta. Reconocé la repetición y ofrecé pasarlo con un humano: "Veo que te estoy dando vueltas con esto, dejame pasarte con un compañero que te resuelve mejor."

E. **INVENCIÓN**: NUNCA inventes datos que no estén en el `knowledge_block` o en resultados de tools. Si no sabés algo (dirección de agencia Dual, peso exacto de un producto, código de un producto que no buscaste), decí "Un compañero te confirma ese dato" y NO inventes.

F. **CONSISTENCIA DEL TOTAL**: Cuando informes al cliente "Total = producto + envío", asegurate de que la cotización que crees con `create_quotation` incluya AMBOS (producto + línea de envío). Si solo añadís el producto al sale.order, NO digas que el total incluye envío.

K. **CONSEJOS — SOLO CUANDO LOS PIDEN**: NUNCA des recomendaciones, consejos técnicos, sugerencias de uso, ni comparativas entre productos por iniciativa propia. Solo si el cliente pregunta explícitamente "¿cuál me recomendás?", "¿qué me sirve para X?", "¿cuál es mejor para Y?" podés opinar. Y cuando lo hagas:
   - Sé cauto. Si hay ambigüedad sobre el uso real, preguntá primero antes de recomendar.
   - No afirmes algo técnico que no podés verificar. Si dudás, decí "Para esa aplicación, un compañero te asesora mejor, lo confirmo con él."
   - NUNCA des consejos de instalación, seguridad, dosis, dureza, tornillería, calibres, voltajes, etc. sin que el cliente lo haya pedido. Esos consejos pueden confundir o ser incorrectos según el caso real del cliente.
   - Si el cliente solo describió un problema ("se me astilla la madera"), NO ofrezcas solución a menos que pregunte. Limitate a "Un compañero te puede dar la solución exacta. ¿Te paso con él?"

L. **BREVEDAD**: respuestas de 1 a 2 oraciones por defecto. Solo extendete cuando listás productos (hasta 3, una línea cada uno) o opciones de envío. Nada de explicaciones largas, contexto innecesario, ni "como te decía antes". Si el cliente pregunta algo simple, contestá con lo justo.

M. **FORMATO EN WHATSAPP (que se vea ordenado, no robótico)**: WhatsApp entiende formato. Usalo con moderación para que el mensaje se lea limpio, nunca para decorar.
   - Negrita con *asteriscos* solo en lo que importa: el precio y, si listás varios, el nombre del producto. Ej de línea de producto: "*Sierra circular Makita 5007* · ₡92,000 (cód. SM-5007)".
   - Cuando listés 2-3 productos u opciones de envío, poné cada ítem en su propia línea (salto de línea real), no todo en un párrafo corrido. Dejá una línea en blanco entre la frase de entrada y la lista para que respire.
   - Para separar dato y dato dentro de una línea usá un punto medio " · " o paréntesis, nunca la raya larga (—).
   - No abuses: máximo un par de negritas por mensaje, nada de TODO EN MAYÚSCULAS ni frases enteras en negrita. El exceso de formato y las mayúsculas se leen como spam y, además, hacen que la gente bloquee.
   - El orden lo dan los saltos de línea y la negrita puntual, no los íconos: cero emojis (ya prohibidos).

N. **POLÍTICA WHATSAPP / META — evitar que bloqueen el número (crítico, no negociable)**: el número es de la empresa; un bloqueo de Meta corta el canal con todos los clientes. Para evitarlo:
   - Respondé SOLO a lo que el cliente escribió. Nunca mandes promociones, catálogos, ofertas ni "¿sigues ahí?" por iniciativa propia. Los mensajes no solicitados son la causa #1 de baneo.
   - Un solo mensaje por respuesta siempre que se pueda. No partas la contestación en 4-5 mensajes seguidos: se ve como spam. La única excepción válida es la foto/card del producto, que va aparte por necesidad técnica.
   - No repitas el mismo mensaje una y otra vez (ver regla D, REPETICIÓN). Si das vueltas, pasá a un humano en vez de insistir.
   - Pocos enlaces y solo los propios (sitio o ficha del producto cuando aporta). Nada de links acortados ni varios enlaces en un mismo mensaje.
   - Resolvé rápido y hablá natural: un bot pesado, repetitivo o que no entiende hace que el cliente bloquee o reporte, y eso es justo lo que dispara la baja del número.

Tono: amable, profesional, cordial sin ser efusivo. Tratá de "usted" por defecto; pasá a "vos" solo si el cliente lo usa primero. Respuestas cortas. Cero emojis. Sin exclamaciones de relleno.

Sitio web: www.paracarpinteros.com
Email: info@paracarpinteros.com
Teléfono: +506 8606-9717"""


# ───────── ODOO TOOL ─────────
_odoo_uid_cache: Optional[int] = None


def odoo_authenticate() -> Optional[int]:
    """Auth XML-RPC con Odoo. Cachea el uid en memoria del proceso."""
    global _odoo_uid_cache
    if _odoo_uid_cache:
        return _odoo_uid_cache
    if not all([ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY]):
        return None
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
        uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, {})
        _odoo_uid_cache = uid or None
        return _odoo_uid_cache
    except Exception as e:
        print(f"[odoo auth err] {e}")
        return None


import re

# Stopwords a ignorar al tokenizar (palabras que el cliente usa pero no aportan a la búsqueda)
_STOPWORDS = {
    "de", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "para", "por", "con", "sin", "y", "o", "u", "del", "al",
    "que", "qué", "cual", "cuál", "cuanto", "cuánto", "cuanta", "cuánta",
    "tenes", "tienes", "tenés", "tiene", "tienen", "hay",
    "venden", "vende", "vendés", "vendes",
    "necesito", "busco", "quiero",
    "mm", "cm", "pulg", "pulgada", "pulgadas",  # las medidas las metemos junto al número
}


def _tokenize_query(query: str) -> list[str]:
    """
    Convierte 'avellanador 8mm' en ['avellanador', '8'].
    'sierra circular 7 1/4' → ['sierra', 'circular', '7', '1/4'].
    Separa dígitos de letras: '8mm' → ['8'] (el 'mm' es stopword).
    """
    s = (query or "").lower().strip()
    if not s:
        return []
    # Separar dígitos de letras: 'avellanador8mm' → 'avellanador 8 mm'
    s = re.sub(r"(\d+)([a-zA-Z])", r"\1 \2", s)
    s = re.sub(r"([a-zA-Z])(\d+)", r"\1 \2", s)
    # Tokens: secuencias alfanuméricas (mantengo '/' por medidas tipo 1/4)
    tokens = re.findall(r"[a-zA-ZáéíóúñüÁÉÍÓÚÑÜ0-9/]+", s)
    out = []
    seen = set()
    for t in tokens:
        if t in _STOPWORDS:
            continue
        if len(t) < 2 and not t.isdigit():
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _es_disponible(p: dict) -> bool:
    """Replica la lógica de disponibilidad de la tienda web Odoo.

    En este catálogo casi todos los productos son tipo 'consu' (consumible) o
    'service': Odoo NUNCA bloquea su venta en la web por falta de stock, así que
    aparecen como comprables en la página aunque `qty_available` sea 0. Solo los
    'product' (almacenables) se bloquean al agotarse, y aún así se venden si
    tienen `allow_out_of_stock_order=True`.

    Antes el bot trataba `qty_available <= 0` como "no disponible" y rechazaba
    ~413 productos que la web sí vende. Esto lo alinea con la tienda.
    """
    tipo = p.get("type")
    if tipo in ("consu", "service"):
        return True
    if p.get("allow_out_of_stock_order"):
        return True
    return int(p.get("qty_available") or 0) > 0


def _odoo_search(domain: list, limit: int):
    """Wrapper de search_read con manejo de sesión caída."""
    global _odoo_uid_cache
    uid = odoo_authenticate()
    if not uid:
        return []
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        return models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "product.template", "search_read",
            [domain],
            {
                "fields": ["name", "default_code", "list_price", "qty_available", "description_sale", "weight", "type", "allow_out_of_stock_order"],
                "limit": limit,
                "order": "list_price asc",
            },
        )
    except Exception as e:
        print(f"[odoo search err] {e}")
        _odoo_uid_cache = None
        return []


def search_products_odoo(query: str, limit: int = 3) -> list[dict]:
    """
    Busca productos en Odoo.
    Estrategia: tokeniza la query y hace AND de ILIKE por token contra nombre.
    Si no encuentra nada con AND, hace fallback con OR sobre tokens >=4 chars.
    Si no encuentra nada, fallback con la query completa cruda.
    Filtra basura del catálogo: precios <= 100 CRC (sin precio cargado) y productos sin default_code.
    """
    if not query or not query.strip():
        return []
    tokens = _tokenize_query(query)

    # Excluir productos basura: precio dummy ≤₡100 y sin código asignado
    base = [
        ("sale_ok", "=", True),
        ("list_price", ">", 100),
        ("default_code", "!=", False),
    ]
    rows = []

    # Estrategia 1: AND de todos los tokens
    if tokens:
        domain = list(base) + [("name", "ilike", t) for t in tokens]
        rows = _odoo_search(domain, limit)

    # Estrategia 2: solo tokens "fuertes" (>=4 chars y no número) AND
    if not rows and tokens:
        strong = [t for t in tokens if len(t) >= 4 and not t.isdigit()]
        if strong:
            domain = list(base) + [("name", "ilike", t) for t in strong]
            rows = _odoo_search(domain, limit)

    # Estrategia 3: OR sobre tokens fuertes (cualquiera matchea)
    if not rows and tokens:
        strong = [t for t in tokens if len(t) >= 4 and not t.isdigit()]
        if strong:
            # Construir OR: ['|', '|', term1, term2, term3, ...]
            or_part: list = []
            for t in strong:
                or_part.append(("name", "ilike", t))
            # Prefijar '|' (N-1 veces) para OR explícito en Odoo
            for _ in range(len(strong) - 1):
                or_part.insert(0, "|")
            domain = list(base) + or_part
            rows = _odoo_search(domain, limit)

    # Estrategia 4: query crudo (último recurso)
    if not rows:
        rows = _odoo_search([("name", "ilike", query.strip()), ("sale_ok", "=", True)], limit)

    out = []
    for p in rows:
        weight_kg = float(p.get("weight") or 0)
        out.append({
            "codigo": p.get("default_code") or "",
            "nombre": (p.get("name") or "").strip(),
            "precio_crc": int(round(p.get("list_price") or 0)),
            "stock": int(p.get("qty_available") or 0),
            "disponible": _es_disponible(p),
            "descripcion": (p.get("description_sale") or "").strip()[:160],
            "peso_kg": weight_kg if weight_kg > 0 else None,
        })
    return out


# Carriers que la tool calculate_shipping_quote debe consultar (ordenados por preferencia del usuario)
SHIPPING_CARRIERS = [
    {"id": 2,  "name": "Pymexpress (Correos CR, entrega a domicilio)",         "short": "Pymex"},
    {"id": 7,  "name": "Encomienda Nacional (Correos CR, retira en oficina)",  "short": "EncomCR"},
    {"id": 10, "name": "Transtusa / Tavo Encomiendas",                          "short": "Tavo"},
    {"id": 11, "name": "Dual Global (retira en agencia más cercana)",          "short": "Dual"},
    {"id": 1,  "name": "Retirada en almacén Santa Cruz, Turrialba",            "short": "Retiro"},
]
MAX_AUTO_QUOTE_KG = 30

# Cache en memoria de agencias Dual (cargado al startup desde JSON)
DUAL_AGENCIAS_PATH = "/app/static/dual_agencias.json"
_DUAL_AGENCIAS: Optional[list[dict]] = None


def _load_dual_agencias() -> list[dict]:
    global _DUAL_AGENCIAS
    if _DUAL_AGENCIAS is not None:
        return _DUAL_AGENCIAS
    try:
        with open(DUAL_AGENCIAS_PATH, "r", encoding="utf-8") as f:
            _DUAL_AGENCIAS = json.load(f)
        print(f"[dual] cargadas {len(_DUAL_AGENCIAS)} agencias")
    except Exception as e:
        print(f"[dual load err] {e}")
        _DUAL_AGENCIAS = []
    return _DUAL_AGENCIAS


def find_dual_agencies(provincia: Optional[str] = None, canton: Optional[str] = None) -> list[dict]:
    """Devuelve agencias Dual filtradas por provincia/cantón.
    - Si se da provincia: matchea por nombre de provincia (case-insensitive, parcial).
    - Si se da cantón: además matchea por nombre de la sucursal o alias (parcial).
    - Devuelve hasta 4 resultados ordenados (principales primero).
    """
    agencias = _load_dual_agencias()
    if not agencias:
        return []

    def _norm(s: str) -> str:
        if not s: return ""
        # Quitar tildes/diacríticos básicos
        rep = str.maketrans("áéíóúÁÉÍÓÚñÑ", "aeiouAEIOUnN")
        return s.translate(rep).lower().strip()

    prov_n = _norm(provincia or "")
    canton_n = _norm(canton or "")

    matches = []
    for a in agencias:
        if prov_n:
            ap = _norm(a.get("provincia") or "")
            if prov_n not in ap and ap not in prov_n:
                continue
        if canton_n:
            haystack = " ".join([
                _norm(a.get("nombre") or ""),
                _norm(a.get("alias") or ""),
                _norm(a.get("direccion") or ""),
            ])
            if canton_n not in haystack:
                continue
        matches.append({
            "provincia": a.get("provincia"),
            "nombre": a.get("nombre"),
            "direccion": a.get("direccion"),
            "telefono": a.get("telefono"),
            "horario": a.get("horario"),
            "maps": a.get("maps"),
            "principal": a.get("principal", False),
        })

    # Ordenar: principales primero, luego alfabético por nombre
    matches.sort(key=lambda x: (not x.get("principal"), x.get("nombre", "")))
    return matches[:4]


TRANSTUSA_PROVINCIAS_OK = {"san jose", "san josé", "cartago"}


def _is_transtusa_available(provincia: Optional[str]) -> bool:
    """Transtusa solo opera desde/hacia San José y Cartago."""
    if not provincia:
        return False
    p = provincia.lower().strip()
    return any(ok in p for ok in TRANSTUSA_PROVINCIAS_OK)


def calculate_shipping_quote_odoo(weight_kg: float, provincia: Optional[str] = None) -> dict:
    """Calcula precio de envío para todos los carriers definidos en SHIPPING_CARRIERS, dado un peso en kg.
    Si `provincia` se conoce y NO es San José ni Cartago, se excluye Transtusa (no opera fuera de esa zona).
    Devuelve dict con: weight_kg, quotes (lista), needs_human_quote, excluded_carriers."""
    if weight_kg is None or weight_kg <= 0:
        return {"error": "Peso inválido. El peso debe ser mayor a 0."}
    transtusa_ok = _is_transtusa_available(provincia) if provincia else None  # None = no sabemos
    uid = odoo_authenticate()
    if not uid:
        return {"error": "No se pudo autenticar con Odoo para cotizar"}
    try:
        api = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    except Exception as e:
        return {"error": f"No se pudo conectar a Odoo: {str(e)[:80]}"}
    quotes = []
    excluded = []
    needs_human = False
    for c in SHIPPING_CARRIERS:
        # Filtro de Transtusa: solo San José y Cartago. Si conocemos provincia y NO califica → excluir.
        if c["short"] == "Tavo" and transtusa_ok is False:
            excluded.append({
                "short": "Tavo",
                "name": c["name"],
                "reason": f"Transtusa solo opera en San José y Cartago (provincia del cliente: {provincia})",
            })
            continue
        try:
            carrier = api.execute_kw(
                ODOO_DB, uid, ODOO_API_KEY, "delivery.carrier", "read",
                [[c["id"]], ["delivery_type", "fixed_price", "active"]]
            )
            if not carrier or not carrier[0].get("active"):
                continue
            ctype = carrier[0].get("delivery_type")
            if ctype == "fixed":
                quotes.append({
                    "short": c["short"],
                    "name": c["name"],
                    "price_crc": int(carrier[0].get("fixed_price") or 0),
                })
                continue
            # base_on_rule → recorrer reglas en orden de sequence
            rules = api.execute_kw(
                ODOO_DB, uid, ODOO_API_KEY, "delivery.price.rule", "search_read",
                [[("carrier_id", "=", c["id"])]],
                {"fields": ["sequence", "operator", "max_value", "list_base_price"],
                 "order": "sequence asc, id asc"}
            )
            picked = None
            for r in rules:
                op, mv, price = r["operator"], r["max_value"], r["list_base_price"]
                if op == "<=" and weight_kg <= mv:
                    picked = price; break
                if op == "<" and weight_kg < mv:
                    picked = price; break
            if picked is None:
                # Buscar regla > que matchee
                for r in rules:
                    if r["operator"] == ">" and weight_kg > r["max_value"]:
                        picked = r["list_base_price"]; break
            if picked is not None:
                quotes.append({"short": c["short"], "name": c["name"], "price_crc": int(picked)})
            else:
                # Carrier no cotiza este peso → escala
                needs_human = True
                quotes.append({
                    "short": c["short"],
                    "name": c["name"],
                    "price_crc": None,
                    "note": f"No auto-cotizable para {weight_kg:g} kg, cotiza un compañero",
                })
        except Exception as e:
            print(f"[shipping quote err carrier {c['id']}] {e}")
            continue
    return {
        "weight_kg": weight_kg,
        "provincia": provincia,
        "quotes": quotes,
        "excluded": excluded,
        "transtusa_status": (
            "ok" if transtusa_ok else
            ("excluido_por_provincia" if transtusa_ok is False else "provincia_no_informada")
        ),
        "needs_human_quote": needs_human or weight_kg > MAX_AUTO_QUOTE_KG,
        "max_auto_kg": MAX_AUTO_QUOTE_KG,
    }


CLAUDE_TOOLS = [
    {
        "name": "search_products",
        "description": (
            "Busca productos en el catálogo real de Paracarpinteros en Odoo. "
            "Usalo SIEMPRE que el cliente mencione un producto, herramienta, marca, "
            "medida o pida precio/stock. Devuelve hasta 3 resultados con: código, "
            "nombre, precio en colones, `disponible` (booleano: si se puede vender; casi siempre true porque se vende por encargo) y peso en kg (si está cargado en la ficha). "
            "Si necesitás el peso del producto para cotizar envío con `calculate_shipping_quote`, "
            "usá el `peso_kg` que devuelve esta tool (no preguntes al cliente si Odoo ya lo trae). "
            "Ejemplos de query: 'avellanador 8mm', 'sierra circular makita', "
            "'tornillo phillips', 'bisagra'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Términos de búsqueda del producto, en español. Pueden ser palabras sueltas o frase corta. Incluí medidas y marcas si el cliente las mencionó.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "send_product_photo",
        "description": (
            "Envía al cliente una TARJETA VISUAL completa del producto: foto + nombre + precio + "
            "código de referencia + botón 'Ver detalles' que abre la ficha en paracarpinteros.com. "
            "Usalo cuando el cliente pida ver foto, imagen, pantallazo o detalles visuales de un producto, "
            "o cuando la imagen ayude a confirmar el producto correcto. "
            "Tomá el código exacto (default_code) que devolvió search_products. "
            "UNA sola tarjeta por turno (si hay varias opciones, envía la más relevante). "
            "Después de llamarla, respondé con UNA frase MUY corta tipo 'Acá te la paso 👇' o "
            "'Mirá 👇' o 'Te paso la ficha'. NO repitas el código (Ref), nombre ni precio — ya están "
            "grandes en la card. NO digas 'Ref: XXX' después de mandar card."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "codigo": {
                    "type": "string",
                    "description": "El código (default_code) exacto del producto, ej: 'A805'",
                }
            },
            "required": ["codigo"],
        },
    },
    {
        "name": "mark_payment_received",
        "description": (
            "Registra un comprobante de pago recibido del cliente (Sinpe Móvil, "
            "transferencia bancaria, depósito, captura del banco). Usalo CUANDO "
            "el cliente envíe una foto que claramente es un comprobante de pago "
            "(NO usar si es foto de un producto). Pasale los datos que veas en el comprobante. "
            "Esto marca la conversación como 'pagado' en el panel para que el equipo prepare envío."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "monto_crc": {
                    "type": "number",
                    "description": "Monto en colones que aparece en el comprobante. Si el comprobante está en otra moneda, convertí aproximadamente o pasá 0.",
                },
                "metodo": {
                    "type": "string",
                    "enum": ["sinpe", "transferencia", "deposito", "tarjeta", "otro"],
                    "description": "Método de pago detectado",
                },
                "referencia": {
                    "type": "string",
                    "description": "Número de comprobante o referencia que aparece, si lo ves",
                },
                "banco": {
                    "type": "string",
                    "description": "Banco origen o destino, ej: BNCR, BAC, BCR, Scotia, Davivienda",
                },
                "fecha": {
                    "type": "string",
                    "description": "Fecha que aparece en el comprobante, formato libre",
                },
            },
            "required": ["monto_crc", "metodo"],
        },
    },
    {
        "name": "create_quotation",
        "description": (
            "Crea una cotización (sale.order en borrador) en Odoo para el cliente. "
            "Usalo SÓLO cuando el cliente confirma intención clara de comprar uno o varios productos: "
            "frases como 'lo llevo', 'quiero comprarlo', 'envíamelo', 'hacé el pedido', "
            "'reservámelo', 'agendá esos 2', 'me los llevo'. "
            "NO la uses si el cliente solo está consultando precio, foto o disponibilidad. "
            "Pasale lista de items con código (default_code) y cantidad. "
            "Cantidad por defecto 1 si el cliente no la dijo, pero confirmá la cantidad antes de crear si tenés duda. "
            "Si el cliente YA eligió método de envío Y ya tenés el precio de envío (de un `calculate_shipping_quote` previo), "
            "incluí los parámetros `envio_carrier_short` y `envio_precio_crc` para que la línea de envío se agregue "
            "a la cotización automáticamente (así el total que se le dijo al cliente coincide con el de Odoo). "
            "Después de crearla, decile brevemente al cliente el número de cotización "
            "(ej: 'Listo, te armé la cotización S07162. Un compañero te confirma pronto')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Lista de productos a cotizar",
                    "items": {
                        "type": "object",
                        "properties": {
                            "codigo": {"type": "string", "description": "default_code del producto (ej: 'A805')"},
                            "cantidad": {"type": "number", "description": "Unidades. Si el cliente no la dijo, 1."},
                        },
                        "required": ["codigo", "cantidad"],
                    },
                },
                "nota": {
                    "type": "string",
                    "description": "Nota interna opcional con detalles del pedido para el compañero que confirme",
                },
                "envio_carrier_short": {
                    "type": "string",
                    "enum": ["Pymex", "EncomCR", "Tavo", "Dual", "Retiro"],
                    "description": "Carrier elegido por el cliente. Usá el `short` exacto que devolvió calculate_shipping_quote. NO inventes.",
                },
                "envio_precio_crc": {
                    "type": "number",
                    "description": "Precio del envío en colones (el `price_crc` que devolvió calculate_shipping_quote para ese carrier).",
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "update_partner_info",
        "description": (
            "Actualiza datos del cliente en Odoo (res.partner). Usalo SOLO si el cliente compartió esos datos "
            "VOLUNTARIAMENTE en la conversación (no se los pediste). Ejemplos: 'soy Juan Pérez', 'mi correo es x@y.com', "
            "'vivo en Liberia, Guanacaste', 'tomá mi cédula 1-1234-5678'. "
            "NUNCA preguntes proactivamente datos personales sólo para llenar esta tool. "
            "EXCEPCIÓN: si el cliente ya eligió envío a domicilio (Pymexpress) podés preguntarle la dirección porque es necesaria para generar la guía. "
            "Pasá solo los campos que conozcas — los que no, dejalos vacíos. Cédula/vat SOLO si el cliente la mencionó."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name":     {"type": "string", "description": "Nombre completo del cliente"},
                "email":    {"type": "string", "description": "Email del cliente (formato válido)"},
                "street":   {"type": "string", "description": "Dirección postal del cliente (calle, número, referencias)"},
                "city":     {"type": "string", "description": "Cantón o ciudad del cliente"},
                "province": {
                    "type": "string",
                    "enum": ["San José", "Alajuela", "Cartago", "Heredia", "Guanacaste", "Puntarenas", "Limón"],
                    "description": "Provincia de Costa Rica (usá la enum exacta)",
                },
                "vat":      {"type": "string", "description": "Cédula nacional o RTN. SOLO si el cliente la mencionó él mismo."},
            },
        },
    },
    {
        "name": "find_dual_agency",
        "description": (
            "Busca agencias de Dual Global cercanas al cliente para retiro de paquetes. "
            "Dual tiene 54 sucursales en Costa Rica distribuidas en las 7 provincias. "
            "Usalo cuando el cliente pregunte por agencias Dual en su zona, o cuando ya "
            "eligió Dual Global como método de envío y necesita saber dónde retirar el paquete. "
            "Devuelve hasta 4 agencias con nombre, dirección, teléfono, horario y link de Google Maps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "provincia": {
                    "type": "string",
                    "enum": ["San José", "Alajuela", "Cartago", "Heredia", "Guanacaste", "Puntarenas", "Limón"],
                    "description": "Provincia donde está el cliente. Usá la enum exacta. Si el cliente no dijo provincia, preguntale primero.",
                },
                "canton": {
                    "type": "string",
                    "description": "Cantón o ciudad específica del cliente, opcional. Si lo dice, ayuda a filtrar a la agencia más cercana (ej: 'Liberia', 'Turrialba', 'Pérez Zeledón').",
                },
            },
            "required": ["provincia"],
        },
    },
    {
        "name": "calculate_shipping_quote",
        "description": (
            "Calcula precios de envío para un peso dado en los servicios disponibles "
            "(Pymexpress, Encomienda Nacional, Transtusa/Tavo, Dual Global, Retirada en almacén). "
            "Usalo SIEMPRE que el cliente pregunte cuánto cuesta el envío, qué opciones hay, "
            "o pida comparar precios. Devuelve precio en colones para cada uno. "
            "Para pedidos >30 kg, Pymexpress y Encomienda Nacional NO auto-cotizan (un humano confirma); "
            "Transtusa y Dual sí cubren cualquier peso. "
            "**Restricción Transtusa**: solo opera en San José y Cartago. Si la provincia del cliente es otra, "
            "Transtusa se excluye automáticamente. Por eso conviene pasar la provincia del cliente si la conocés "
            "(de partner Odoo o de algo que dijo en el chat). Si la provincia es desconocida, Transtusa aparece "
            "pero advertí al cliente que solo aplica para SJ/Cartago. "
            "Si el cliente no te dijo el peso, preguntale el peso aproximado del pedido en kilos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "weight_kg": {
                    "type": "number",
                    "description": "Peso total del pedido en kilogramos. Si tenés gramos, dividí entre 1000. Mínimo 0.5 kg.",
                },
                "provincia": {
                    "type": "string",
                    "enum": ["San José", "Alajuela", "Cartago", "Heredia", "Guanacaste", "Puntarenas", "Limón"],
                    "description": "Provincia del cliente (CR). OPCIONAL pero MUY recomendado: si la pasás, el sistema filtra Transtusa automáticamente para clientes fuera de SJ/Cartago. Si no la sabés, podés omitirla o preguntarle al cliente.",
                },
            },
            "required": ["weight_kg"],
        },
    },
]


async def send_wa_image_by_id(to: str, media_id: str, caption: str = "") -> dict:
    """Envía un mensaje tipo image usando un media_id de Meta."""
    url = f"{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id, "caption": caption[:1024]} if caption else {"id": media_id},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload, headers=headers)
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": r.text[:500]}


async def send_wa_interactive_cta(
    to: str,
    header_image_media_id: str,
    body_text: str,
    footer_text: str,
    cta_label: str,
    cta_url: str,
) -> dict:
    """Envía un mensaje interactive tipo cta_url con imagen de header + botón URL.
    Limites WA: body<=1024, footer<=60, cta_label<=20, url cualquier https válida."""
    url = f"{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "header": {"type": "image", "image": {"id": header_image_media_id}},
            "body": {"text": (body_text or "")[:1024]},
            "footer": {"text": (footer_text or "")[:60]},
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": (cta_label or "Ver más")[:20],
                    "url": cta_url,
                },
            },
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"status_code": r.status_code, "text": r.text[:500]}
        if isinstance(data, dict) and "error" in data:
            print(f"[WA INTERACTIVE ERROR] {data['error']}")
        return data


async def transcribe_audio(audio_bytes: bytes, mime: str = "audio/ogg", filename: str = "audio.ogg") -> Optional[str]:
    """Transcribe audio usando OpenAI Whisper API. Devuelve texto o None si falla.
    Costos: ~$0.006/minuto. Soporta español."""
    if not OPENAI_API_KEY:
        print("[whisper] OPENAI_API_KEY no configurada, saltando transcripción")
        return None
    if not audio_bytes:
        return None
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            files = {"file": (filename, audio_bytes, mime)}
            data = {"model": "whisper-1", "language": "es"}
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
            r = await client.post(WHISPER_API, headers=headers, data=data, files=files)
            if r.status_code != 200:
                print(f"[whisper] HTTP {r.status_code}: {r.text[:400]}")
                return None
            txt = (r.json() or {}).get("text", "").strip()
            return txt or None
    except Exception as e:
        print(f"[whisper err] {e}")
        return None


async def download_meta_media(media_id: str) -> Optional[bytes]:
    """Descarga binario de un mensaje multimedia (imagen/audio/etc) de Meta. 2 pasos: GET URL → GET binario."""
    if not media_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r1 = await client.get(
                f"{WA_API_BASE}/{media_id}",
                headers={"Authorization": f"Bearer {WA_ACCESS_TOKEN}"},
            )
            if r1.status_code != 200:
                print(f"[download_media] step1 HTTP {r1.status_code}: {r1.text[:300]}")
                return None
            url = (r1.json() or {}).get("url")
            if not url:
                return None
            r2 = await client.get(url, headers={"Authorization": f"Bearer {WA_ACCESS_TOKEN}"})
            if r2.status_code != 200:
                print(f"[download_media] step2 HTTP {r2.status_code}")
                return None
            return r2.content
    except Exception as e:
        print(f"[download_media err] {e}")
        return None


async def upload_media_to_meta(image_bytes: bytes, filename: str = "product.jpg") -> Optional[str]:
    """Sube binario a /PHONE_ID/media y devuelve el media_id."""
    url = f"{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}"}
    files = {
        "file": (filename, image_bytes, "image/jpeg"),
    }
    data = {"messaging_product": "whatsapp", "type": "image/jpeg"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, data=data, files=files)
        if r.status_code != 200:
            print(f"[Meta upload] HTTP {r.status_code}: {r.text[:300]}")
            return None
        try:
            return r.json().get("id")
        except Exception:
            return None


def _get_product_image(codigo: str) -> Optional[tuple[str, bytes]]:
    """Busca producto por default_code en Odoo y devuelve (nombre, bytes_jpeg). Compat."""
    info = _get_product_full(codigo)
    if not info or not info.get("image_bytes"):
        return None
    return (info["name"], info["image_bytes"])


def _get_product_full(codigo: str) -> Optional[dict]:
    """Busca producto por default_code en Odoo y devuelve dict completo:
    {name, price_crc, image_bytes, weight_kg, website_url}.
    """
    uid = odoo_authenticate()
    if not uid:
        return None
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        rows = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "product.template", "search_read",
            [[("default_code", "=", codigo)]],
            {"fields": ["id", "name", "image_1920", "list_price", "weight", "qty_available", "type", "allow_out_of_stock_order"], "limit": 1},
        )
        if not rows:
            return None
        p = rows[0]
        img_b64 = p.get("image_1920")
        return {
            "id": p.get("id"),
            "name": p.get("name") or "",
            "price_crc": float(p.get("list_price") or 0),
            "image_bytes": base64.b64decode(img_b64) if img_b64 else None,
            "weight_kg": float(p.get("weight") or 0) or None,
            "stock": int(p.get("qty_available") or 0),
            "disponible": _es_disponible(p),
            # URL de búsqueda por código (siempre funciona, sin depender del slug Odoo)
            "website_url": f"https://paracarpinteros.com/shop?search={codigo}",
        }
    except Exception as e:
        print(f"[get_product_full err] {e}")
        return None


def _normalize_phone_digits(phone: str) -> str:
    """Solo dígitos. Para CR, esperamos 506xxxxxxxx (11 chars) o xxxxxxxx (8)."""
    return "".join(c for c in (phone or "") if c.isdigit())


def odoo_resolve_partner(phone: str, name: Optional[str] = None) -> Optional[dict]:
    """
    Busca un res.partner por teléfono en Odoo. Si no existe, lo crea.
    Devuelve dict {id, name, email, is_existing} o None si falla.
    Estrategia de búsqueda: por los últimos 8 dígitos (parte significativa para CR).
    """
    uid = odoo_authenticate()
    if not uid:
        return None
    digits = _normalize_phone_digits(phone)
    if len(digits) < 8:
        return None
    last8 = digits[-8:]

    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        # Buscar por phone que contenga los últimos 8 dígitos (Odoo 19 consolidó mobile en phone)
        domain = [
            "|",
            ("phone", "ilike", last8),
            ("phone", "ilike", digits),
        ]
        ids = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "res.partner", "search",
            [domain],
            {"limit": 1, "order": "customer_rank desc, id desc"},
        )
        if ids:
            row = models.execute_kw(
                ODOO_DB, uid, ODOO_API_KEY,
                "res.partner", "read",
                [ids],
                {"fields": ["name", "email", "phone"]},
            )
            p = row[0] if row else {}
            return {
                "id": ids[0],
                "name": p.get("name") or "",
                "email": p.get("email") or "",
                "phone": p.get("phone") or "",
                "is_existing": True,
            }

        # No existe → crear
        phone_e164 = f"+{digits}" if digits else (phone or "")
        partner_data = {
            "name": (name or "").strip() or f"WhatsApp {phone_e164}",
            "phone": phone_e164,
            "customer_rank": 1,
            "comment": f"Creado por WhatsApp Bot — {dt.datetime.utcnow().isoformat()}Z",
        }
        # País Costa Rica si lo encuentra
        try:
            cr_ids = models.execute_kw(
                ODOO_DB, uid, ODOO_API_KEY,
                "res.country", "search",
                [[("code", "=", "CR")]],
                {"limit": 1},
            )
            if cr_ids:
                partner_data["country_id"] = cr_ids[0]
        except Exception:
            pass
        new_id = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "res.partner", "create",
            [partner_data],
        )
        return {
            "id": new_id,
            "name": partner_data["name"],
            "email": "",
            "phone": phone_e164,
            "is_existing": False,
        }
    except Exception as e:
        print(f"[odoo_resolve_partner err] {e}")
        global _odoo_uid_cache
        _odoo_uid_cache = None
        return None


def _carrier_id_by_short(short: str) -> Optional[int]:
    """Mapea 'Pymex'/'EncomCR'/'Tavo'/'Dual'/'Retiro' al delivery.carrier.id."""
    if not short:
        return None
    for c in SHIPPING_CARRIERS:
        if c["short"].lower() == short.lower():
            return c["id"]
    return None


def create_quotation_odoo(
    partner_id: int,
    items: list,
    note: str = "",
    envio_carrier_short: Optional[str] = None,
    envio_precio_crc: Optional[float] = None,
) -> dict:
    """Crea sale.order en draft con líneas + (opcional) línea de envío con el carrier elegido.
    envio_carrier_short: 'Pymex'/'EncomCR'/'Tavo'/'Dual'/'Retiro'.
    envio_precio_crc: precio del envío en colones (lo calcula `calculate_shipping_quote`).
    """
    if not partner_id:
        return {"ok": False, "error": "Sin partner_id"}
    if not items:
        return {"ok": False, "error": "Sin items"}
    uid = odoo_authenticate()
    if not uid:
        return {"ok": False, "error": "Odoo no disponible"}
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        order_lines = []
        failed = []
        resolved = []
        for it in items:
            code = (it.get("codigo") or "").strip()
            try:
                qty = float(it.get("cantidad") or 1)
            except Exception:
                qty = 1.0
            if not code or qty <= 0:
                failed.append({"codigo": code, "error": "Código vacío o cantidad <= 0"})
                continue
            prod_ids = models.execute_kw(
                ODOO_DB, uid, ODOO_API_KEY,
                "product.product", "search",
                [[("default_code", "=", code)]],
                {"limit": 1},
            )
            if not prod_ids:
                failed.append({"codigo": code, "error": "Producto no encontrado"})
                continue
            order_lines.append((0, 0, {
                "product_id": prod_ids[0],
                "product_uom_qty": qty,
            }))
            resolved.append({"codigo": code, "cantidad": qty})

        if not order_lines:
            return {"ok": False, "error": "Ningún producto válido", "failed": failed}

        # Línea de envío opcional — sólo si Claude pasó carrier+precio
        envio_info = None
        if envio_carrier_short and envio_precio_crc is not None and envio_precio_crc > 0:
            cid = _carrier_id_by_short(envio_carrier_short)
            if cid:
                try:
                    carrier_row = models.execute_kw(
                        ODOO_DB, uid, ODOO_API_KEY,
                        "delivery.carrier", "read",
                        [[cid]],
                        {"fields": ["product_id", "name"]},
                    )
                    if carrier_row and carrier_row[0].get("product_id"):
                        carrier_product_id = carrier_row[0]["product_id"][0]
                        carrier_name = carrier_row[0]["name"]
                        order_lines.append((0, 0, {
                            "product_id": carrier_product_id,
                            "product_uom_qty": 1,
                            "price_unit": float(envio_precio_crc),
                            "name": f"Envío · {carrier_name}",
                            "is_delivery": True,
                        }))
                        envio_info = {"carrier": carrier_name, "precio_crc": float(envio_precio_crc)}
                except Exception as e:
                    print(f"[create_quotation envio err] {e}")

        order_data = {"partner_id": partner_id, "order_line": order_lines}
        if note:
            order_data["note"] = note[:1000]

        order_id = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "sale.order", "create",
            [order_data],
        )
        rows = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "sale.order", "read",
            [[order_id]],
            {"fields": ["name", "amount_total", "amount_untaxed"]},
        )
        return {
            "ok": True,
            "order_id": order_id,
            "order_name": rows[0]["name"] if rows else f"#{order_id}",
            "total_crc": int(round(rows[0]["amount_total"])) if rows else 0,
            "lines_count": len(order_lines),
            "lines_resolved": resolved,
            "lines_failed": failed,
            "envio": envio_info,
            "url": f"{ODOO_URL}/odoo/sales/{order_id}",
        }
    except Exception as e:
        print(f"[create_quotation err] {e}")
        global _odoo_uid_cache
        _odoo_uid_cache = None
        return {"ok": False, "error": str(e)[:200]}


# Cache simple de state_id por provincia (Costa Rica). Se llena perezosamente.
_CR_STATE_CACHE: dict = {}


def _resolve_cr_state(province: str) -> Optional[int]:
    """Busca el res.country.state id de Costa Rica para una provincia dada."""
    if not province:
        return None
    key = province.lower().strip()
    if key in _CR_STATE_CACHE:
        return _CR_STATE_CACHE[key]
    uid = odoo_authenticate()
    if not uid:
        return None
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        states = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "res.country.state", "search_read",
            [[("country_id.code", "=", "CR"), ("name", "ilike", province)]],
            {"fields": ["id", "name"], "limit": 1},
        )
        sid = states[0]["id"] if states else None
        _CR_STATE_CACHE[key] = sid
        return sid
    except Exception as e:
        print(f"[resolve state err] {e}")
        return None


def update_partner_info_odoo(
    partner_id: int,
    name: Optional[str] = None,
    email: Optional[str] = None,
    street: Optional[str] = None,
    city: Optional[str] = None,
    province: Optional[str] = None,
    vat: Optional[str] = None,
) -> dict:
    """Actualiza datos del cliente en Odoo. Solo escribe los campos no-vacíos."""
    if not partner_id:
        return {"ok": False, "error": "Sin partner_id"}
    update = {}
    if name and name.strip():
        update["name"] = name.strip()[:120]
    if email and email.strip():
        # Validación mínima de formato
        em = email.strip().lower()
        if "@" in em and "." in em.split("@")[-1] and len(em) <= 120:
            update["email"] = em
    if street and street.strip():
        update["street"] = street.strip()[:200]
    if city and city.strip():
        update["city"] = city.strip()[:80]
    if vat and vat.strip():
        update["vat"] = vat.strip()[:30]
    if province and province.strip():
        sid = _resolve_cr_state(province.strip())
        if sid:
            update["state_id"] = sid
    if not update:
        return {"ok": False, "error": "No hay campos válidos para actualizar"}
    uid = odoo_authenticate()
    if not uid:
        return {"ok": False, "error": "Odoo auth fallida"}
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "res.partner", "write", [[partner_id], update]
        )
        print(f"[update_partner] id={partner_id} fields={list(update.keys())}")
        return {"ok": True, "partner_id": partner_id, "updated_fields": list(update.keys())}
    except Exception as e:
        print(f"[update_partner err] {e}")
        return {"ok": False, "error": str(e)[:200]}


async def send_product_photo(phone: str, codigo: str) -> dict:
    """Envía al cliente una tarjeta visual del producto (foto + nombre + precio + Ref + botón "Ver detalles")
    como mensaje interactive WhatsApp. Si la generación de card falla, fallback a foto plana.
    Devuelve dict para tool_result."""
    if not phone or not codigo:
        return {"sent": False, "error": "Faltan datos"}
    info = _get_product_full(codigo)
    if not info:
        return {"sent": False, "error": f"Producto {codigo} no encontrado en Odoo"}
    if not info.get("image_bytes"):
        return {"sent": False, "error": f"Producto {codigo} no tiene foto cargada en Odoo"}

    name = info["name"]
    price = info["price_crc"]
    website_url = info["website_url"]
    raw_image = info["image_bytes"]

    # 1. Intentar generar la card visual
    card_bytes = None
    try:
        from product_card import generate_card_bytes
        card_bytes = await asyncio.to_thread(
            generate_card_bytes, raw_image, codigo, name, price
        )
    except Exception as e:
        print(f"[product_card gen err] codigo={codigo} {e}")
        card_bytes = None

    # 2. Si la card está OK, intentar enviar como interactive cta_url
    if card_bytes:
        media_id = await upload_media_to_meta(card_bytes, filename=f"{codigo}_card.png")
        if media_id:
            body = f"{name}"  # nombre del producto (la imagen ya muestra precio + ref)
            footer = f"Ref: {codigo}"
            resp = await send_wa_interactive_cta(
                phone,
                header_image_media_id=media_id,
                body_text=body,
                footer_text=footer,
                cta_label="Ver detalles",
                cta_url=website_url,
            )
            if "messages" in resp and resp.get("messages"):
                fname = f"{codigo}_card_{now_ts()}.png"
                try:
                    with open(f"/opt/whatsapp-bot/data/media/{fname}", "wb") as f:
                        f.write(card_bytes)
                except Exception as e:
                    print(f"[save card err] {e}")
                    fname = None
                _save_outbound(
                    phone,
                    f"[CARD] {codigo} — {name} · ₡{int(price):,} · → {website_url}",
                    bot=True,
                    media_path=fname,
                    wa_msg_id=(resp.get("messages")[0] or {}).get("id"),
                )
                return {"sent": True, "type": "card", "codigo": codigo, "nombre": name,
                        "stock_disponible": info.get("disponible", True),
                        "aviso_sin_stock": None if info.get("disponible", True) else "Avisar al cliente que no hay stock disponible."}
            # Si fallo el envío interactive, caemos al fallback de foto plana
            print(f"[card interactive failed, fallback to plain photo] resp={str(resp)[:200]}")

    # 3. Fallback: foto plana de Odoo con caption
    media_id = await upload_media_to_meta(raw_image, filename=f"{codigo}.jpg")
    if not media_id:
        return {"sent": False, "error": "No se pudo subir imagen a WhatsApp"}
    caption = f"{codigo} — {name}"[:1024]
    resp = await send_wa_image_by_id(phone, media_id, caption=caption)
    if "messages" in resp and resp.get("messages"):
        fname = f"{codigo}_{now_ts()}.jpg"
        try:
            with open(f"/opt/whatsapp-bot/data/media/{fname}", "wb") as f:
                f.write(raw_image)
        except Exception as e:
            print(f"[save media err] {e}")
            fname = None
        _save_outbound(phone, f"[FOTO] {caption}", bot=True, media_path=fname, wa_msg_id=(resp.get("messages")[0] or {}).get("id"))
        return {"sent": True, "type": "photo", "codigo": codigo, "nombre": name,
                "stock_disponible": info.get("disponible", True),
                "aviso_sin_stock": None if info.get("disponible", True) else "Avisar al cliente que no hay stock disponible."}
    return {"sent": False, "error": str(resp)[:200]}

OUT_OF_HOURS_MSG = (
    f"Gracias por escribir a Paracarpinteros. "
    f"Atendemos de lunes a viernes de {BIZ_HOUR_START}am a {BIZ_HOUR_END}h hora Costa Rica. "
    f"Te respondemos apenas volvamos al horario."
)

SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 días


# ───────── DB ─────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                phone TEXT PRIMARY KEY,
                name TEXT,
                first_seen INTEGER,
                last_seen INTEGER,
                last_message_preview TEXT,
                escalated INTEGER DEFAULT 0,
                unread INTEGER DEFAULT 0,
                odoo_partner_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                direction TEXT NOT NULL,
                text TEXT,
                ts INTEGER NOT NULL,
                wa_msg_id TEXT,
                bot_replied INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_messages_phone_ts ON messages(phone, ts);
        """)
        # Migration: agregar columnas si la tabla vino de versión anterior
        cols = [r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
        if "odoo_partner_id" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN odoo_partner_id INTEGER")
        if "status" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN status TEXT DEFAULT 'nuevo'")
        if "payment_meta" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN payment_meta TEXT")
        if "odoo_sale_order_id" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN odoo_sale_order_id INTEGER")
        if "odoo_sale_order_name" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN odoo_sale_order_name TEXT")
        mcols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
        if "media_path" not in mcols:
            conn.execute("ALTER TABLE messages ADD COLUMN media_path TEXT")
        if "status" not in mcols:
            # sent / delivered / read / failed (solo aplica a salientes)
            conn.execute("ALTER TABLE messages ADD COLUMN status TEXT")
        # Índice para localizar rápido el mensaje al recibir un status webhook
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_wa_msg_id ON messages(wa_msg_id)")
        # Tabla de sesiones persistentes (30 días)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)
        # Tabla genérica de settings de la app (modo del bot, flags, etc.)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at INTEGER NOT NULL
            )
        """)
        # Insertar default del modo de respuesta si no existe
        conn.execute("""
            INSERT OR IGNORE INTO app_settings (key, value, updated_at)
            VALUES ('bot_reply_mode', 'normal', ?)
        """, (int(dt.datetime.now(dt.timezone.utc).timestamp()),))
        # Tabla de subscripciones Web Push (PWA del panel)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                ua TEXT,
                created_at INTEGER NOT NULL,
                last_seen_at INTEGER
            )
        """)
        # Tabla de knowledge base del bot
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'general',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        # Seed de datos iniciales si la tabla está vacía
        cnt = conn.execute("SELECT COUNT(*) FROM bot_knowledge").fetchone()[0]
        if cnt == 0:
            now = int(dt.datetime.now(dt.timezone.utc).timestamp())
            seed = [
                ("empresa", "Sobre Paracarpinteros",
                 "Paracarpinteros (también conocida como 'La Juguetería para Carpinteros') es una empresa de Costa Rica especializada en herramientas y suministros para carpinteros y trabajos de carpintería. "
                 "IMPORTANTE: NO tenemos local físico de venta al público. Solo trabajamos online + envío a domicilio en todo el país. "
                 "Si un cliente pregunta por nuestra dirección o si puede ir a comprar, decile que no tenemos local de visita, solo trabajamos por pedido + envío. "
                 "Base de operaciones: Turrialba, Cartago."),
                ("ubicacion", "Ubicación y contacto",
                 "Base operativa: Alto Cruz A22, Santa Cruz, Turrialba, Cartago. CP 30504. "
                 "Tel/WhatsApp: 8606-9717. Email: info@paracarpinteros.com. Web: www.paracarpinteros.com. "
                 "NO atendemos público presencial — los envíos se preparan y se despachan desde nuestra base, sin atención de visita."),
                ("horarios", "Horarios de atención",
                 "Atendemos consultas por WhatsApp y web de Lunes a Viernes de 8am a 6pm hora Costa Rica. "
                 "Fuera de ese horario, el bot responde con mensaje automático y un compañero contesta al volver a horario. "
                 "Sábados y domingos no atendemos consultas."),
                ("envios", "Métodos de envío y entrega",
                 "Hacemos envíos a todo Costa Rica. Métodos disponibles según destino y peso: "
                 "Pymexpress (Correos CR), Encomienda Nacional Correos CR, Envío Transtusa, Dual Global, Envío Encomienda Regional, Mensajería privada. "
                 "También opción de retirada en almacén Santa Cruz, Turrialba (sin costo de envío). "
                 "El precio del envío se cobra junto con el producto antes de despachar (NO cobramos envío contra entrega)."),
                ("pagos", "Métodos de pago",
                 "Aceptamos SINPE Móvil al 8606-9717 (a nombre de Gabriela Brenes Solano), transferencia bancaria BCR/BNCR, y depósito. "
                 "El cliente debe enviar foto del comprobante por WhatsApp para confirmar pago. "
                 "Una vez confirmado el pago COMPLETO (producto + envío), preparamos el despacho. "
                 "No despachamos pedidos pendientes de pago."),
                ("productos", "Catálogo y productos",
                 "Nuestro catálogo está en Odoo. El bot tiene acceso vía herramienta `search_products` para consultar productos reales, precios y disponibilidad. "
                 "Si un cliente busca algo específico, siempre usá la herramienta de búsqueda en lugar de inventar respuestas. "
                 "Especialidad: herramientas para carpintería (brocas, avellanadores, bisagras, sierras, tornillos, accesorios)."),
            ]
            for i, (cat, ttl, cnt_text) in enumerate(seed):
                conn.execute("""
                    INSERT INTO bot_knowledge (category, title, content, active, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?, ?)
                """, (cat, ttl, cnt_text, i, now, now))
    # Crear dir para fotos persistidas
    os.makedirs("/opt/whatsapp-bot/data/media", exist_ok=True)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ───────── HELPERS ─────────
def is_business_hours() -> bool:
    """Costa Rica = UTC-6 (sin DST)."""
    cr_now = dt.datetime.utcnow() - dt.timedelta(hours=6)
    # Lunes=0..Domingo=6
    if cr_now.weekday() >= 5 and not BIZ_WEEKENDS_OPEN:  # sábado o domingo
        return False
    return BIZ_HOUR_START <= cr_now.hour < BIZ_HOUR_END


def now_ts() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp())


# Estados del tablero (orden importa para mostrar tabs)
STATUS_ORDER = ["nuevo", "en_conversacion", "cotizado", "pagado", "a_despachar", "cerrado"]
STATUS_LABELS = {
    "nuevo": "🆕 Nuevos",
    "en_conversacion": "💬 En conversación",
    "cotizado": "📋 Cotizado",
    "pagado": "💰 Pagado",
    "a_despachar": "📦 A despachar",
    "cerrado": "✅ Cerrado",
}
# Transiciones automáticas permitidas (origen → destino)
# Nunca degradar manualmente. Si está 'pagado' y llega un mensaje, no volver a 'en_conversacion'.
_AUTO_ADVANCE = {
    None: "nuevo",
    "": "nuevo",
    "cerrado": "en_conversacion",
    "nuevo": "en_conversacion",
}


def _payments_list(payment_meta_json: Optional[str]) -> list[dict]:
    """Devuelve la lista de pagos. Soporta formato antiguo (dict suelto) y nuevo (lista)."""
    if not payment_meta_json:
        return []
    try:
        v = json.loads(payment_meta_json)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return [v]
    except Exception:
        pass
    return []


def _add_payment(phone: str, monto: float, metodo: str, banco: str, referencia: str, fecha: str):
    """Acumula un pago en payment_meta (lista). Mantiene histórico."""
    with db() as conn:
        row = conn.execute("SELECT payment_meta FROM conversations WHERE phone=?", (phone,)).fetchone()
        existing = _payments_list(row["payment_meta"]) if row else []
        existing.append({
            "monto_crc": float(monto or 0),
            "metodo": metodo or "otro",
            "banco": banco or "",
            "referencia": referencia or "",
            "fecha": fecha or "",
            "ts": now_ts(),
        })
        conn.execute(
            "UPDATE conversations SET payment_meta=? WHERE phone=?",
            (json.dumps(existing, ensure_ascii=False), phone),
        )


def _total_paid(payment_meta_json: Optional[str]) -> float:
    return sum(float(p.get("monto_crc") or 0) for p in _payments_list(payment_meta_json))


def _set_status(phone: str, new_status: str, force: bool = False):
    """Cambiar status. Sin force, respeta la jerarquía (no retrocede en el flujo)."""
    if new_status not in STATUS_ORDER:
        return
    with db() as conn:
        cur = conn.execute("SELECT status FROM conversations WHERE phone=?", (phone,)).fetchone()
        if not cur:
            return
        current = cur["status"] or "nuevo"
        if not force:
            try:
                if STATUS_ORDER.index(new_status) <= STATUS_ORDER.index(current) and current != "cerrado":
                    return  # no retroceder
            except ValueError:
                pass
        conn.execute("UPDATE conversations SET status=? WHERE phone=?", (new_status, phone))


def _save_outbound(phone: str, text: str, bot: bool = True, media_path: Optional[str] = None, wa_msg_id: Optional[str] = None):
    """Persistir un mensaje saliente en la DB (texto o '[FOTO] xxx'). media_path es nombre de archivo en data/media/.
    Si se pasa wa_msg_id, se marca status='sent' para soportar el seguimiento de check/doble-check vía webhook."""
    status = "sent" if wa_msg_id else None
    with db() as conn:
        conn.execute("""
            INSERT INTO messages(phone, direction, text, ts, bot_replied, media_path, wa_msg_id, status)
            VALUES (?, 'out', ?, ?, ?, ?, ?, ?)
        """, (phone, text, now_ts(), 1 if bot else 0, media_path, wa_msg_id, status))
        conn.execute("""
            UPDATE conversations SET last_seen=?, last_message_preview=?
            WHERE phone=?
        """, (now_ts(), text[:200], phone))


async def send_wa_message(to: str, text: str) -> dict:
    url = f"{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text[:4096]},  # WA hard limit
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, json=payload, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"status_code": r.status_code, "text": r.text[:500]}
        # Log errores Meta (token expirado, número bloqueado, etc.)
        if "error" in data:
            err = data["error"]
            code = err.get("code")
            msg = err.get("message", "")
            print(f"[WA SEND ERROR] code={code} msg={msg!r} to={to}")
            if code == 190:
                print("[WA SEND ERROR] !!! TOKEN EXPIRADO O INVÁLIDO — regenerar WA_ACCESS_TOKEN en Meta")
        return data


# ───────── WEB PUSH ─────────
def _send_push_sync(subs: list[dict], payload: str) -> tuple[int, list[int]]:
    """Envía un push a cada subscripción (síncrono — usado vía to_thread).
    Devuelve (enviados_ok, ids_a_borrar)."""
    sent = 0
    stale: list[int] = []
    for s in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": s["endpoint"],
                    "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                ttl=60,
            )
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            print(f"[push send] sub={s['id']} http={code} err={str(e)[:160]}")
            if code in (404, 410):
                stale.append(s["id"])
        except Exception as e:
            print(f"[push send] sub={s['id']} unexpected err={e!r}")
    return sent, stale


async def send_push_notification(title: str, body: str, data: Optional[dict] = None) -> int:
    """Envía notificación push a todas las subscripciones registradas. Devuelve cuántas se enviaron OK."""
    if not (PYWEBPUSH_AVAILABLE and VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY):
        return 0
    payload = json.dumps({"title": title, "body": body, "data": data or {}})
    with db() as conn:
        subs = [dict(r) for r in conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions"
        ).fetchall()]
    if not subs:
        return 0
    try:
        sent, stale = await asyncio.to_thread(_send_push_sync, subs, payload)
    except Exception as e:
        print(f"[push] to_thread err={e!r}")
        return 0
    if stale:
        with db() as conn:
            conn.executemany("DELETE FROM push_subscriptions WHERE id=?", [(i,) for i in stale])
        print(f"[push] purged {len(stale)} stale subscriptions")
    if sent:
        print(f"[push] sent={sent}/{len(subs)} title={title!r}")
    return sent


PURCHASE_INTENT_PATTERNS = [
    "quiero", "lo llevo", "los llevo", "me lo llevo", "me los llevo",
    "envíame", "enviame", "envíamelo", "enviamelo", "envialo",
    "hacé el pedido", "haz el pedido", "hace el pedido", "hace pedido",
    "reservame", "reservámelo", "reservamelo", "reservalo",
    "agendá", "agendamelo", "agenda esos", "agendalos", "agendame",
    "comprar", "compralo", "comprame", "comprármelo",
    "lo compro", "los compro", "me llevo",
]


def detect_purchase_intent(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in PURCHASE_INTENT_PATTERNS)


def _knowledge_block() -> str:
    """Lee el knowledge base activo y lo formatea como bloque del system prompt."""
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT category, title, content FROM bot_knowledge WHERE active=1 ORDER BY sort_order, id"
            ).fetchall()
        if not rows:
            return ""
        lines = ["", "═══ INFORMACIÓN OFICIAL DE LA EMPRESA (usá esto siempre, NUNCA inventes datos contradictorios) ═══", ""]
        for r in rows:
            lines.append(f"## {r['title']} [{r['category']}]")
            lines.append(r["content"])
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        print(f"[knowledge_block err] {e}")
        return ""


async def ai_reply(history: list[dict], user_text: str, phone: str = "", image_b64: Optional[str] = None, bot_mode: str = "normal") -> str:
    msgs = []
    # Construir histórico alternado user/assistant (limitado a 4 turnos: balance entre contexto y costo/alucinaciones)
    last_role = None
    for h in history[-4:]:
        role = "user" if h["direction"] == "in" else "assistant"
        if not h.get("text"):
            continue
        if role == last_role and msgs:
            # Concatenar al último para evitar dos consecutivos del mismo rol
            msgs[-1]["content"] += "\n" + h["text"]
        else:
            msgs.append({"role": role, "content": h["text"]})
            last_role = role
    # Asegurar que termine con user (el actual)
    if msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n" + user_text
    else:
        msgs.append({"role": "user", "content": user_text})

    # Si hay imagen del cliente, convertir el último user message a content array con la imagen
    if image_b64:
        last = msgs[-1]
        text_part = last["content"] if isinstance(last["content"], str) else user_text
        last["content"] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
            },
            {
                "type": "text",
                "text": (text_part or "") + "\n\n[El cliente envió esta foto. Identificá qué producto es y usá `search_products` con palabras clave de lo que ves para mostrarle opciones del catálogo.]",
            },
        ]

    # Modo conservador: restringe tools y respuestas. Sin create_quotation ni mark_payment_received.
    # calculate_shipping_quote sí permitido (solo lectura, ayuda al cliente).
    # send_product_photo solo si cliente lo pide explícitamente (lo refuerza el prompt extra).
    if bot_mode == "conservative":
        active_tools = [t for t in CLAUDE_TOOLS if t.get("name") in ("search_products", "send_product_photo", "calculate_shipping_quote", "find_dual_agency", "update_partner_info")]
        conservative_note = (
            "\n\n[MODO CONSERVADOR ACTIVO] Restricciones de este turno:\n"
            "- NO crees cotizaciones bajo ninguna circunstancia. Si el cliente pide comprar, "
            "respondé 'Un compañero te confirma enseguida los detalles del pedido y la cotización' (sin S0####).\n"
            "- NO mandes fotos a menos que el cliente las pida con 'foto', 'imagen', 'pantallazo', 'mostrame'.\n"
            "- Respuestas breves: máximo 2 oraciones cuando sea posible.\n"
            "- Para pagos: NO uses mark_payment_received. Si ves un comprobante, decí 'Recibido, un compañero confirma el pago enseguida'."
        )
        system_prompt_final = SYSTEM_PROMPT + _knowledge_block() + conservative_note
        max_tokens_final = 350
    else:
        active_tools = CLAUDE_TOOLS
        system_prompt_final = SYSTEM_PROMPT + _knowledge_block()
        max_tokens_final = 600

    # Prompt caching (TTL 5min, descuento ~90% en cache hits)
    # Cacheamos: 1) system prompt + knowledge, 2) tools, 3) último user message del cliente (para reuso si vuelve a escribir en <5min)
    system_blocks = [{
        "type": "text",
        "text": system_prompt_final,
        "cache_control": {"type": "ephemeral"},
    }]
    # Marcar la última tool con cache_control: eso cachea TODO el bloque de tools (Anthropic cachea acumulativamente hasta el marker)
    tools_cached = []
    for i, t in enumerate(active_tools):
        if i == len(active_tools) - 1:
            tools_cached.append({**t, "cache_control": {"type": "ephemeral"}})
        else:
            tools_cached.append(t)
    # Marcar el último mensaje del cliente con cache_control: cuando el cliente vuelva a escribir en <5min,
    # toda la conversación previa se reutiliza desde cache (ahorro fuerte en conversaciones activas)
    msgs_cached = list(msgs)
    if msgs_cached and msgs_cached[-1].get("role") == "user":
        last = msgs_cached[-1]
        content = last.get("content")
        if isinstance(content, str):
            msgs_cached[-1] = {
                "role": "user",
                "content": [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}],
            }
        elif isinstance(content, list) and content:
            # Ya es array (caso de imagen + texto). Marcamos el último bloque con cache_control.
            new_content = []
            for j, block in enumerate(content):
                if j == len(content) - 1 and isinstance(block, dict):
                    new_content.append({**block, "cache_control": {"type": "ephemeral"}})
                else:
                    new_content.append(block)
            msgs_cached[-1] = {"role": "user", "content": new_content}

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens_final,
        "system": system_blocks,
        "messages": msgs_cached,
        "tools": tools_cached,
    }
    # Si el último mensaje del cliente tiene intención de compra, forzamos uso de alguna tool.
    # En modo conservador esto NO aplica (no queremos forzar create_quotation que ni siquiera está en tools).
    has_intent = detect_purchase_intent(user_text)
    if has_intent and bot_mode == "normal":
        payload["tool_choice"] = {"type": "any"}
        print(f"[ai_reply] forzando tool_choice=any por intent en: {user_text[:80]!r}")
    if bot_mode != "normal":
        print(f"[ai_reply] mode={bot_mode} tools={[t['name'] for t in active_tools]} max_tokens={max_tokens_final}")

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    print(f"[ai_reply] phone={phone} text={user_text[:80]!r}")
    # Tool loop: el modelo puede pedir search_products y devolvemos resultados
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(5):  # límite de iteraciones
            r = await client.post(CLAUDE_API, json=payload, headers=headers)
            if r.status_code != 200:
                print(f"[Claude] HTTP {r.status_code}: {r.text[:400]}")
                return "Disculpá, tuve un problema técnico. Un humano te va a contestar en breve."
            d = r.json()
            stop = d.get("stop_reason")
            print(f"[ai_reply] iter={i} stop_reason={stop}")
            for block in d.get("content", []):
                btype = block.get("type", "?")
                if btype == "tool_use":
                    print(f"[ai_reply]   tool_use {block.get('name')} input={json.dumps(block.get('input', {}), ensure_ascii=False)[:300]}")
                elif btype == "text":
                    print(f"[ai_reply]   text: {(block.get('text') or '')[:120]!r}")

            if stop == "tool_use":
                # Procesar todas las tool calls del turno y reinyectar resultados
                tool_results = []
                for block in d.get("content", []):
                    if block.get("type") != "tool_use":
                        continue
                    tool_name = block.get("name")
                    tool_input = block.get("input") or {}
                    tool_id = block.get("id")
                    if tool_name == "search_products":
                        results = search_products_odoo(tool_input.get("query", ""))
                        result_text = json.dumps(results, ensure_ascii=False)
                    elif tool_name == "calculate_shipping_quote":
                        weight_kg = float(tool_input.get("weight_kg") or 0)
                        prov = (tool_input.get("provincia") or "").strip() or None
                        result = calculate_shipping_quote_odoo(weight_kg, provincia=prov)
                        result_text = json.dumps(result, ensure_ascii=False)
                        print(f"[shipping quote] phone={phone} kg={weight_kg} prov={prov!r} → {len(result.get('quotes', []))} carriers")
                    elif tool_name == "find_dual_agency":
                        prov = (tool_input.get("provincia") or "").strip()
                        cant = (tool_input.get("canton") or "").strip()
                        result = find_dual_agencies(prov, cant)
                        result_text = json.dumps({"provincia": prov, "canton": cant, "agencias": result}, ensure_ascii=False)
                        print(f"[dual] phone={phone} prov={prov!r} canton={cant!r} → {len(result)} agencias")
                    elif tool_name == "send_product_photo":
                        codigo = (tool_input.get("codigo") or "").strip()
                        result = await send_product_photo(phone, codigo)
                        result_text = json.dumps(result, ensure_ascii=False)
                    elif tool_name == "mark_payment_received":
                        monto = float(tool_input.get("monto_crc") or 0)
                        metodo = tool_input.get("metodo", "otro")
                        referencia = tool_input.get("referencia", "") or ""
                        banco = tool_input.get("banco", "") or ""
                        fecha = tool_input.get("fecha", "") or ""
                        try:
                            _add_payment(phone, monto, metodo, banco, referencia, fecha)
                            _set_status(phone, "pagado", force=True)
                        except Exception as e:
                            print(f"[mark_payment err] {e}")
                        event_text = (
                            f"💰 PAGO RECIBIDO: ₡{int(monto):,} via {metodo.upper()}"
                            + (f" · {banco}" if banco else "")
                            + (f" · ref {referencia}" if referencia else "")
                        )
                        _save_outbound(phone, event_text, bot=True)
                        print(f"[payment] phone={phone} monto={monto} metodo={metodo}")
                        result_text = json.dumps({"ok": True, "registered": True, "monto_crc": monto, "metodo": metodo, "banco": banco, "referencia": referencia, "fecha": fecha}, ensure_ascii=False)
                    elif tool_name == "create_quotation":
                        # Obtener partner_id de la conversación (resuelto en el webhook)
                        partner_id = None
                        try:
                            with db() as conn:
                                row = conn.execute(
                                    "SELECT odoo_partner_id FROM conversations WHERE phone=?",
                                    (phone,)
                                ).fetchone()
                                if row:
                                    partner_id = row["odoo_partner_id"]
                        except Exception:
                            pass
                        # Fallback: resolver ad-hoc si todavía no estaba cacheado
                        if not partner_id:
                            p = odoo_resolve_partner(phone)
                            if p and p.get("id"):
                                partner_id = p["id"]
                                try:
                                    with db() as conn:
                                        conn.execute(
                                            "UPDATE conversations SET odoo_partner_id=? WHERE phone=?",
                                            (partner_id, phone)
                                        )
                                except Exception:
                                    pass
                        if not partner_id:
                            result_text = json.dumps({"ok": False, "error": "No se pudo identificar el cliente en Odoo"})
                        else:
                            items = tool_input.get("items", []) or []
                            note = tool_input.get("nota", "") or ""
                            envio_short = tool_input.get("envio_carrier_short")
                            envio_precio = tool_input.get("envio_precio_crc")
                            result = create_quotation_odoo(
                                partner_id, items, note,
                                envio_carrier_short=envio_short,
                                envio_precio_crc=envio_precio,
                            )
                            result_text = json.dumps(result, ensure_ascii=False)
                            # Persistir evento en la DB para que se vea en el panel
                            if result.get("ok"):
                                envio_str = ""
                                if result.get("envio"):
                                    envio_str = f" + envío {result['envio']['carrier']} ₡{int(result['envio']['precio_crc']):,}"
                                event_text = (
                                    f"📋 Cotización {result['order_name']} creada · "
                                    f"{result['lines_count']} líneas · "
                                    f"₡{result['total_crc']:,}{envio_str}"
                                )
                                _save_outbound(phone, event_text, bot=True)
                                print(f"[quotation] phone={phone} order={result['order_name']} url={result.get('url')} envio={envio_short}")
                                # Guardar order_id/name + avanzar status
                                try:
                                    with db() as conn:
                                        conn.execute(
                                            "UPDATE conversations SET odoo_sale_order_id=?, odoo_sale_order_name=? WHERE phone=?",
                                            (result.get("order_id"), result.get("order_name"), phone)
                                        )
                                except Exception:
                                    pass
                                _set_status(phone, "cotizado")
                    elif tool_name == "update_partner_info":
                        # Resolver partner_id de la conversación (igual que create_quotation)
                        partner_id = None
                        try:
                            with db() as conn:
                                row = conn.execute(
                                    "SELECT odoo_partner_id FROM conversations WHERE phone=?", (phone,)
                                ).fetchone()
                                if row:
                                    partner_id = row["odoo_partner_id"]
                        except Exception:
                            pass
                        if not partner_id:
                            p = odoo_resolve_partner(phone)
                            if p and p.get("id"):
                                partner_id = p["id"]
                        if not partner_id:
                            result_text = json.dumps({"ok": False, "error": "Cliente no resuelto en Odoo"})
                        else:
                            result = update_partner_info_odoo(
                                partner_id,
                                name=tool_input.get("name"),
                                email=tool_input.get("email"),
                                street=tool_input.get("street"),
                                city=tool_input.get("city"),
                                province=tool_input.get("province"),
                                vat=tool_input.get("vat"),
                            )
                            result_text = json.dumps(result, ensure_ascii=False)
                            if result.get("ok"):
                                fields = ", ".join(result.get("updated_fields", []))
                                _save_outbound(phone, f"👤 Cliente actualizado en Odoo · {fields}", bot=True)
                                print(f"[update_partner] phone={phone} → {fields}")
                    else:
                        result_text = json.dumps({"error": f"Tool {tool_name} no reconocida"})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_text,
                    })
                payload["messages"].append({"role": "assistant", "content": d["content"]})
                payload["messages"].append({"role": "user", "content": tool_results})
                # Después de la primera iteración, dejamos al modelo libre para finalizar en texto
                payload["tool_choice"] = {"type": "auto"}
                continue

            # end_turn → extraer texto final
            for block in d.get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    return block["text"]
            print(f"[Claude] Respuesta sin texto: {d}")
            return "Disculpá, tuve un problema técnico. Un humano te va a contestar en breve."

    return "Disculpá, mi respuesta tardó demasiado. Un humano te va a contestar pronto."


# ───────── APP ─────────
init_db()
app = FastAPI(title="WhatsApp Bot Paracarpinteros")


# ───────── WEBHOOK ─────────
@app.get("/webhook")
async def webhook_verify(request: Request):
    """Handshake de verificación que Meta hace al configurar el webhook."""
    hub_mode = request.query_params.get("hub.mode")
    hub_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge", "")
    if hub_mode == "subscribe" and hub_token == WA_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def webhook_receive(request: Request):
    """Recibe los mensajes entrantes de WhatsApp Cloud API."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "bad_json"}, status_code=200)

    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # ── Status updates de mensajes salientes (sent/delivered/read/failed)
                for st in value.get("statuses", []) or []:
                    wa_id = st.get("id")
                    st_name = (st.get("status") or "").lower()
                    if not wa_id or st_name not in ("sent", "delivered", "read", "failed"):
                        continue
                    # Sólo avanzar hacia "más leído" — no degradar (Meta a veces reenvía).
                    rank = {"sent": 1, "delivered": 2, "read": 3, "failed": 99}
                    try:
                        with db() as conn:
                            cur = conn.execute(
                                "SELECT status FROM messages WHERE wa_msg_id=?", (wa_id,)
                            ).fetchone()
                            if cur is None:
                                continue
                            prev = (cur["status"] or "").lower()
                            if st_name == "failed" or rank.get(st_name, 0) > rank.get(prev, 0):
                                conn.execute(
                                    "UPDATE messages SET status=? WHERE wa_msg_id=?",
                                    (st_name, wa_id)
                                )
                    except Exception as e:
                        print(f"[status webhook err] {e}")

                # Nombre del contacto si viene
                contacts = value.get("contacts", [])
                contact_name = None
                if contacts:
                    contact_name = contacts[0].get("profile", {}).get("name")

                for msg in value.get("messages", []):
                    phone = msg.get("from")
                    if not phone:
                        continue
                    wa_msg_id = msg.get("id")
                    mtype = msg.get("type", "text")
                    image_b64 = None
                    in_media_fname = None
                    if mtype == "text":
                        text = (msg.get("text") or {}).get("body", "")
                    elif mtype == "image":
                        img_info = msg.get("image") or {}
                        caption = img_info.get("caption", "")
                        media_id = img_info.get("id")
                        text = f"[IMAGEN] {caption}".strip()
                        # Descargar imagen entrante de Meta
                        img_bytes = await download_meta_media(media_id) if media_id else None
                        if img_bytes:
                            in_media_fname = f"in_{media_id}_{now_ts()}.jpg"
                            try:
                                with open(f"/opt/whatsapp-bot/data/media/{in_media_fname}", "wb") as f:
                                    f.write(img_bytes)
                            except Exception as e:
                                print(f"[save inbound media err] {e}")
                                in_media_fname = None
                            # Preparar para Vision (Claude soporta hasta ~5MB base64)
                            if len(img_bytes) < 5_000_000:
                                image_b64 = base64.b64encode(img_bytes).decode()
                                print(f"[inbound image] phone={phone} bytes={len(img_bytes)} → Vision activado")
                            else:
                                print(f"[inbound image] phone={phone} bytes={len(img_bytes)} → demasiado grande, sin Vision")
                    elif mtype == "audio":
                        audio_info = msg.get("audio") or {}
                        media_id_a = audio_info.get("id")
                        mime_a = audio_info.get("mime_type", "audio/ogg")
                        text = "[AUDIO]"
                        if media_id_a:
                            audio_bytes = await download_meta_media(media_id_a)
                            if audio_bytes:
                                # Determinar extensión por mime
                                ext = ".ogg"
                                if "mp3" in mime_a or "mpeg" in mime_a: ext = ".mp3"
                                elif "mp4" in mime_a or "m4a" in mime_a: ext = ".m4a"
                                elif "wav" in mime_a: ext = ".wav"
                                in_media_fname = f"in_audio_{media_id_a}_{now_ts()}{ext}"
                                try:
                                    with open(f"/opt/whatsapp-bot/data/media/{in_media_fname}", "wb") as f:
                                        f.write(audio_bytes)
                                except Exception as e:
                                    print(f"[save inbound audio err] {e}")
                                    in_media_fname = None
                                # Transcribir
                                transcript = await transcribe_audio(audio_bytes, mime_a, f"audio{ext}")
                                if transcript:
                                    text = f"🎙️ {transcript}"
                                    print(f"[audio in] phone={phone} bytes={len(audio_bytes)} → transcripción {len(transcript)} chars")
                                else:
                                    text = "[AUDIO] (no se pudo transcribir)"
                    elif mtype == "video":
                        text = "[VIDEO]"
                    elif mtype == "document":
                        fname = (msg.get("document") or {}).get("filename", "")
                        text = f"[DOCUMENTO] {fname}".strip()
                    elif mtype == "location":
                        loc = msg.get("location") or {}
                        text = f"[UBICACIÓN] {loc.get('latitude')}, {loc.get('longitude')}"
                    elif mtype == "interactive":
                        # Botones / listas
                        inter = msg.get("interactive") or {}
                        btn = inter.get("button_reply") or inter.get("list_reply") or {}
                        text = btn.get("title") or btn.get("id") or "[INTERACTIVO]"
                    else:
                        text = f"[{mtype.upper()}]"

                    ts_int = int(msg.get("timestamp") or now_ts())

                    # Persistir mensaje + conversación
                    with db() as conn:
                        conn.execute("""
                            INSERT INTO conversations(phone, name, first_seen, last_seen, last_message_preview, unread)
                            VALUES (?, ?, ?, ?, ?, 1)
                            ON CONFLICT(phone) DO UPDATE SET
                                last_seen = excluded.last_seen,
                                last_message_preview = excluded.last_message_preview,
                                unread = unread + 1,
                                name = COALESCE(?, conversations.name)
                        """, (phone, contact_name, ts_int, ts_int, text[:200], contact_name))
                        conn.execute("""
                            INSERT INTO messages(phone, direction, text, ts, wa_msg_id, media_path)
                            VALUES (?, 'in', ?, ?, ?, ?)
                        """, (phone, text, ts_int, wa_msg_id, in_media_fname))

                    # Transición de estado al recibir mensaje:
                    # - Si no había conversación previa: ya está en 'nuevo' (default)
                    # - Si estaba 'cerrado': pasa a 'en_conversacion'
                    # - Resto: no degradar
                    try:
                        with db() as conn:
                            cur = conn.execute(
                                "SELECT status FROM conversations WHERE phone=?", (phone,)
                            ).fetchone()
                            current = (cur["status"] if cur and cur["status"] else "nuevo")
                            if current == "cerrado":
                                conn.execute(
                                    "UPDATE conversations SET status='en_conversacion' WHERE phone=?",
                                    (phone,)
                                )
                    except Exception as e:
                        print(f"[status transition err] {e}")

                    # Push notification al panel (PWA) — mensaje nuevo entrante
                    try:
                        push_title = contact_name or phone
                        push_body = (text or "[mensaje]")[:140]
                        await send_push_notification(
                            title=push_title,
                            body=push_body,
                            data={"phone": phone, "url": "/", "ts": ts_int},
                        )
                    except Exception as e:
                        print(f"[push trigger err] {e}")

                    # Resolver / crear partner en Odoo si todavía no se hizo
                    try:
                        with db() as conn:
                            cur = conn.execute(
                                "SELECT odoo_partner_id, name FROM conversations WHERE phone=?",
                                (phone,)
                            ).fetchone()
                        if cur and not cur["odoo_partner_id"]:
                            partner = odoo_resolve_partner(phone, contact_name or cur["name"])
                            if partner and partner.get("id"):
                                with db() as conn:
                                    conn.execute(
                                        "UPDATE conversations SET odoo_partner_id=?, name=COALESCE(name, ?) WHERE phone=?",
                                        (partner["id"], partner.get("name") or None, phone)
                                    )
                                print(f"[odoo partner] phone={phone} → id={partner['id']} ({'existing' if partner.get('is_existing') else 'NEW'})")
                    except Exception as e:
                        print(f"[odoo resolve err] {e}")

                    # ¿Escalada? Saltamos auto-reply
                    with db() as conn:
                        row = conn.execute(
                            "SELECT escalated FROM conversations WHERE phone=?", (phone,)
                        ).fetchone()
                        escalated = bool(row["escalated"]) if row else False
                    if escalated:
                        continue

                    # Modo del bot — control de agresividad
                    bot_mode = get_bot_mode()
                    if bot_mode == "escalate_all":
                        # Modo pánico: ninguna respuesta automática, marcar como escalada
                        try:
                            with db() as conn:
                                conn.execute(
                                    "UPDATE conversations SET escalated=1 WHERE phone=?",
                                    (phone,)
                                )
                            print(f"[bot mode] escalate_all → conv {phone} marcada escalada sin responder")
                        except Exception as e:
                            print(f"[escalate_all err] {e}")
                        continue

                    # Fuera de horario → mensaje fijo
                    if not is_business_hours():
                        try:
                            _res = await send_wa_message(phone, OUT_OF_HOURS_MSG)
                            _wid = None
                            if isinstance(_res, dict) and _res.get("messages"):
                                _wid = (_res["messages"][0] or {}).get("id")
                            _save_outbound(phone, OUT_OF_HOURS_MSG, bot=True, wa_msg_id=_wid)
                        except Exception as e:
                            print(f"[out-of-hours send error] {e}")
                        continue

                    # En horario → respuesta Claude (modo normal o conservador)
                    try:
                        with db() as conn:
                            history = [dict(r) for r in conn.execute(
                                "SELECT direction, text FROM messages WHERE phone=? ORDER BY ts DESC LIMIT 6",
                                (phone,)
                            )][::-1]
                        reply = await ai_reply(history, text, phone=phone, image_b64=image_b64, bot_mode=bot_mode)
                        _res = await send_wa_message(phone, reply)
                        _wid = None
                        if isinstance(_res, dict) and _res.get("messages"):
                            _wid = (_res["messages"][0] or {}).get("id")
                        _save_outbound(phone, reply, bot=True, wa_msg_id=_wid)
                        # Si seguía en 'nuevo', el bot ya respondió → avanzar a 'en_conversacion'
                        _set_status(phone, "en_conversacion")
                    except Exception as e:
                        print(f"[AI reply error] {e}")

        return JSONResponse({"status": "ok"})
    except Exception as e:
        print(f"[webhook error] {e}")
        # WA reintenta si devolvés non-200; preferimos 200 para no spamear
        return JSONResponse({"status": "error", "msg": str(e)[:200]})


# ───────── AUTH PANEL ─────────
def _session_is_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT expires_at FROM sessions WHERE token=?", (token,)
            ).fetchone()
            if not row:
                return False
            if row["expires_at"] < now_ts():
                conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                return False
            return True
    except Exception as e:
        print(f"[session check err] {e}")
        return False


def require_auth(session: Optional[str] = Cookie(None)) -> str:
    if not _session_is_valid(session):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return session


@app.post("/login")
async def login(password: str = Form(...)):
    if password != WA_PANEL_PASSWORD:
        return JSONResponse({"ok": False, "error": "Contraseña incorrecta"}, status_code=401)
    token = secrets.token_urlsafe(32)
    now = now_ts()
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions(token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, now, now + SESSION_TTL_SECONDS)
        )
        # Limpieza oportunista de expiradas
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "session", token,
        httponly=True, secure=True, samesite="none",  # none = funciona en iframe cross-subdomain
        max_age=SESSION_TTL_SECONDS,
    )
    return resp


@app.post("/logout")
async def logout(session: Optional[str] = Cookie(None)):
    if session:
        try:
            with db() as conn:
                conn.execute("DELETE FROM sessions WHERE token=?", (session,))
        except Exception:
            pass
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


# ───────── API PANEL ─────────
@app.get("/api/stats")
async def stats(_: str = Depends(require_auth)):
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        unread = conn.execute("SELECT COUNT(*) FROM conversations WHERE unread > 0").fetchone()[0]
        escalated = conn.execute("SELECT COUNT(*) FROM conversations WHERE escalated = 1").fetchone()[0]
        # Mensajes del día (CR)
        day_start_cr = (dt.datetime.utcnow() - dt.timedelta(hours=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        ts_day_start = int((day_start_cr + dt.timedelta(hours=6)).timestamp())
        msgs_today = conn.execute("SELECT COUNT(*) FROM messages WHERE ts >= ?", (ts_day_start,)).fetchone()[0]
        # Conteos por estado
        rows = conn.execute(
            "SELECT COALESCE(status,'nuevo') AS s, COUNT(*) AS c FROM conversations GROUP BY s"
        ).fetchall()
        by_status = {r["s"]: r["c"] for r in rows}
    return {
        "total": total,
        "unread": unread,
        "escalated": escalated,
        "msgs_today": msgs_today,
        "business_hours": is_business_hours(),
        "by_status": {s: by_status.get(s, 0) for s in STATUS_ORDER},
    }


@app.get("/api/conversations")
async def list_conversations(status: Optional[str] = None, _: str = Depends(require_auth)):
    where = ""
    params: tuple = ()
    if status and status in STATUS_ORDER:
        where = "WHERE COALESCE(status,'nuevo') = ?"
        params = (status,)
    with db() as conn:
        rows = conn.execute(f"""
            SELECT phone, name, last_seen, last_message_preview, escalated, unread,
                   odoo_partner_id, COALESCE(status,'nuevo') AS status,
                   odoo_sale_order_name, payment_meta
            FROM conversations
            {where}
            ORDER BY last_seen DESC
            LIMIT 200
        """, params).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/conversation/{phone}/status")
async def set_conversation_status(phone: str, request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    new_status = (data.get("status") or "").strip()
    if new_status not in STATUS_ORDER:
        raise HTTPException(400, f"Status inválido. Permitidos: {STATUS_ORDER}")
    _set_status(phone, new_status, force=True)
    return {"ok": True, "status": new_status}


@app.post("/api/partner/{partner_id}/update")
async def update_partner(partner_id: int, request: Request, _: str = Depends(require_auth)):
    """Actualiza campos del res.partner en Odoo. Body: dict con cualquiera de:
       name, email, phone, mobile, street, street2, city, zip"""
    data = await request.json()
    allowed = ["name", "email", "phone", "street", "street2", "city", "zip"]
    values = {}
    for k in allowed:
        if k in data:
            v = data[k]
            values[k] = (v.strip() if isinstance(v, str) else v)
    if not values:
        return JSONResponse({"ok": False, "error": "Sin cambios"}, status_code=400)
    uid = odoo_authenticate()
    if not uid:
        return JSONResponse({"ok": False, "error": "Odoo no disponible"}, status_code=503)
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "res.partner", "write",
            [[partner_id], values],
        )
        print(f"[partner update] id={partner_id} keys={list(values.keys())}")
        return {"ok": True, "updated": list(values.keys())}
    except Exception as e:
        print(f"[partner update err] {e}")
        global _odoo_uid_cache
        _odoo_uid_cache = None
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@app.get("/api/partner/{partner_id}/full")
async def get_partner_full(partner_id: int, _: str = Depends(require_auth)):
    """Devuelve todos los campos editables del partner para la ficha completa."""
    uid = odoo_authenticate()
    if not uid:
        return JSONResponse({"ok": False, "error": "Odoo no disponible"}, status_code=503)
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        rows = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "res.partner", "read",
            [[partner_id]],
            {"fields": [
                "name", "email", "phone", "street", "street2",
                "city", "zip", "state_id", "country_id",
                "sale_order_count", "total_invoiced", "comment"
            ]},
        )
        if not rows:
            return JSONResponse({"ok": False, "error": "No encontrado"}, status_code=404)
        p = rows[0]
        return {
            "ok": True,
            "id": partner_id,
            "name": p.get("name") or "",
            "email": p.get("email") or "",
            "phone": p.get("phone") or "",
            "street": p.get("street") or "",
            "street2": p.get("street2") or "",
            "city": p.get("city") or "",
            "zip": p.get("zip") or "",
            "state": p.get("state_id")[1] if p.get("state_id") else "",
            "country": p.get("country_id")[1] if p.get("country_id") else "",
            "sale_count": int(p.get("sale_order_count") or 0),
            "total_invoiced": float(p.get("total_invoiced") or 0),
            "comment": p.get("comment") or "",
        }
    except Exception as e:
        print(f"[partner full err] {e}")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@app.post("/api/odoo/carriers/{carrier_id}/quote")
async def carrier_quote(carrier_id: int, request: Request, _: str = Depends(require_auth)):
    """Cotiza precio del envío con un carrier según peso (gramos) y partner.

    Para Dual Global: deriva zona del cantón del partner (x_studio_canton_cr)
    y aplica las tarifas de zonas_dual.DUAL_TARIFFS. Soporta entrega a domicilio
    opcional vía home_delivery=true en el body.
    Para otros carriers: usa fixed_price del carrier (Tavo/Pymex/etc.)."""
    data = await request.json()
    weight_g = float(data.get("weight_g") or 500)
    partner_id = data.get("partner_id")
    home_delivery = bool(data.get("home_delivery", False))
    uid = odoo_authenticate()
    if not uid:
        return JSONResponse({"ok": False, "error": "Odoo no disponible"}, status_code=503)
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        c = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "delivery.carrier", "read",
            [[carrier_id]],
            {"fields": ["name", "delivery_type", "fixed_price"]},
        )
        if not c:
            return JSONResponse({"ok": False, "error": "Carrier no encontrado"}, status_code=404)
        carrier = c[0]
        cname = (carrier["name"] or "").lower()

        # Para Dual Global: cotización por zona (cantón → zona) + peso
        if "dual" in cname:
            from zonas_dual import quote_dual_by_canton
            canton_id = None
            if partner_id:
                try:
                    pr = models.execute_kw(
                        ODOO_DB, uid, ODOO_API_KEY,
                        "res.partner", "read", [[int(partner_id)]],
                        {"fields": ["x_studio_canton_cr", "name", "city"]})
                    if pr and pr[0].get("x_studio_canton_cr"):
                        canton_id = pr[0]["x_studio_canton_cr"][0]
                except Exception:
                    canton_id = None
            q = quote_dual_by_canton(weight_g, canton_id, home_delivery=home_delivery)
            return {
                "ok": True,
                "carrier_id": carrier_id,
                "carrier_name": carrier["name"],
                "delivery_type": "zone_based",
                "price": q["precio_total"],
                "weight_g": weight_g,
                "zone": q["zone"],
                "zone_name": q["zone_name"],
                "rango_peso": q["rango"],
                "precio_base": q["precio_base"],
                "home_delivery": q["home_delivery"],
                "home_extra": q["home_extra"],
                "canton_id": canton_id,
                "recommended_carrier_id": q["recommended_carrier_id"],
                "note": (
                    f'Tarifa Dual Global {q["zone_name"]} — rango {q["rango"]}. '
                    f'Entrega a domicilio: {"INCLUIDA" if not q["home_extra"] and q["home_delivery"] else ("+₡" + str(q["home_extra"]) if q["home_delivery"] else "no incluida (retiro en sucursal)")}. '
                    f'Carrier sugerido en Odoo: #{q["recommended_carrier_id"]}.'
                ),
            }

        # Otros carriers: precio fijo del carrier (Tavo/Pymex/Mensajería/etc.)
        price = float(carrier.get("fixed_price") or 0)
        return {
            "ok": True,
            "carrier_id": carrier_id,
            "carrier_name": carrier["name"],
            "delivery_type": carrier["delivery_type"],
            "price": price,
            "weight_g": weight_g,
            "note": "Precio fijo del carrier en Odoo. El precio real puede variar según peso/zona — confirmar con cotización oficial.",
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


def _odoo_partner_brief(partner_id: int) -> Optional[dict]:
    """Trae datos mínimos del partner + URL al backend de Odoo."""
    if not partner_id:
        return None
    uid = odoo_authenticate()
    if not uid:
        return None
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        rows = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "res.partner", "read",
            [[partner_id]],
            {"fields": ["name", "email", "phone", "city", "sale_order_count", "total_invoiced"]},
        )
        if not rows:
            return None
        p = rows[0]
        return {
            "id": partner_id,
            "name": p.get("name") or "",
            "email": p.get("email") or "",
            "phone": p.get("phone") or "",
            "city": p.get("city") or "",
            "sale_count": int(p.get("sale_order_count") or 0),
            "total_invoiced": float(p.get("total_invoiced") or 0),
            "url": f"{ODOO_URL}/odoo/contacts/{partner_id}",
        }
    except Exception as e:
        print(f"[odoo partner brief err] {e}")
        return None


@app.get("/api/conversation/{phone}")
async def get_conversation(phone: str, _: str = Depends(require_auth)):
    with db() as conn:
        info_row = conn.execute("SELECT * FROM conversations WHERE phone=?", (phone,)).fetchone()
        msgs = conn.execute("""
            SELECT id, direction, text, ts, bot_replied, media_path, status, wa_msg_id
            FROM messages WHERE phone=?
            ORDER BY ts ASC
        """, (phone,)).fetchall()
        # marcar como leído
        conn.execute("UPDATE conversations SET unread=0 WHERE phone=?", (phone,))
    info = dict(info_row) if info_row else None
    if info and not info.get("status"):
        info["status"] = "nuevo"
    if info:
        if info.get("payment_meta"):
            info["payments"] = _payments_list(info["payment_meta"])
            info["total_paid"] = _total_paid(info["payment_meta"])
            info["payment_meta_parsed"] = info["payments"][-1] if info["payments"] else None
        else:
            info["payments"] = []
            info["total_paid"] = 0
    partner_info = None
    if info and info.get("odoo_partner_id"):
        partner_info = _odoo_partner_brief(info["odoo_partner_id"])
    return {
        "info": info,
        "partner": partner_info,
        "messages": [dict(m) for m in msgs],
    }


@app.post("/api/conversation/create")
async def create_pending_conversation(request: Request, _: str = Depends(require_auth)):
    """Crea una conversación pre-pendiente (sin haber recibido mensaje del cliente todavía).
    Body: { phone, name (req), note (opt), wa_message (opt - texto del wa.me link) }
    Devuelve link wa.me + datos del partner creado en Odoo.
    Útil para añadir leads desde el panel y compartirles un link para que ellos te escriban.
    Cumple política Meta: NO enviamos nada al cliente; el contacto solo queda pre-cargado en la DB."""
    body = await request.json()
    phone_raw = (body.get("phone") or "").strip()
    name = (body.get("name") or "").strip()[:120]
    note = (body.get("note") or "").strip()[:500]
    wa_message = (body.get("wa_message") or "").strip()[:300]

    if not phone_raw:
        raise HTTPException(400, "Teléfono requerido")
    if not name:
        raise HTTPException(400, "Nombre requerido")

    # Normalizar phone: solo dígitos. Si es CR 8 dígitos, prefijar 506.
    phone = _normalize_phone_digits(phone_raw)
    if len(phone) == 8:
        phone = "506" + phone
    if len(phone) < 10 or len(phone) > 15:
        raise HTTPException(400, f"Teléfono inválido: {phone}")

    # Resolver/crear partner en Odoo
    partner_id = None
    try:
        partner = odoo_resolve_partner(phone, name)
        if partner and partner.get("id"):
            partner_id = partner["id"]
            print(f"[new contact] partner odoo id={partner_id} ({'existente' if partner.get('is_existing') else 'nuevo'})")
    except Exception as e:
        print(f"[new contact] odoo resolve err: {e}")

    # Crear/actualizar conversación en SQLite
    ts = now_ts()
    created_new = False
    with db() as conn:
        existing = conn.execute("SELECT phone FROM conversations WHERE phone=?", (phone,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE conversations SET name=?, odoo_partner_id=COALESCE(?, odoo_partner_id) WHERE phone=?",
                (name, partner_id, phone),
            )
        else:
            conn.execute("""
                INSERT INTO conversations (phone, name, first_seen, last_seen, last_message_preview, status, unread, odoo_partner_id, escalated)
                VALUES (?, ?, ?, ?, '(contacto creado desde panel)', 'nuevo', 0, ?, 0)
            """, (phone, name, ts, ts, partner_id))
            created_new = True
        # Si hay nota, guardarla como mensaje informativo (no enviado a WA)
        if note:
            conn.execute("""
                INSERT INTO messages (phone, direction, text, ts, bot_replied, media_path)
                VALUES (?, 'out', ?, ?, 0, NULL)
            """, (phone, f"📝 Nota interna del operador: {note}", ts))

    # Construir wa.me link con mensaje pre-llenado
    first_name = name.split()[0] if name else ""
    default_msg = f"Hola {first_name}, te escribimos de Paracarpinteros 👋".strip()
    wa_text = wa_message or default_msg
    wa_link = f"https://wa.me/{phone}?text=" + urllib.parse.quote(wa_text)

    return {
        "ok": True,
        "phone": phone,
        "name": name,
        "partner_id": partner_id,
        "created_new": created_new,
        "wa_link": wa_link,
        "wa_text_used": wa_text,
    }


@app.post("/api/conversation/{phone}/reply")
async def reply_manual(phone: str, request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Mensaje vacío")
    try:
        result = await send_wa_message(phone, text)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    wa_id = None
    try:
        if isinstance(result, dict) and result.get("messages"):
            wa_id = (result["messages"][0] or {}).get("id")
    except Exception:
        wa_id = None
    with db() as conn:
        conn.execute("""
            INSERT INTO messages(phone, direction, text, ts, bot_replied, wa_msg_id, status)
            VALUES (?, 'out', ?, ?, 0, ?, ?)
        """, (phone, text, now_ts(), wa_id, "sent" if wa_id else None))
        conn.execute("""
            UPDATE conversations SET last_seen=?, last_message_preview=?
            WHERE phone=?
        """, (now_ts(), text[:200], phone))
    return {"ok": True, "result": result}


@app.post("/api/conversation/{phone}/reply-image")
async def reply_image_manual(
    phone: str,
    image: UploadFile = File(...),
    caption: str = Form(""),
    _: str = Depends(require_auth),
):
    """Envía una imagen al cliente. Sube a Meta y persiste localmente para mostrarla en el panel."""
    img_bytes = await image.read()
    if not img_bytes:
        return JSONResponse({"ok": False, "error": "Imagen vacía"}, status_code=400)
    if len(img_bytes) > 5 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "Imagen demasiado grande (máx 5 MB)"}, status_code=400)
    cap = (caption or "").strip()[:1024]
    fname_orig = image.filename or "img.jpg"
    try:
        media_id = await upload_media_to_meta(img_bytes, filename=fname_orig)
        if not media_id:
            return JSONResponse({"ok": False, "error": "No se pudo subir la imagen a WhatsApp"}, status_code=500)
        result = await send_wa_image_by_id(phone, media_id, caption=cap)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if not (isinstance(result, dict) and result.get("messages")):
        err = (result.get("error") if isinstance(result, dict) else None) or "Meta rechazó el envío"
        return JSONResponse({"ok": False, "error": str(err)[:300]}, status_code=500)
    wa_id = (result["messages"][0] or {}).get("id")
    # Guardar localmente para que el panel pueda mostrarla
    ts = now_ts()
    ext = ".jpg"
    low = fname_orig.lower()
    for e in (".png", ".jpeg", ".webp", ".gif"):
        if low.endswith(e):
            ext = e if e != ".jpeg" else ".jpg"
            break
    fname = f"out_manual_{ts}{ext}"
    try:
        with open(f"/opt/whatsapp-bot/data/media/{fname}", "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        print(f"[reply-image save err] {e}")
        fname = None
    preview = f"[FOTO] {cap}" if cap else "[FOTO]"
    with db() as conn:
        conn.execute("""
            INSERT INTO messages(phone, direction, text, ts, bot_replied, media_path, wa_msg_id, status)
            VALUES (?, 'out', ?, ?, 0, ?, ?, ?)
        """, (phone, preview, ts, fname, wa_id, "sent" if wa_id else None))
        conn.execute("""
            UPDATE conversations SET last_seen=?, last_message_preview=?
            WHERE phone=?
        """, (ts, preview[:200], phone))
    return {"ok": True, "wa_msg_id": wa_id, "media_path": fname}


@app.get("/api/conversation/{phone}/wizard")
async def wizard_info(phone: str, _: str = Depends(require_auth)):
    """Devuelve info consolidada para el wizard de despacho."""
    with db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE phone=?", (phone,)).fetchone()
    if not row:
        return {"ok": False, "error": "Conversación no encontrada"}
    order_id = row["odoo_sale_order_id"]
    payment_meta = None
    if row["payment_meta"]:
        try:
            payment_meta = json.loads(row["payment_meta"])
        except Exception:
            pass

    # FALLBACK: si la conv no tiene sale.order asociado pero hay partner, buscar el último draft/sent en Odoo
    if not order_id and row["odoo_partner_id"]:
        try:
            uid_t = odoo_authenticate()
            if uid_t:
                m = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
                ids = m.execute_kw(
                    ODOO_DB, uid_t, ODOO_API_KEY,
                    "sale.order", "search",
                    [[("partner_id", "=", row["odoo_partner_id"]),
                      ("state", "in", ["draft", "sent", "sale"])]],
                    {"order": "id desc", "limit": 1}
                )
                if ids:
                    found_id = ids[0]
                    found = m.execute_kw(
                        ODOO_DB, uid_t, ODOO_API_KEY,
                        "sale.order", "read",
                        [[found_id]],
                        {"fields": ["name"]}
                    )
                    found_name = found[0]["name"] if found else None
                    with db() as conn:
                        conn.execute(
                            "UPDATE conversations SET odoo_sale_order_id=?, odoo_sale_order_name=? WHERE phone=?",
                            (found_id, found_name, phone)
                        )
                    order_id = found_id
                    print(f"[wizard auto-link] phone={phone} → vinculada sale.order {found_name} (#{found_id})")
        except Exception as e:
            print(f"[wizard auto-link err] {e}")

    order_info = None
    if order_id:
        uid = odoo_authenticate()
        if uid:
            try:
                models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
                rs = models.execute_kw(
                    ODOO_DB, uid, ODOO_API_KEY,
                    "sale.order", "read",
                    [[order_id]],
                    {"fields": ["name", "state", "carrier_id", "amount_total", "picking_ids", "order_line"]},
                )
                if rs:
                    o = rs[0]
                    # Líneas
                    lines = []
                    if o.get("order_line"):
                        lines_data = models.execute_kw(
                            ODOO_DB, uid, ODOO_API_KEY,
                            "sale.order.line", "read",
                            [o["order_line"]],
                            {"fields": ["name", "product_uom_qty", "price_subtotal", "product_id"]},
                        )
                        lines = lines_data
                    picking_name = ""
                    picking_state = ""
                    if o.get("picking_ids"):
                        picks = models.execute_kw(
                            ODOO_DB, uid, ODOO_API_KEY,
                            "stock.picking", "read",
                            [o["picking_ids"]],
                            {"fields": ["name", "state"]},
                        )
                        if picks:
                            picking_name = picks[0]["name"]
                            picking_state = picks[0]["state"]
                    order_info = {
                        "id": o["id"],
                        "name": o["name"],
                        "state": o["state"],  # draft/sent/sale/done/cancel
                        "amount_total": o["amount_total"],
                        "carrier_id": o["carrier_id"][0] if o.get("carrier_id") else None,
                        "carrier_name": o["carrier_id"][1] if o.get("carrier_id") else None,
                        "picking_name": picking_name,
                        "picking_state": picking_state,
                        "url": f"{ODOO_URL}/odoo/sales/{o['id']}",
                        "lines": [
                            {
                                "name": l["name"],
                                "qty": l["product_uom_qty"],
                                "subtotal": l["price_subtotal"],
                            }
                            for l in lines
                        ],
                    }
            except Exception as e:
                print(f"[wizard_info err] {e}")
                global _odoo_uid_cache
                _odoo_uid_cache = None
    return {
        "ok": True,
        "phone": phone,
        "conv_status": row["status"] or "nuevo",
        "payment": payment_meta,
        "order": order_info,
    }


@app.get("/api/odoo/carriers")
async def list_odoo_carriers(_: str = Depends(require_auth)):
    """Lista los delivery.carrier activos de Odoo."""
    uid = odoo_authenticate()
    if not uid:
        return JSONResponse({"ok": False, "error": "Odoo no disponible"}, status_code=503)
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        rows = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "delivery.carrier", "search_read",
            [[("active", "=", True)]],
            {
                "fields": ["name", "delivery_type", "fixed_price"],
                "order": "sequence asc, name asc",
            },
        )
        return {"ok": True, "carriers": rows}
    except Exception as e:
        print(f"[list_carriers err] {e}")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@app.post("/api/conversation/{phone}/ask-balance")
async def ask_balance_diff(phone: str, request: Request, _: str = Depends(require_auth)):
    """Le manda al cliente un mensaje pidiendo que pague la diferencia que falta."""
    data = await request.json()
    amount_due = float(data.get("amount_due") or 0)
    note = (data.get("note") or "").strip()
    if amount_due <= 0:
        return JSONResponse({"ok": False, "error": "amount_due debe ser > 0"}, status_code=400)
    with db() as conn:
        row = conn.execute(
            "SELECT name, odoo_sale_order_name FROM conversations WHERE phone=?", (phone,)
        ).fetchone()
    cliente_nombre = (row["name"] if row else "") or ""
    order_name = (row["odoo_sale_order_name"] if row else "") or ""
    parts = ["Hola"]
    if cliente_nombre:
        parts[0] = f"Hola {cliente_nombre.split()[0]}"
    parts.append(",")
    parts.append("")
    parts.append(f"Para completar tu pedido{f' *{order_name}*' if order_name else ''} falta abonar:")
    parts.append("")
    parts.append(f"💰 *₡{int(amount_due):,}*")
    parts.append("")
    if note:
        parts.append(note)
        parts.append("")
    parts.append("Cuando hagas la transferencia, enviame el comprobante por aquí y procesamos el envío. 👍")
    msg = "\n".join(parts)
    try:
        resp = await send_wa_message(phone, msg)
        ok = "messages" in resp and bool(resp.get("messages"))
        if ok:
            _save_outbound(phone, msg, bot=False)
            return {"ok": True, "sent": True, "message_preview": msg}
        return JSONResponse({"ok": False, "error": "Error enviando WhatsApp"}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@app.post("/api/conversation/{phone}/quote-shipping")
async def quote_shipping_to_client(phone: str, request: Request, _: str = Depends(require_auth)):
    """
    Envía al cliente por WhatsApp las opciones de envío seleccionadas con precios.
    Body: {"carrier_ids": [1, 2, 3], "extra_note": "opcional"}
    """
    data = await request.json()
    carrier_ids = data.get("carrier_ids") or []
    extra_note = (data.get("extra_note") or "").strip()
    if not carrier_ids:
        return JSONResponse({"ok": False, "error": "carrier_ids requerido"}, status_code=400)
    uid = odoo_authenticate()
    if not uid:
        return JSONResponse({"ok": False, "error": "Odoo no disponible"}, status_code=503)
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        rows = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "delivery.carrier", "read",
            [[int(cid) for cid in carrier_ids]],
            {"fields": ["id", "name", "delivery_type", "fixed_price"]}
        )
        # Construir mensaje al cliente
        msg_lines = ["📦 *Opciones de envío disponibles para tu pedido:*", ""]
        letras = ["A", "B", "C", "D", "E", "F", "G"]
        for i, c in enumerate(rows):
            letra = letras[i] if i < len(letras) else str(i + 1)
            price = ""
            if c["delivery_type"] == "fixed":
                price = f"₡{int(c['fixed_price'] or 0):,}"
            elif c["delivery_type"] == "base_on_rule" and c["fixed_price"]:
                price = f"desde ₡{int(c['fixed_price'] or 0):,} (según peso/zona)"
            else:
                price = "según peso/destino"
            msg_lines.append(f"*{letra})* {c['name']} — {price}")
        msg_lines.append("")
        if extra_note:
            msg_lines.append(extra_note)
            msg_lines.append("")
        msg_lines.append("Decinos cuál preferís y te lo agregamos al pedido. 👍")
        message = "\n".join(msg_lines)

        # Enviar por WhatsApp
        resp = await send_wa_message(phone, message)
        ok = "messages" in resp and bool(resp.get("messages"))
        if ok:
            _save_outbound(phone, message, bot=False)
            print(f"[quote-shipping] phone={phone} opciones={len(rows)} enviadas")
            return {"ok": True, "sent": True, "options": len(rows)}
        return JSONResponse({"ok": False, "error": "Error enviando WhatsApp", "details": str(resp)[:200]}, status_code=500)
    except Exception as e:
        print(f"[quote_shipping err] {e}")
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@app.post("/api/conversation/{phone}/set-carrier")
async def set_order_carrier(phone: str, request: Request, _: str = Depends(require_auth)):
    """Asigna un delivery.carrier al sale.order y agrega la línea de envío al total.

    Body: {"carrier_id": int, "custom_price": float (opcional, sobrescribe fixed_price)}
    """
    data = await request.json()
    carrier_id = data.get("carrier_id")
    custom_price = data.get("custom_price")
    if not carrier_id:
        return JSONResponse({"ok": False, "error": "carrier_id requerido"}, status_code=400)
    with db() as conn:
        row = conn.execute(
            "SELECT odoo_sale_order_id, odoo_sale_order_name FROM conversations WHERE phone=?", (phone,)
        ).fetchone()
    if not row or not row["odoo_sale_order_id"]:
        return JSONResponse({"ok": False, "error": "Sin sale.order asociado"}, status_code=404)
    order_id = row["odoo_sale_order_id"]
    uid = odoo_authenticate()
    if not uid:
        return JSONResponse({"ok": False, "error": "Odoo no disponible"}, status_code=503)
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        # Leer carrier (precio, producto)
        c = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "delivery.carrier", "read",
            [[int(carrier_id)]],
            {"fields": ["name", "fixed_price", "delivery_type", "product_id"]},
        )
        if not c:
            return JSONResponse({"ok": False, "error": "Carrier no encontrado"}, status_code=404)
        carrier = c[0]
        price = float(custom_price) if custom_price is not None else float(carrier.get("fixed_price") or 0)
        carrier_name = carrier.get("name", "")
        # Asignar carrier al sale.order
        models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "sale.order", "write",
            [[order_id], {"carrier_id": int(carrier_id)}],
        )
        # Intentar set_delivery_line de Odoo (método oficial del módulo delivery)
        delivery_line_ok = False
        try:
            models.execute_kw(
                ODOO_DB, uid, ODOO_API_KEY,
                "sale.order", "set_delivery_line",
                [[order_id], int(carrier_id), price],
            )
            delivery_line_ok = True
            print(f"[set_carrier] set_delivery_line OK price={price}")
        except Exception as e1:
            # Fallback manual: borrar líneas marcadas como is_delivery, agregar nueva
            print(f"[set_carrier] set_delivery_line falló, intentando manual: {e1}")
            try:
                # Buscar líneas existentes is_delivery=True y eliminarlas
                existing = models.execute_kw(
                    ODOO_DB, uid, ODOO_API_KEY,
                    "sale.order.line", "search",
                    [[("order_id", "=", order_id), ("is_delivery", "=", True)]],
                )
                if existing:
                    models.execute_kw(
                        ODOO_DB, uid, ODOO_API_KEY,
                        "sale.order.line", "unlink", [existing],
                    )
                # Agregar nueva línea si hay precio y producto del carrier
                if price > 0 and carrier.get("product_id"):
                    product_id = carrier["product_id"][0] if isinstance(carrier["product_id"], list) else carrier["product_id"]
                    models.execute_kw(
                        ODOO_DB, uid, ODOO_API_KEY,
                        "sale.order.line", "create",
                        [{
                            "order_id": order_id,
                            "product_id": product_id,
                            "name": f"Envío: {carrier_name}",
                            "product_uom_qty": 1,
                            "price_unit": price,
                            "is_delivery": True,
                        }],
                    )
                    delivery_line_ok = True
                    print(f"[set_carrier] línea manual creada price={price}")
            except Exception as e2:
                print(f"[set_carrier] fallback manual falló: {e2}")
        # Leer total actualizado
        info = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "sale.order", "read",
            [[order_id]],
            {"fields": ["amount_total"]},
        )
        new_total = info[0]["amount_total"] if info else 0
        return {
            "ok": True,
            "carrier_name": carrier_name,
            "carrier_price": price,
            "delivery_line_added": delivery_line_ok,
            "new_total": new_total,
        }
    except Exception as e:
        print(f"[set_carrier err] {e}")
        global _odoo_uid_cache
        _odoo_uid_cache = None
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@app.post("/api/conversation/{phone}/manual-quote")
async def manual_quote(phone: str, request: Request, _: str = Depends(require_auth)):
    """Crea una cotización manualmente desde el panel admin (no requiere intervención del bot)."""
    data = await request.json()
    items = data.get("items") or []
    if not items:
        return JSONResponse({"ok": False, "error": "Sin items"}, status_code=400)
    with db() as conn:
        row = conn.execute("SELECT odoo_partner_id, name FROM conversations WHERE phone=?", (phone,)).fetchone()
    if not row:
        return JSONResponse({"ok": False, "error": "Conversación no encontrada"}, status_code=404)
    partner_id = row["odoo_partner_id"]
    if not partner_id:
        # Resolver / crear ad-hoc
        p = odoo_resolve_partner(phone, row["name"])
        if p and p.get("id"):
            partner_id = p["id"]
            with db() as conn:
                conn.execute("UPDATE conversations SET odoo_partner_id=? WHERE phone=?", (partner_id, phone))
    if not partner_id:
        return JSONResponse({"ok": False, "error": "No se pudo identificar el cliente en Odoo"}, status_code=400)
    result = create_quotation_odoo(partner_id, items, note=f"Cotización manual desde panel WA, phone +{phone}")
    if result.get("ok"):
        with db() as conn:
            conn.execute(
                "UPDATE conversations SET odoo_sale_order_id=?, odoo_sale_order_name=? WHERE phone=?",
                (result["order_id"], result["order_name"], phone)
            )
        _set_status(phone, "cotizado")
        event = (
            f"📋 Cotización {result['order_name']} creada manualmente desde panel · "
            f"{result['lines_count']} líneas · ₡{result['total_crc']:,}"
        )
        _save_outbound(phone, event, bot=False)
        print(f"[manual-quote] phone={phone} order={result['order_name']} url={result.get('url')}")
    return result


@app.post("/api/conversation/{phone}/confirm-order")
async def confirm_order_in_odoo(phone: str, _: str = Depends(require_auth)):
    """Confirma el sale.order asociado a la conversación (draft → sale). Genera picking."""
    with db() as conn:
        row = conn.execute(
            "SELECT odoo_sale_order_id, odoo_sale_order_name FROM conversations WHERE phone=?",
            (phone,)
        ).fetchone()
    if not row or not row["odoo_sale_order_id"]:
        return JSONResponse({"ok": False, "error": "No hay cotización asociada"}, status_code=400)
    order_id = row["odoo_sale_order_id"]
    uid = odoo_authenticate()
    if not uid:
        return JSONResponse({"ok": False, "error": "Odoo no disponible"}, status_code=503)
    try:
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        # Verificar que esté en draft o sent
        info = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "sale.order", "read",
            [[order_id]],
            {"fields": ["name", "state", "picking_ids"]},
        )
        if not info:
            return JSONResponse({"ok": False, "error": "Cotización no encontrada en Odoo"}, status_code=404)
        order = info[0]
        if order["state"] not in ("draft", "sent"):
            return JSONResponse({
                "ok": False,
                "error": f"La cotización ya está en estado '{order['state']}'",
                "order_name": order["name"],
            }, status_code=409)
        # Confirmar
        models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "sale.order", "action_confirm",
            [[order_id]],
        )
        # Leer picking generado
        info2 = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "sale.order", "read",
            [[order_id]],
            {"fields": ["name", "state", "picking_ids"]},
        )
        picking_name = ""
        if info2 and info2[0].get("picking_ids"):
            picks = models.execute_kw(
                ODOO_DB, uid, ODOO_API_KEY,
                "stock.picking", "read",
                [info2[0]["picking_ids"]],
                {"fields": ["name"]},
            )
            picking_name = picks[0]["name"] if picks else ""
        print(f"[confirm-order] phone={phone} order={order['name']} → picking={picking_name}")
        return {
            "ok": True,
            "order_name": order["name"],
            "picking_name": picking_name,
            "order_url": f"{ODOO_URL}/odoo/sales/{order_id}",
        }
    except Exception as e:
        print(f"[confirm-order err] {e}")
        global _odoo_uid_cache
        _odoo_uid_cache = None
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


@app.post("/api/conversation/{phone}/escalate")
async def toggle_escalate(phone: str, request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    escalated = 1 if data.get("escalated") else 0
    with db() as conn:
        conn.execute("UPDATE conversations SET escalated=? WHERE phone=?", (escalated, phone))
    return {"ok": True, "escalated": bool(escalated)}


# ───────── BACKUPS ─────────
BACKUP_DIR = "/var/backups/whatsapp-bot"


@app.get("/api/backups")
async def list_backups(_: str = Depends(require_auth)):
    if not os.path.isdir(BACKUP_DIR):
        return {"ok": True, "backups": []}
    out = []
    try:
        for fn in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if not fn.endswith(".tar.gz"):
                continue
            path = f"{BACKUP_DIR}/{fn}"
            try:
                st = os.stat(path)
                out.append({
                    "filename": fn,
                    "size_bytes": st.st_size,
                    "size_human": _human_size(st.st_size),
                    "modified": int(st.st_mtime),
                })
            except Exception:
                continue
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return {"ok": True, "backups": out}


@app.get("/api/backups/{filename}")
async def download_backup(filename: str, _: str = Depends(require_auth)):
    from fastapi.responses import FileResponse
    # Sanitize
    if "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".tar.gz"):
        raise HTTPException(404)
    path = f"{BACKUP_DIR}/{filename}"
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="application/gzip", filename=filename)


@app.post("/api/backups/run-now")
async def run_backup_now(_: str = Depends(require_auth)):
    """Dispara un backup manual: comprime /opt/whatsapp-bot/data en tar.gz."""
    import tarfile
    import time as time_mod
    src = "/opt/whatsapp-bot/data"
    if not os.path.isdir(src):
        return JSONResponse({"ok": False, "error": "Carpeta data no encontrada"}, status_code=500)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = f"{BACKUP_DIR}/wabot_{ts}.tar.gz"
    t0 = time_mod.time()
    try:
        with tarfile.open(out, "w:gz") as tar:
            tar.add(src, arcname="data")
        size = os.path.getsize(out)
        # Rotación: mantener últimos 30
        files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("wabot_") and f.endswith(".tar.gz")])
        while len(files) > 30:
            try:
                os.remove(f"{BACKUP_DIR}/{files[0]}")
                files.pop(0)
            except Exception:
                break
        return {
            "ok": True,
            "filename": os.path.basename(out),
            "size_bytes": size,
            "size_human": _human_size(size),
            "duration_s": round(time_mod.time() - t0, 2),
            "total_backups": len(files),
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=500)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ───────── KNOWLEDGE BASE ─────────
# ───────── APP SETTINGS (modo del bot, flags) ─────────
BOT_MODES = {
    "normal": {
        "label": "Normal",
        "desc": "Bot completo: responde con Claude, busca productos, manda fotos, crea cotizaciones y registra pagos.",
        "suggested_when": "Quality GREEN estable y conversaciones sin reportes.",
    },
    "conservative": {
        "label": "Conservador",
        "desc": "Bot responde con Claude pero NO crea cotizaciones automáticamente, NO manda fotos sin que el cliente las pida explícitamente y respuestas más cortas. Menor riesgo de spam-report.",
        "suggested_when": "Quality YELLOW o cuando empiezas a recibir conversaciones de clientes nuevos.",
    },
    "escalate_all": {
        "label": "Solo humano",
        "desc": "Bot NO responde nada. Cada mensaje entrante queda 'sin leer' en el panel y se marca como escalada para que un humano conteste. Cero riesgo de baneo por respuestas del bot.",
        "suggested_when": "Quality RED o cuando detectes que algo se rompió en las respuestas del bot.",
    },
}


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        with db() as conn:
            r = conn.execute(
                "SELECT value FROM app_settings WHERE key=?", (key,)
            ).fetchone()
            return r["value"] if r else default
    except Exception:
        return default


def set_setting(key: str, value: str):
    with db() as conn:
        conn.execute("""
            INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, value, now_ts()))


def get_bot_mode() -> str:
    m = get_setting("bot_reply_mode", "normal") or "normal"
    return m if m in BOT_MODES else "normal"


@app.get("/api/bot/mode")
async def api_get_bot_mode(_: str = Depends(require_auth)):
    return {"mode": get_bot_mode(), "modes": BOT_MODES}


@app.post("/api/bot/mode")
async def api_set_bot_mode(request: Request, _: str = Depends(require_auth)):
    body = await request.json()
    mode = (body.get("mode") or "").strip()
    if mode not in BOT_MODES:
        raise HTTPException(400, f"Modo inválido. Opciones: {list(BOT_MODES.keys())}")
    set_setting("bot_reply_mode", mode)
    print(f"[bot mode] changed to {mode}")
    return {"ok": True, "mode": mode}


# ───────── TERMÓMETRO META (Graph API) ─────────
_META_HEALTH_CACHE: dict = {"ts": 0, "data": None}
_META_HEALTH_TTL = 300  # 5 min


def _fix_double_utf8(s: str) -> str:
    """Algunos endpoints de Meta devuelven strings con doble-encoding latin-1→UTF-8.
    Detecta el patrón (Ã + carácter UTF-8 alto) e intenta el roundtrip latin-1 → utf-8.
    Si no es doble-encoded, devuelve el string sin cambios."""
    if not s or not isinstance(s, str):
        return s
    if "Ã" not in s and "Â" not in s:
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _walk_fix_strings(obj):
    """Recorre el JSON y arregla strings con doble UTF-8."""
    if isinstance(obj, str):
        return _fix_double_utf8(obj)
    if isinstance(obj, dict):
        return {k: _walk_fix_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_fix_strings(v) for v in obj]
    return obj


async def _meta_graph_get(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> dict:
    url = f"{WA_API_BASE}/{path}"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}"}
    try:
        r = await client.get(url, params=params or {}, headers=headers, timeout=12.0)
        try:
            data = json.loads(r.content.decode("utf-8"))
            data = _walk_fix_strings(data)
        except Exception:
            data = {"_status": r.status_code, "_text": r.text[:300]}
        if r.status_code >= 400:
            data["_error_status"] = r.status_code
        return data
    except Exception as e:
        return {"_error": str(e)[:200]}


def _classify_quality(rating: str) -> dict:
    r = (rating or "").upper()
    mapping = {
        "GREEN": ("ok", "Excelente"),
        "YELLOW": ("warn", "Bajando — vigilar"),
        "RED": ("err", "Mala — riesgo de ban"),
        "FLAGGED": ("err", "Marcado"),
        "UNKNOWN": ("muted", "Sin datos suficientes"),
    }
    level, label = mapping.get(r, ("muted", r or "—"))
    return {"raw": r, "level": level, "label": label}


def _summarize_analytics(payload: dict) -> dict:
    """Aplana conversation_analytics en agregados simples por categoría/dirección."""
    if not payload:
        return {}
    ca = payload.get("conversation_analytics") or {}
    points = (ca.get("data") or [{}])[0].get("data_points", []) if ca else []
    by_cat: dict[str, int] = {}
    total = 0
    cost_total = 0.0
    for p in points:
        cat = p.get("conversation_category") or "UNKNOWN"
        c = int(p.get("conversation") or 0)
        by_cat[cat] = by_cat.get(cat, 0) + c
        total += c
        try:
            cost_total += float(p.get("cost") or 0)
        except Exception:
            pass
    return {"total": total, "by_category": by_cat, "cost": round(cost_total, 4), "points": len(points)}


async def gather_meta_health(force: bool = False) -> dict:
    now = now_ts()
    if not force and _META_HEALTH_CACHE["data"] and (now - _META_HEALTH_CACHE["ts"]) < _META_HEALTH_TTL:
        return {**_META_HEALTH_CACHE["data"], "cached": True, "age_s": now - _META_HEALTH_CACHE["ts"]}

    start = now - 7 * 86400
    end = now
    waba_id = os.environ.get("WA_WABA_ID", "")
    analytics_fields = (
        f"conversation_analytics.start({start}).end({end}).granularity(DAILY)"
        f".phone_numbers([]).conversation_categories(['MARKETING','UTILITY','AUTHENTICATION','SERVICE'])"
        f".conversation_types(['REGULAR','FREE_TIER','FREE_ENTRY_POINT'])"
    )

    async def _noop():
        return {}

    async with httpx.AsyncClient() as client:
        phone_task = _meta_graph_get(client, WA_PHONE_NUMBER_ID, {
            "fields": "id,display_phone_number,verified_name,quality_rating,platform_type,throughput,name_status,code_verification_status"
        })
        if waba_id:
            waba_task = _meta_graph_get(client, waba_id, {
                "fields": "id,name,timezone_id,business_verification_status,ownership_type,on_behalf_of_business_info"
            })
            templates_task = _meta_graph_get(client, f"{waba_id}/message_templates", {
                "fields": "id,name,status,category,language,quality_score",
                "limit": 100,
            })
            analytics_task = _meta_graph_get(client, waba_id, {"fields": analytics_fields})
        else:
            waba_task = templates_task = analytics_task = _noop()
        phone, waba, templates, analytics = await asyncio.gather(
            phone_task, waba_task, templates_task, analytics_task
        )

    quality = _classify_quality(phone.get("quality_rating") or "")
    templates_list = (templates.get("data") or []) if isinstance(templates, dict) else []
    tmpl_summary = {
        "total": len(templates_list),
        "approved": sum(1 for t in templates_list if t.get("status") == "APPROVED"),
        "rejected": sum(1 for t in templates_list if t.get("status") == "REJECTED"),
        "pending": sum(1 for t in templates_list if t.get("status") in ("PENDING", "IN_APPEAL", "PENDING_DELETION")),
        "items": [
            {
                "name": t.get("name"),
                "status": t.get("status"),
                "category": t.get("category"),
                "language": t.get("language"),
                "quality": (t.get("quality_score") or {}).get("score"),
            }
            for t in templates_list
        ],
    }
    analytics_summary = _summarize_analytics(analytics if isinstance(analytics, dict) else {})

    data = {
        "ok": True,
        "ts": now,
        "phone": {
            "number": phone.get("display_phone_number"),
            "verified_name": phone.get("verified_name"),
            "quality": quality,
            "throughput": (phone.get("throughput") or {}).get("level"),
            "name_status": phone.get("name_status"),
            "platform": phone.get("platform_type"),
            "code_verification": phone.get("code_verification_status"),
            "_raw_error": phone.get("_error_status") or phone.get("_error"),
        },
        "waba": {
            "id": waba.get("id") if isinstance(waba, dict) else None,
            "name": waba.get("name") if isinstance(waba, dict) else None,
            "verification": waba.get("business_verification_status") if isinstance(waba, dict) else None,
            "ownership": waba.get("ownership_type") if isinstance(waba, dict) else None,
            "business": (waba.get("on_behalf_of_business_info") or {}).get("name") if isinstance(waba, dict) else None,
            "business_status": (waba.get("on_behalf_of_business_info") or {}).get("status") if isinstance(waba, dict) else None,
        },
        "templates": tmpl_summary,
        "analytics_7d": analytics_summary,
        "cached": False,
        "age_s": 0,
    }
    _META_HEALTH_CACHE["ts"] = now
    _META_HEALTH_CACHE["data"] = data
    return data


@app.get("/api/meta/health")
async def api_meta_health(force: int = 0, _: str = Depends(require_auth)):
    return await gather_meta_health(force=bool(force))


# ───────── PWA + WEB PUSH ─────────
PWA_STATIC_DIR = "/app/static"


@app.get("/manifest.webmanifest")
@app.get("/manifest.json")
async def pwa_manifest():
    manifest = {
        "name": "WhatsApp Bot · Paracarpinteros",
        "short_name": "WA Bot",
        "description": "Panel de WhatsApp Bot de Paracarpinteros",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#e8ebf0",
        "theme_color": "#008069",
        "lang": "es-CR",
        "categories": ["business", "productivity"],
        "icons": [
            {"src": "/pwa/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/pwa/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/pwa/icon-192-maskable.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "/pwa/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return JSONResponse(manifest, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/pwa/{filename}")
async def pwa_static(filename: str):
    safe = {
        "icon-192.png", "icon-512.png",
        "icon-192-maskable.png", "icon-512-maskable.png",
        "apple-touch-icon.png",
    }
    if filename not in safe:
        raise HTTPException(404)
    path = os.path.join(PWA_STATIC_DIR, "icons", filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon():
    path = os.path.join(PWA_STATIC_DIR, "icons", "apple-touch-icon.png")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sw.js")
async def service_worker():
    return Response(
        content=SW_JS,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/api/push/vapid-key")
async def push_vapid_key(_: str = Depends(require_auth)):
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(503, "VAPID no configurado")
    return {"key": VAPID_PUBLIC_KEY}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, _: str = Depends(require_auth)):
    body = await request.json()
    endpoint = body.get("endpoint")
    keys = body.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    ua = (body.get("ua") or request.headers.get("user-agent") or "")[:300]
    if not (endpoint and p256dh and auth):
        raise HTTPException(400, "missing endpoint/keys")
    ts = now_ts()
    with db() as conn:
        conn.execute("""
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, ua, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                p256dh=excluded.p256dh,
                auth=excluded.auth,
                ua=excluded.ua,
                last_seen_at=excluded.last_seen_at
        """, (endpoint, p256dh, auth, ua, ts, ts))
    print(f"[push subscribe] ua={ua[:80]!r}")
    return {"ok": True}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request, _: str = Depends(require_auth)):
    body = await request.json()
    endpoint = body.get("endpoint")
    if not endpoint:
        raise HTTPException(400, "missing endpoint")
    with db() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(_: str = Depends(require_auth)):
    if not PYWEBPUSH_AVAILABLE:
        raise HTTPException(503, "pywebpush no instalado")
    if not (VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY):
        raise HTTPException(503, "VAPID no configurado")
    n = await send_push_notification(
        title="🔔 Prueba Paracarpinteros",
        body="Las notificaciones del panel funcionan correctamente.",
        data={"url": "/", "test": True},
    )
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    return {"ok": True, "sent": n, "total_subs": total}


@app.get("/api/push/status")
async def push_status(_: str = Depends(require_auth)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, ua, created_at, last_seen_at FROM push_subscriptions ORDER BY created_at DESC"
        ).fetchall()
    return {
        "ok": True,
        "configured": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and PYWEBPUSH_AVAILABLE),
        "subs": [dict(r) for r in rows],
    }


@app.get("/api/knowledge")
async def list_knowledge(_: str = Depends(require_auth)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, category, title, content, active, sort_order, updated_at FROM bot_knowledge ORDER BY sort_order, id"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/knowledge")
async def create_knowledge(request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    category = (data.get("category") or "general").strip()
    if not title or not content:
        return JSONResponse({"ok": False, "error": "title y content requeridos"}, status_code=400)
    now = now_ts()
    with db() as conn:
        max_sort = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM bot_knowledge").fetchone()[0]
        c = conn.execute("""
            INSERT INTO bot_knowledge (category, title, content, active, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)
        """, (category, title, content, max_sort + 1, now, now))
        new_id = c.lastrowid
    return {"ok": True, "id": new_id}


@app.put("/api/knowledge/{kid}")
async def update_knowledge(kid: int, request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    fields = []
    values = []
    for k in ("title", "content", "category"):
        if k in data:
            fields.append(f"{k} = ?")
            values.append((data[k] or "").strip())
    if "active" in data:
        fields.append("active = ?")
        values.append(1 if data["active"] else 0)
    if not fields:
        return JSONResponse({"ok": False, "error": "Sin cambios"}, status_code=400)
    fields.append("updated_at = ?")
    values.append(now_ts())
    values.append(kid)
    with db() as conn:
        conn.execute(f"UPDATE bot_knowledge SET {', '.join(fields)} WHERE id=?", tuple(values))
    return {"ok": True}


@app.delete("/api/knowledge/{kid}")
async def delete_knowledge(kid: int, _: str = Depends(require_auth)):
    with db() as conn:
        conn.execute("DELETE FROM bot_knowledge WHERE id=?", (kid,))
    return {"ok": True}


# ───────── MEDIA ─────────
@app.get("/media/{filename}")
async def get_media(filename: str, _: str = Depends(require_auth)):
    from fastapi.responses import FileResponse
    # Sanitize: no path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(404)
    path = f"/opt/whatsapp-bot/data/media/{filename}"
    if not os.path.exists(path):
        raise HTTPException(404)
    # Detectar mime por extensión
    flow = filename.lower()
    if flow.endswith(".ogg") or flow.endswith(".oga"):
        mt = "audio/ogg"
    elif flow.endswith(".mp3"):
        mt = "audio/mpeg"
    elif flow.endswith(".m4a") or flow.endswith(".mp4"):
        mt = "audio/mp4"
    elif flow.endswith(".wav"):
        mt = "audio/wav"
    elif flow.endswith(".png"):
        mt = "image/png"
    else:
        mt = "image/jpeg"
    return FileResponse(path, media_type=mt)


# ───────── HEALTH ─────────
@app.get("/health")
async def health():
    odoo_status = "off"
    if all([ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY]):
        odoo_status = "ok" if odoo_authenticate() else "auth_failed"
    # Check Meta token sin enviar nada (solo GET al phone_number)
    meta_status = "off"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{WA_API_BASE}/{WA_PHONE_NUMBER_ID}",
                headers={"Authorization": f"Bearer {WA_ACCESS_TOKEN}"},
            )
            if r.status_code == 200:
                meta_status = "ok"
            elif r.status_code == 401:
                meta_status = "token_expired"
            else:
                meta_status = f"http_{r.status_code}"
    except Exception as e:
        meta_status = f"error: {str(e)[:60]}"
    return {
        "ok": True,
        "business_hours": is_business_hours(),
        "model": CLAUDE_MODEL,
        "odoo": odoo_status,
        "meta_token": meta_status,
        "whisper": "configured" if OPENAI_API_KEY else "missing_key",
    }


# ───────── SERVICE WORKER (PWA + Web Push) ─────────
SW_JS = r"""// Service Worker - WhatsApp Bot Paracarpinteros
// Versión: bump para forzar update en clientes
const SW_VERSION = 'wabot-v3';
const RUNTIME_CACHE = `${SW_VERSION}-runtime`;

self.addEventListener('install', (event) => {
  // Activar inmediatamente sin esperar a que se cierren las pestañas
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(n => !n.startsWith(SW_VERSION)).map(n => caches.delete(n)));
    await self.clients.claim();
  })());
});

// Estrategia: network-first para todo (panel siempre fresco), cache solo iconos PWA y fuentes.
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // Solo cachear assets estáticos (iconos PWA y fuentes Google)
  const cacheable = url.pathname.startsWith('/pwa/')
                 || url.pathname === '/manifest.json'
                 || url.pathname === '/manifest.webmanifest'
                 || url.host === 'fonts.googleapis.com'
                 || url.host === 'fonts.gstatic.com';
  if (!cacheable) return; // pass-through al network
  event.respondWith((async () => {
    const cache = await caches.open(RUNTIME_CACHE);
    const cached = await cache.match(req);
    const fetchPromise = fetch(req).then(resp => {
      if (resp && resp.status === 200) cache.put(req, resp.clone());
      return resp;
    }).catch(() => cached);
    return cached || fetchPromise;
  })());
});

// Push notifications — payload JSON con {title, body, data:{phone,url,...}}
self.addEventListener('push', (event) => {
  let payload = { title: 'Paracarpinteros', body: 'Mensaje nuevo', data: {} };
  try {
    if (event.data) {
      payload = { ...payload, ...event.data.json() };
    }
  } catch (e) {
    try { payload.body = event.data ? event.data.text() : payload.body; } catch (_) {}
  }
  const options = {
    body: payload.body || '',
    icon: '/pwa/icon-192.png',
    badge: '/pwa/icon-192.png',
    tag: payload.data && payload.data.phone ? ('msg-' + payload.data.phone) : 'wabot',
    renotify: true,
    vibrate: [120, 60, 120],
    data: payload.data || {},
    requireInteraction: false,
  };
  event.waitUntil(self.registration.showNotification(payload.title || 'Paracarpinteros', options));
});

// Click en notificación: enfocar pestaña abierta del panel o abrir una nueva
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetPath = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const allClients = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of allClients) {
      try {
        const u = new URL(client.url);
        if (u.origin === self.location.origin) {
          await client.focus();
          // Notificar a la página del click (para que abra el chat correspondiente)
          if (event.notification.data && event.notification.data.phone) {
            client.postMessage({ type: 'open-chat', phone: event.notification.data.phone });
          }
          return;
        }
      } catch (_) {}
    }
    await clients.openWindow(targetPath);
  })());
});

// Re-suscribirse si la subscripción caduca (Chrome rota claves a veces)
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    try {
      const res = await fetch('/api/push/vapid-key', { credentials: 'same-origin' });
      if (!res.ok) return;
      const { key } = await res.json();
      const sub = await self.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key),
      });
      await fetch('/api/push/subscribe', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint: sub.endpoint, keys: sub.toJSON().keys, ua: 'sw-resub' }),
      });
    } catch (e) { /* swallow */ }
  })());
});

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}
"""


# ───────── ROOT (panel) ─────────
@app.get("/static/{filename}")
async def panel_static(filename: str):
    safe = {"panel.css", "panel.js"}
    if filename not in safe:
        raise HTTPException(404)
    path = os.path.join(PWA_STATIC_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    mt = "text/css" if filename.endswith(".css") else "application/javascript"
    return FileResponse(path, media_type=mt, headers={"Cache-Control": "public, max-age=60"})


@app.get("/", response_class=HTMLResponse)
async def root(session: Optional[str] = Cookie(None)):
    fname = "panel.html" if _session_is_valid(session) else "login.html"
    return FileResponse(os.path.join(PWA_STATIC_DIR, fname), media_type="text/html")
