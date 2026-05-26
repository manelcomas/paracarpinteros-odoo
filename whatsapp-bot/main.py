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
  → Después confirmale al cliente: "¡Recibido! Anoté tu pago de ₡{monto} ({método}). Un compañero te prepara el envío en breve. Gracias!"

  **CASO B — Producto o herramienta** (foto física, captura de e-commerce, dibujo, etc.):
  1. Describí brevemente qué ves (1 frase).
  2. Usá `search_products` con palabras clave SIMPLES: 1-2 palabras genéricas, idealmente el sustantivo principal solo. NO metas colores, marcas comerciales, ni adjetivos tipo "intercambiable", "magnético", "eléctrico". Ej: si ves una broca avellanadora con mango azul, buscá "avellanador" (no "avellanador azul con mango").
  3. PRESENTÁ AL CLIENTE los resultados aunque no sean visualmente idénticos a la foto. NUNCA digas "no encontré ese producto exacto" si search_products devolvió al menos 1 resultado.
  4. Formato sugerido: "Veo un [tipo]. En nuestro catálogo tenemos: [hasta 3 productos]. ¿Alguno te sirve o pasamos con un compañero?"
  5. Solo si después de 2 búsquedas distintas search_products devolvió 0 resultados, podés decir que no hay y ofrecer pasar a un humano.

  Si dudás entre A y B (no es claro si es pago o producto), preguntale al cliente "¿esto es un comprobante de pago o me podés decir qué producto buscás?".
- Si `search_products` devuelve resultados, presentá hasta 3 al cliente con código, nombre y precio en colones (formato "₡4,500"). Si el cliente pide ver foto, pantallazo, imagen o referencia visual de un producto, usá la herramienta `send_product_photo` con el código exacto del producto — la foto va sola, vos solo confirmá brevemente con una frase tipo "Acá te la paso 👇".
- Sobre disponibilidad: NO menciones el número exacto de stock al cliente. Decí "disponible" si stock > 0, "consultá disponibilidad con un compañero" si stock <= 0. Nunca digas "tenemos 34 unidades", solo "disponible".
- Si la búsqueda devuelve precios sospechosamente bajos (₡1, ₡10) significa que el producto no tiene precio cargado: NO se lo muestres al cliente, decile "déjame confirmar el precio con un compañero" y ofrecé pasarlo al equipo.
- Si la búsqueda devuelve vacío, decí amablemente que no encontraste ese producto exacto y ofrecé pasarlo al equipo humano.
- Dar información sobre envíos por Pymexpress, Encomienda Nacional Correos CR, Tavo Encomiendas o Dual Global a todo el país.

ENVÍOS — leer con atención:
- Cuando el cliente pregunte cuánto cuesta el envío, qué opciones tiene, o pida comparar precios entre servicios, usá la herramienta `calculate_shipping_quote` con el peso aproximado del pedido en kilos. Si no sabés el peso, preguntale al cliente cuánto pesa aproximadamente el pedido (1 kg, 5 kg, etc.).
- Presentá las opciones devueltas al cliente como una lista breve con precios. Ej:
  "Para X kg, las opciones de envío son:
  - Pymexpress (entrega a domicilio): ₡8.400
  - Encomienda Nacional (retira en oficina Correos): ₡5.300
  - Transtusa/Tavo: ₡2.500
  - Dual Global (retira en agencia): ₡3.000
  - Retirada en almacén Santa Cruz, Turrialba: gratis"
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

B. **FOTOS — SÍ podés enviarlas**: NUNCA digas "no puedo enviar fotos por este chat", "te recomiendo verlas en la web", "escribí al correo para fotos" o frases similares. SÍ tenés la herramienta `send_product_photo(codigo)` que envía la foto del producto. Si el cliente pide foto/imagen/pantallazo y tenés el código del producto (de un `search_products` reciente), INVOCÁ `send_product_photo` SIN excepciones y respondé en UNA frase corta tipo "Acá te la paso 👇".

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

I. **TONO**: hablás como un compa tico, no como un robot.
   - Usá frases naturales: "Te paso esto 👇", "Mirá", "Buenísimo", "Listo", "Genial".
   - Evitá empezar respuestas con "Sí, tenemos..." o "Perfecto, anotado:" repetidamente.
   - Cuando mandes una card de producto con `send_product_photo`, NO repitas el código, nombre, ni precio en el texto (la card ya los muestra grandes). Una frase corta tipo "Acá te la paso 👇" o "Mirá la ficha" es suficiente.
   - Variaciones, no plantilla fija. Si en el turno anterior usaste "Perfecto", usá "Listo" / "Buenísimo" / "Genial" en el siguiente.

J. **EN PROCESO**: Si ya estás en medio de un flujo de compra (cotizaste, peso, envío) y el cliente cambia de tema bruscamente, retomá pero recordale dónde quedamos: "Genial, te ayudo con eso. Y respecto a la tapeteadora que estábamos viendo, ¿seguís interesado o lo dejamos para otro momento?"

D. **REPETICIÓN**: Si notás que el cliente está enviando la MISMA pregunta 2-3 veces seguidas (porque no le diste lo que quería), NO repitas la misma respuesta. Reconocé la repetición y ofrecé pasarlo con un humano: "Veo que te estoy dando vueltas con esto, dejame pasarte con un compañero que te resuelve mejor."

E. **INVENCIÓN**: NUNCA inventes datos que no estén en el `knowledge_block` o en resultados de tools. Si no sabés algo (dirección de agencia Dual, peso exacto de un producto, código de un producto que no buscaste), decí "Un compañero te confirma ese dato" y NO inventes.

F. **CONSISTENCIA DEL TOTAL**: Cuando informes al cliente "Total = producto + envío", asegurate de que la cotización que crees con `create_quotation` incluya AMBOS (producto + línea de envío). Si solo añadís el producto al sale.order, NO digas que el total incluye envío.

Tono: amable, cercano, profesional, tico (usá "vos" o "usted" según el cliente). Respuestas cortas (1-3 oraciones máximo, salvo cuando listás productos). No uses emojis excesivos.

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
                "fields": ["name", "default_code", "list_price", "qty_available", "description_sale", "weight"],
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
            "nombre, precio en colones, stock disponible y peso en kg (si está cargado en la ficha). "
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
            {"fields": ["id", "name", "image_1920", "list_price", "weight"], "limit": 1},
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
                return {"sent": True, "type": "card", "codigo": codigo, "nombre": name}
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
        return {"sent": True, "type": "photo", "codigo": codigo, "nombre": name}
    return {"sent": False, "error": str(resp)[:200]}

OUT_OF_HOURS_MSG = (
    f"¡Hola! Gracias por escribir a Paracarpinteros. "
    f"Nuestro horario de atención es de Lunes a Viernes de {BIZ_HOUR_START}am a {BIZ_HOUR_END}h hora Costa Rica. "
    f"Te respondemos en cuanto abramos. ¡Saludos!"
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
    """Cotiza precio del envío con un carrier según peso (gramos) y partner."""
    data = await request.json()
    weight_g = float(data.get("weight_g") or 500)
    partner_id = data.get("partner_id")
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


# ───────── HTML ─────────
LOGIN_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#008069">
<title>WhatsApp Bot · Paracarpinteros</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Helvetica Neue',Helvetica,'Lucida Grande',Arial,Ubuntu,Cantarell,'Fira Sans',sans-serif;
  background:#f0f2f5;color:#111b21;font-weight:400;-webkit-font-smoothing:antialiased;
  display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}
body::before{content:'';position:fixed;top:0;left:0;right:0;height:127px;background:#008069;z-index:0}
.box{max-width:400px;width:100%;background:#ffffff;border:none;border-radius:3px;overflow:hidden;box-shadow:0 1px 3px rgba(11,20,26,.1);position:relative;z-index:1}
.head{padding:32px 28px 20px;text-align:center}
.logo{font-size:1.4rem;color:#008069;font-weight:500}
.sub{font-size:.85rem;color:#667781;margin-top:4px;font-weight:400}
.body{padding:0 28px 32px}
input{width:100%;background:#ffffff;border:1px solid #e9edef;border-radius:6px;padding:11px 14px;
  color:#111b21;font-size:.95rem;outline:none;margin-bottom:14px;font-family:inherit;font-weight:400;transition:.15s}
input:focus{border-color:#008069;box-shadow:0 0 0 1px #008069}
button{width:100%;background:#008069;color:#fff;border:none;border-radius:24px;padding:12px;
  font-weight:500;cursor:pointer;font-size:.95rem;font-family:inherit;transition:.15s}
button:hover{background:#017561}
.err{color:#c53030;font-size:.82rem;margin-top:12px;text-align:center;min-height:18px;font-weight:400}
.hint{font-size:.85rem;color:#667781;margin-bottom:18px;text-align:center;font-weight:400}
</style></head>
<body>
<div class="box">
  <div class="head">
    <div class="logo">Paracarpinteros · WA Bot</div>
    <div class="sub">Centro de atención WhatsApp</div>
  </div>
  <div class="body">
    <div class="hint">Acceso restringido</div>
    <input id="pwd" type="password" placeholder="Contraseña" autocomplete="current-password"
      onkeydown="if(event.key==='Enter') doLogin()">
    <button onclick="doLogin()">Entrar</button>
    <div id="err" class="err"></div>
  </div>
</div>
<script>
async function doLogin(){
  const pwd = document.getElementById('pwd').value;
  const err = document.getElementById('err');
  err.textContent = '';
  const fd = new FormData(); fd.append('password', pwd);
  try{
    const r = await fetch('/login', {method:'POST', body: fd, credentials:'same-origin'});
    if(r.ok){ location.reload(); }
    else { const d = await r.json(); err.textContent = d.error || 'Error'; }
  } catch(e){ err.textContent = 'Error de conexión'; }
}
</script>
</body></html>
"""


PANEL_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#008069">
<title>WhatsApp Bot · Paracarpinteros</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" sizes="192x192" href="/pwa/icon-192.png">
<link rel="icon" type="image/png" sizes="512x512" href="/pwa/icon-512.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="WA Bot">
<meta name="mobile-web-app-capable" content="yes">
<style>
/* ───────── PALETA WHATSAPP WEB (light, relajada) ───────── */
:root{
  --bg:#f0f2f5;             /* Fondo general — neutro WA Web */
  --surface:#ffffff;        /* Sidebar / paneles */
  --card:#f5f6f6;           /* Cards / inputs */
  --card2:#f0f2f5;           /* Header / footer chat — más claro */
  --border:#e9edef;          /* Sutil, casi invisible */
  --border2:#d1d7db;         /* Borde "prominente" — antes era border */
  --text:#111b21;            /* Casi negro WA */
  --text2:#667781;           /* Gris medio WA Web original */
  --text3:#8696a0;           /* Gris claro placeholders */
  --green:#008069;
  --green2:#06cf9c;
  --green3:#d9fdd3;         /* Verde burbuja propia — WA Web real */
  --bg-chat:#efeae2;         /* Crema WA Web real */
  --yellow:#bf8a00;
  --red:#c53030;
  --blue:#3b6cb5;
  --bubble-shadow:0 1px .5px rgba(11,20,26,.13);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Helvetica Neue',Helvetica,'Lucida Grande',Arial,Ubuntu,Cantarell,'Fira Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased;font-weight:400}
.app{display:grid;grid-template-columns:30% 70%;height:100vh;max-width:1600px;margin:0 auto;background:var(--surface);box-shadow:0 0 0 1px rgba(11,20,26,.05)}
@media(max-width:1100px){.app{grid-template-columns:380px 1fr}}
@media(max-width:767px){.app{display:block;height:100vh}.sidebar{height:100vh;border-right:none}.main{height:100vh}}

/* Topbar WA */
.topbar{display:flex;align-items:center;gap:5px;padding:10px 16px;background:var(--card2);border-bottom:1px solid var(--border);min-height:59px;color:var(--text);flex-wrap:nowrap;overflow:hidden}
.topbar .logo{font-weight:500;font-size:.95rem;color:var(--text);flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.topbar .badge{background:transparent;border:none;padding:0;font-size:.72rem;color:var(--text2);font-weight:400;flex-shrink:0}
/* En topbar estrecho ocultar el TEXTO del horario y dejar solo el círculo de color */
@media (max-width: 599px){.topbar .badge .badge-text{display:none}}
.topbar .badge.live::before{content:'';display:inline-block;width:8px;height:8px;border-radius:50%;background:#06cf9c;margin-right:6px;vertical-align:middle}
.topbar .badge.off::before{content:'';display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--text3);margin-right:6px;vertical-align:middle}

/* Indicador "Estado Cuenta" — pulso ECG. Color/label cambian según quality + modo del bot. Click → drawer */
.meta-chip{
  display:inline-flex;align-items:center;gap:6px;
  background:#fff;border:1px solid var(--border);border-radius:14px;
  padding:3px 10px 3px 8px;cursor:pointer;font:500 .72rem/1.1 inherit;
  color:var(--text2);transition:.15s;white-space:nowrap;flex-shrink:0;
}
.meta-chip:hover{background:#f5f6f6;border-color:var(--border2)}
.meta-chip svg{
  width:16px;height:16px;display:block;color:#8696a0;
  transition:color .25s;flex-shrink:0;
}
.meta-chip .meta-label{font-size:.72rem;font-weight:500}
.meta-chip.ok    {border-color:#a8e5b8;color:#0f5e1f}
.meta-chip.ok    svg{color:#1bb24a}
.meta-chip.warn  {background:#fff5d6;border-color:#f1d488;color:#7a5a00}
.meta-chip.warn  svg{color:#d49a00;animation:metaPulse 1.6s infinite}
.meta-chip.err   {background:#fde2e2;border-color:#f8b4b4;color:#921313}
.meta-chip.err   svg{color:#c53030;animation:metaPulse .9s infinite}
.meta-chip.loading svg{animation:metaPulse 1s infinite}
/* Si el modo del bot NO es normal, agrega un punto pequeño al lado como indicador */
.meta-chip.mode-conservative::after{content:'';width:6px;height:6px;border-radius:50%;background:#d49a00;display:inline-block}
.meta-chip.mode-escalate_all::after{content:'';width:6px;height:6px;border-radius:50%;background:#c53030;display:inline-block;animation:metaPulse 1s infinite}
@keyframes metaPulse{0%,100%{opacity:1}50%{opacity:.4}}
@media(max-width:600px){.meta-chip .meta-label{display:none}.meta-chip{padding:5px}}
.btn-out{background:transparent;border:none;color:var(--text2);padding:5px 7px;border-radius:50%;font-size:.95rem;cursor:pointer;font-family:inherit;transition:.15s;flex-shrink:0;min-width:32px;display:inline-flex;align-items:center;justify-content:center}
.btn-out:hover{background:rgba(0,0,0,.06);color:var(--text)}

/* Menú "⋮" — visible solo en móvil. Los botones secundarios se ocultan en móvil. */
.topbar-menu-toggle{display:none}
.topbar-menu{
  display:none;position:absolute;top:55px;right:8px;z-index:200;
  background:#fff;border:1px solid var(--border);border-radius:10px;
  box-shadow:0 8px 24px rgba(0,0,0,.18);padding:6px;min-width:220px;
  animation:menuIn .15s ease-out;
}
@keyframes menuIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
.topbar-menu.open{display:block}
.topbar-menu button{
  display:flex;align-items:center;gap:11px;width:100%;
  padding:11px 14px;background:transparent;border:none;
  font:500 .9rem inherit;cursor:pointer;text-align:left;
  border-radius:6px;color:var(--text);transition:.1s;
}
.topbar-menu button:hover{background:#f0f2f5}
.topbar-menu button .menu-icon{font-size:1.05rem;width:22px;text-align:center;flex-shrink:0}
.topbar-menu .menu-sep{height:1px;background:var(--border);margin:4px 6px}
.topbar-menu button.danger{color:var(--red)}

@media(max-width:767px){
  .btn-secondary{display:none !important}
  .topbar-menu-toggle{display:inline-flex !important}
  .topbar .logo{font-size:.95rem}
}

/* Sidebar lista conversaciones — estilo WA */
.sidebar{background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.search{padding:8px 12px;background:var(--surface);border-bottom:1px solid var(--border)}
.search input{width:100%;background:var(--card);border:none;border-radius:8px;padding:9px 14px 9px 36px;color:var(--text);font-size:.88rem;outline:none;font-family:inherit;background-image:url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22 fill=%22%238696a0%22><path d=%22M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z%22/></svg>');background-repeat:no-repeat;background-position:14px center;background-size:14px;transition:.15s}
.search input:focus{background-color:#fff}
.conv-list{flex:1;overflow-y:auto;background:var(--surface)}
.conv{padding:12px 16px 12px 14px;cursor:pointer;display:flex;gap:13px;align-items:center;transition:background .1s;border-bottom:1px solid var(--border);position:relative}
.conv:hover{background:#f5f6f6}
.conv.active{background:#f0f2f5}
.conv-avatar{width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,#dfe5e7,#cfd8dc);display:flex;align-items:center;justify-content:center;font-weight:500;color:#54656f;flex-shrink:0;font-size:1.1rem;font-family:inherit}
.conv-info{flex:1;min-width:0}
.conv-name{font-size:1rem;font-weight:400;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3}
.conv-prev{font-size:.84rem;color:var(--text2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:3px;font-weight:400}
.conv-meta{flex-shrink:0;display:flex;flex-direction:column;align-items:flex-end;gap:5px}
.conv-time{font-size:.72rem;color:var(--text2);white-space:nowrap;font-weight:400}
.conv.has-unread .conv-time{color:var(--green)}
.conv.has-unread .conv-name{font-weight:500}
.conv-unread{background:var(--green);color:#fff;font-size:.72rem;font-weight:500;padding:0 6px;border-radius:10px;min-width:20px;height:20px;display:flex;align-items:center;justify-content:center}
.conv .esc-icon{color:var(--yellow);font-size:.85rem}

/* Main chat — estilo WA */
.main{display:flex;flex-direction:column;height:100vh;overflow:hidden;background:var(--bg-chat)}
.chat-head{padding:10px 16px;background:var(--card2);display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--border);min-height:59px}
.chat-head .name{font-size:1rem;font-weight:400;color:var(--text);line-height:1.3;cursor:pointer}
.chat-head .name:hover{text-decoration:none;color:var(--green)}
.chat-head .phone{font-size:.78rem;color:var(--text2);margin-top:2px;font-weight:400}
.chat-head .esc-btn{background:transparent;border:none;color:var(--text2);padding:6px 12px;border-radius:6px;font-size:.78rem;cursor:pointer;font-family:inherit;font-weight:400;transition:.15s}
.chat-head .esc-btn:hover{background:rgba(0,0,0,.05);color:var(--text)}
.chat-head .esc-btn.on{background:rgba(251,192,45,.18);color:#a87800}
.chat-body{
  flex:1;overflow-y:auto;padding:20px 8% 12px;
  background:var(--bg-chat);
  background-image: url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22400%22 height=%22400%22 viewBox=%220 0 400 400%22><g fill=%22%23d4cbb8%22 fill-opacity=%22.20%22><circle cx=%2240%22 cy=%2240%22 r=%221%22/><circle cx=%22120%22 cy=%2280%22 r=%221%22/><circle cx=%22200%22 cy=%2240%22 r=%221%22/><circle cx=%22280%22 cy=%22120%22 r=%221%22/><circle cx=%2280%22 cy=%22200%22 r=%221%22/><circle cx=%22320%22 cy=%22240%22 r=%221%22/><circle cx=%22160%22 cy=%22320%22 r=%221%22/><circle cx=%22240%22 cy=%22360%22 r=%221%22/></g></svg>');
}
.empty{text-align:center;padding:80px 20px;color:var(--text2);background:var(--bg-chat)}
.empty .emoji{font-size:4rem;display:block;margin-bottom:18px;opacity:.3}
.empty h3{font-size:1.5rem;margin-bottom:8px;color:var(--text);font-weight:300}
.empty p{font-size:.88rem}

.msg{margin-bottom:4px;display:flex;padding:0 4px}
.msg.in{justify-content:flex-start}
.msg.out{justify-content:flex-end}
.bubble{
  max-width:65%;padding:6px 9px 8px 11px;border-radius:7.5px;
  font-size:.9rem;line-height:1.35;word-wrap:break-word;white-space:pre-wrap;
  position:relative;color:var(--text);
  box-shadow:var(--bubble-shadow);
  font-family:inherit;
  font-weight:400;
}
.msg.in .bubble{background:var(--surface);border-top-left-radius:0}
.msg.out .bubble{background:var(--green3);border-top-right-radius:0}
.msg.out.bot .bubble{background:#dcf8c6;border-top-right-radius:0}
.bubble-meta{font-size:.6875rem;color:var(--text2);margin-top:2px;margin-left:6px;display:inline-block;float:right;position:relative;top:6px;font-weight:400}
.msg.in .bubble-meta{text-align:left}
.msg.out .bubble-meta{color:rgba(0,0,0,.45)}
.msg.out.bot .bubble-meta{color:rgba(0,0,0,.45)}

.chat-foot{padding:8px 16px;background:var(--card2);display:flex;gap:8px;align-items:flex-end;border-top:1px solid var(--border)}
.chat-foot textarea{flex:1;background:var(--surface);border:none;border-radius:8px;padding:9px 14px;color:var(--text);font-family:inherit;font-size:.95rem;outline:none;resize:none;max-height:120px;min-height:42px;line-height:1.4}
.chat-foot textarea:focus{box-shadow:0 0 0 1px var(--green2)}
.chat-foot button{background:var(--green);color:#fff;border:none;border-radius:50%;width:42px;height:42px;cursor:pointer;font-size:1.05rem;flex-shrink:0;font-family:inherit;font-weight:600;transition:.15s;display:flex;align-items:center;justify-content:center}
.chat-foot button:hover{background:#006a55}
.chat-foot button:disabled{opacity:.4;cursor:not-allowed;background:var(--text3)}

/* Stats row — más compacta para que entre en sidebar de 30% */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;padding:8px 12px;background:var(--surface);border-bottom:1px solid var(--border)}
.stat{background:#fff;border:1px solid var(--border);border-radius:8px;padding:7px 8px;text-align:center;min-width:0;cursor:pointer;transition:.15s;user-select:none}
.stat:hover{background:#f5f6f6}
.stat.active{background:var(--green3);border-color:#a8e5b8}
.stat.active .stat-label{color:#0f5e1f}
.stat-label{font-size:.66rem;color:var(--text2);font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat-val{font-size:1.25rem;font-weight:500;color:var(--green);line-height:1.1;margin-top:2px}
.stat-val.yellow{color:#a87800}
.stat-val.red{color:var(--red)}
.stat-val.blue{color:var(--blue)}

/* Tabs estado WA-style — wrap a varias filas para no cortar */
.status-tabs{display:flex;gap:6px;padding:8px 12px;border-bottom:1px solid var(--border);background:var(--surface);flex-wrap:wrap}
.status-tab{background:transparent;border:1px solid var(--border);border-radius:14px;padding:4px 11px;font-size:.78rem;color:var(--text2);cursor:pointer;white-space:nowrap;font-family:inherit;font-weight:400;transition:.15s;flex-shrink:0}
.status-tab:hover{background:#f5f6f6;color:var(--text)}
.status-tab.active{background:#d9fdd3;color:#0f5a3b;border-color:#a8e5b8;font-weight:500}
.status-tab .cnt{display:inline-block;padding:0 5px;border-radius:8px;margin-left:4px;font-size:.7rem;color:var(--text3);font-weight:400}
.status-tab.active .cnt{color:#0f5a3b}

/* Badge de estado en cards y header */
.status-badge{display:inline-block;padding:2px 9px;border-radius:11px;font-size:.7rem;font-weight:500}
.sb-nuevo{background:#fff3d6;color:#a87800}
.sb-en_conversacion{background:#eceff1;color:#54656f}
.sb-cotizado{background:#e1edff;color:#3b6cb5}
.sb-pagado{background:#d9fdd3;color:#0f5a3b}
.sb-a_despachar{background:#ede1ff;color:#6a3bb5}
.sb-cerrado{background:#f0f2f5;color:#8696a0}
button.status-badge{border:none;cursor:pointer;font-family:inherit;transition:.15s}
button.status-badge::after{content:' ▾';font-size:.6rem;opacity:.6}
button.status-badge:hover{filter:brightness(.96)}

/* Menú flotante de cambio de estado */
.status-menu{
  position:absolute;z-index:70;display:none;
  background:#fff;border:1px solid var(--border);border-radius:8px;
  box-shadow:0 8px 24px rgba(11,20,26,.18);
  padding:4px;min-width:180px;font-size:.85rem;
  animation:menuIn .15s ease-out;
}
.status-menu.open{display:block}
.status-menu .opt{
  display:flex;align-items:center;gap:8px;padding:8px 10px;
  border-radius:6px;cursor:pointer;color:var(--text);
  font-weight:400;transition:background .1s;
}
.status-menu .opt:hover{background:var(--card)}
.status-menu .opt.current{opacity:.55;cursor:default}
.status-menu .opt.current:hover{background:transparent}
.status-menu .opt .dot{
  width:10px;height:10px;border-radius:50%;flex-shrink:0;
}
.status-menu .opt .check{margin-left:auto;color:var(--green);font-weight:500;font-size:.78rem}

/* Botones acción del chat header (solo escalado) */
.chat-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.action-btn{background:transparent;border:1px solid var(--border);color:var(--text2);padding:6px 12px;border-radius:6px;font-size:.72rem;cursor:pointer;font-family:inherit;font-weight:400;transition:.15s}
.action-btn:hover{color:var(--text);border-color:var(--border2)}

/* ───────── BANNER DE ACCIÓN CONTEXTUAL ───────── */
.action-banner{padding:0;border-bottom:1px solid var(--border);background:transparent;transition:max-height .25s ease,padding .25s ease;overflow:hidden;max-height:0;position:relative;z-index:5}
.action-banner.show{padding:14px 18px 14px 22px;max-height:700px;background:#ffffff;box-shadow:0 1px 2px rgba(11,20,26,.06)}
.action-banner.show.collapsed{max-height:62px;padding:10px 18px 10px 22px}
.action-banner.show.collapsed .wiz-steps,
.action-banner.show.collapsed .banner-actions,
.action-banner.show.collapsed .banner-sub{display:none}
.banner-toggle{position:absolute;top:8px;right:14px;background:transparent;border:1px solid var(--border);color:var(--text2);width:30px;height:30px;border-radius:50%;cursor:pointer;font-size:.8rem;display:flex;align-items:center;justify-content:center;transition:.15s;font-family:inherit;z-index:6}
.banner-toggle:hover{background:var(--card);color:var(--text);border-color:var(--border2)}
.action-banner .banner-header{font-size:1rem;font-weight:500;margin-bottom:8px;display:flex;align-items:center;gap:8px;line-height:1.3;color:var(--text)}
.action-banner .banner-sub{font-size:.82rem;color:var(--text2);margin-bottom:14px;line-height:1.55;font-weight:400}
.action-banner .banner-sub b{color:var(--text);font-weight:500}
.action-banner .banner-actions{display:flex;gap:8px;flex-wrap:wrap}
.banner-btn{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:9px 16px;border-radius:24px;font-size:.82rem;cursor:pointer;font-family:inherit;font-weight:400;display:inline-flex;align-items:center;gap:8px;transition:.15s;text-decoration:none;line-height:1.2}
.banner-btn:hover{border-color:var(--green);color:var(--green);background:#f0fdf6}
.banner-btn.prim{background:var(--green);color:#fff;border-color:var(--green);font-weight:500}
.banner-btn.prim:hover{background:#017561;color:#fff;border-color:#017561}
.banner-btn.danger{color:#c53030;border-color:#f8b4b4}
.banner-btn.danger:hover{background:#fff5f5;border-color:#e05252;color:#c53030}
.banner-btn.warning{color:#a87800;border-color:#f1d488}
.banner-btn.warning:hover{background:#fffbeb;border-color:#fbc02d;color:#a87800}
/* Colores de fondo por estado — barra lateral izquierda + leve tinte */
.action-banner.show{border-left:3px solid var(--border2)}
.action-banner.banner-nuevo.show{border-left-color:#fbc02d;background:#fffbeb}
.action-banner.banner-en_conversacion.show{border-left-color:#8696a0;background:#f7f9fa}
.action-banner.banner-cotizado.show{border-left-color:#3b6cb5;background:#eef4ff}
.action-banner.banner-pagado.show{border-left-color:#008069;background:#e8f7ee}
.action-banner.banner-a_despachar.show{border-left-color:#a855f7;background:#f5edff}
.action-banner.banner-cerrado.show{border-left-color:#8696a0;background:#f7f9fa}

/* ───────── WIZARD DE PASOS ───────── */
.wiz-steps{display:flex;flex-direction:column;gap:0;margin-top:8px}
.wiz-step{display:flex;gap:14px;padding:14px 4px;border-left:2px solid var(--border);margin-left:18px;position:relative;align-items:flex-start}
.wiz-step:last-child{border-left:2px solid transparent}
.wiz-icon{position:absolute;left:-19px;top:14px;width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:500;font-size:.9rem;border:1px solid var(--border);background:var(--surface);color:var(--text2);flex-shrink:0;font-family:inherit}
.wiz-step.done .wiz-icon{background:#25D366;color:#fff;border-color:#25D366}
.wiz-step.done{border-left-color:#25D366}
.wiz-step.current .wiz-icon{background:#fbbf24;color:#fff;border-color:#fbbf24;box-shadow:0 0 0 3px rgba(232,168,0,.18)}
.wiz-step.current{border-left-color:#fbbf24}
.wiz-step.blocked .wiz-icon{background:var(--card);color:var(--text3);border-color:var(--border)}
.wiz-step.pending .wiz-icon{background:var(--card);color:var(--text3);border-color:var(--border);opacity:.7}
.wiz-content{padding-left:22px;flex:1;min-width:0}
.wiz-title{font-size:.95rem;font-weight:500;color:var(--text);margin-bottom:4px;line-height:1.3}
.wiz-title-muted{font-size:.92rem;font-weight:400;color:var(--text2);margin-bottom:4px;opacity:.75}
.wiz-sub{font-size:.82rem;color:var(--text2);line-height:1.5;font-weight:400}
.wiz-sub b{color:var(--text);font-weight:500}
.wiz-step.blocked .wiz-sub{color:var(--text3);font-style:italic}

/* ───────── MODAL GENÉRICO ───────── */
.gen-modal-bg{position:fixed;inset:0;background:rgba(11,20,26,.4);backdrop-filter:blur(2px);display:none;align-items:center;justify-content:center;z-index:80;padding:20px}
.gen-modal-bg.show{display:flex}
.gen-modal-box{background:var(--surface);border:none;border-radius:8px;padding:24px;max-width:480px;width:100%;box-shadow:0 4px 20px rgba(11,20,26,.2);max-height:90vh;overflow-y:auto}
.gen-modal-box h3{margin-bottom:14px;font-size:1.1rem;font-weight:500;color:var(--text)}
.gen-modal-box p{font-size:.82rem;color:var(--text2);margin-bottom:12px;line-height:1.5}
.gen-modal-box ul{font-size:.78rem;color:var(--text2);margin-bottom:14px;padding-left:20px;line-height:1.5}
.gen-modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:18px;flex-wrap:wrap}
.gen-modal-input{width:100%;background:var(--card);border:1px solid var(--border2);border-radius:6px;padding:10px 12px;color:var(--text);font-family:inherit;font-size:.85rem;outline:none}
.gen-modal-input:focus{border-color:#25D366}
.warn-text{color:#fbbf24;font-size:.78rem;padding:10px 12px;background:rgba(232,168,0,.08);border-radius:6px;border:1px solid rgba(232,168,0,.25);margin-bottom:10px}

/* ───────── DRAWER PARTNER ───────── */
.partner-drawer{position:fixed;right:0;top:0;bottom:0;width:340px;max-width:90vw;background:var(--surface);border-left:1px solid var(--border);transform:translateX(100%);transition:transform .25s ease;z-index:60;box-shadow:-8px 0 24px rgba(11,20,26,.18);overflow-y:auto}
.partner-drawer.open{transform:translateX(0)}
.partner-drawer .drawer-head{padding:16px 18px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;background:var(--surface);z-index:1}
.partner-drawer .drawer-title{font-family:inherit;font-size:1.05rem;color:var(--text);font-weight:500}
.partner-drawer .close-x{background:none;border:none;color:var(--text2);font-size:1.2rem;cursor:pointer;width:34px;height:34px;border-radius:50%;font-family:inherit}
.partner-drawer .close-x:hover{background:var(--card);color:var(--text)}
.partner-drawer .drawer-body{padding:18px}
.psec{margin-bottom:14px}
.psec h4{font-size:.72rem;color:var(--text2);margin-bottom:6px;font-weight:500}
.psec .val{font-size:.88rem;color:var(--text);line-height:1.4;word-break:break-word;font-weight:400}
.psec .val.muted{color:var(--text2);font-size:.78rem;font-weight:400}
.pf-inp{width:100%;background:#fff;border:1px solid var(--border);border-radius:6px;padding:8px 10px;color:var(--text);font-family:inherit;font-size:.88rem;outline:none;transition:.15s;font-weight:400}
.pf-inp:focus{border-color:var(--green);box-shadow:0 0 0 1px var(--green)}
.chat-head{cursor:default}
.chat-head .name{cursor:pointer}
.chat-head .name:hover{text-decoration:underline}

/* Mobile back button */
.back-btn{display:none;background:transparent;border:none;color:var(--text);font-size:1.3rem;cursor:pointer;padding:6px 10px;font-family:inherit}

/* Píldora flotante móvil — indica unread y pendientes mientras estás dentro de un chat */
.mobile-pill{
  display:none;position:absolute;top:8px;left:50%;transform:translateX(-50%);
  z-index:40;background:var(--green);color:#fff;
  border:none;border-radius:18px;padding:6px 14px;font:500 .78rem/1.1 inherit;
  cursor:pointer;box-shadow:0 4px 12px rgba(11,20,26,.18);
  align-items:center;gap:6px;max-width:calc(100% - 24px);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  animation:pillIn .25s ease-out;
}
@keyframes pillIn{from{transform:translateX(-50%) translateY(-6px);opacity:0}to{transform:translateX(-50%) translateY(0);opacity:1}}
.mobile-pill .pill-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#fff;opacity:.95}
.mobile-pill .pill-sep{opacity:.55;margin:0 2px}

@media(max-width:767px){
  .app.show-chat .sidebar{display:none}
  .app:not(.show-chat) .main{display:none}
  .back-btn{display:inline-block}
  .main{position:relative}
  .app.show-chat .mobile-pill.has-content{display:inline-flex}
}
</style></head>
<body>
<div class="app" id="app">
  <aside class="sidebar">
    <div class="topbar">
      <div class="conv-avatar" style="width:40px;height:40px;font-size:.95rem">P</div>
      <div class="logo">Paracarpinteros</div>
      <span class="badge" id="hoursBadge">·</span>
      <button class="meta-chip loading" id="metaChip" onclick="openMetaDrawer()" title="Estado de la cuenta WhatsApp · click para detalle y control">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
        </svg>
        <span class="meta-label">Cuenta</span>
      </button>
      <button class="btn-out btn-secondary" id="notifBtn" onclick="togglePushSubscription()" title="Notificaciones del panel" style="display:none">🔕</button>
      <button class="btn-out btn-secondary" onclick="openNewContactDrawer()" title="Añadir nuevo contacto / lead">➕</button>
      <button class="btn-out btn-secondary" onclick="openMetaDrawer()" title="Salud del número en Meta">🌡️</button>
      <button class="btn-out btn-secondary" onclick="openKnowledgeDrawer()" title="Conocimientos del bot">📚</button>
      <button class="btn-out btn-secondary" onclick="openBackupsModal()" title="Backups">💾</button>
      <button class="btn-out btn-secondary" onclick="doLogout()" title="Cerrar sesión">⏻</button>
      <button class="btn-out topbar-menu-toggle" onclick="toggleTopbarMenu(event)" title="Más opciones" aria-label="Menú">⋮</button>
    </div>
    <div class="topbar-menu" id="topbarMenu">
      <button onclick="openNewContactDrawer();closeTopbarMenu()"><span class="menu-icon">➕</span> Nuevo contacto</button>
      <button onclick="togglePushSubscription();closeTopbarMenu()" id="menuNotifBtn" style="display:none"><span class="menu-icon">🔕</span> Notificaciones</button>
      <div class="menu-sep"></div>
      <button onclick="openMetaDrawer();closeTopbarMenu()"><span class="menu-icon">🌡️</span> Estado de la cuenta</button>
      <button onclick="openKnowledgeDrawer();closeTopbarMenu()"><span class="menu-icon">📚</span> Conocimientos del bot</button>
      <button onclick="openBackupsModal();closeTopbarMenu()"><span class="menu-icon">💾</span> Backups</button>
      <div class="menu-sep"></div>
      <button class="danger" onclick="doLogout()"><span class="menu-icon">⏻</span> Cerrar sesión</button>
    </div>
    <div class="stats" id="stats"></div>
    <div class="status-tabs" id="statusTabs"></div>
    <div class="search">
      <input id="searchInput" placeholder="🔍 Buscar por nombre o teléfono..." oninput="renderConvs()">
    </div>
    <div class="conv-list" id="convList"></div>
  </aside>

  <main class="main" id="main">
    <button type="button" class="mobile-pill" id="mobilePill" onclick="closeChat()" aria-label="Volver a la lista de chats"></button>
    <div class="empty" id="empty">
      <span class="emoji">💬</span>
      <h3>Seleccioná una conversación</h3>
      <p>Las nuevas aparecerán automáticamente arriba.</p>
    </div>
    <div class="chat-head" id="chatHead" style="display:none">
      <button class="back-btn" onclick="closeChat()">‹</button>
      <div style="flex:1; min-width:0">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap">
          <div class="name" id="chatName" onclick="openPartnerDrawer()" title="Ver ficha del cliente">—</div>
          <button id="chatStatusBadge" class="status-badge sb-nuevo" onclick="openStatusMenu(event)" title="Click para cambiar el estado">·</button>
          <div id="statusMenu" class="status-menu" onclick="event.stopPropagation()"></div>
        </div>
        <div class="phone" id="chatPhone">—</div>
        <div id="partnerInfo" style="font-size:.65rem; color:var(--text3); margin-top:4px; display:none"></div>
      </div>
      <button class="action-btn" onclick="openPartnerDrawer()" title="Ver ficha completa">ℹ Cliente</button>
    </div>
    <div class="action-banner" id="actionBanner"></div>
    <div class="chat-body" id="chatBody" style="display:none"></div>
    <div class="chat-foot" id="chatFoot" style="display:none">
      <textarea id="replyText" placeholder="Escribí un mensaje..." rows="1" onkeydown="onReplyKey(event)" onpaste="onReplyPaste(event)"></textarea>
      <input type="file" id="imgFileInput" accept="image/*" style="display:none" onchange="onImgFileChosen(event)">
      <button onclick="document.getElementById('imgFileInput').click()" id="attachBtn" title="Adjuntar imagen" style="background:transparent;border:none;font-size:1.3rem;cursor:pointer;color:var(--text2);padding:0 6px">📎</button>
      <button onclick="sendReply()" id="sendBtn" title="Enviar (Enter)">➤</button>
    </div>
  </main>
</div>

<script>
let CONVS = [];
let CURRENT_PHONE = null;
let CURRENT_INFO = null;
let CURRENT_PARTNER = null;
let POLL_TIMER = null;
let CURRENT_STATUS_FILTER = '';  // '' = todos
let CURRENT_QUICK_FILTER = '';   // '' | 'unread' | 'escalated' | 'today'

const STATUS_ORDER = ['nuevo','en_conversacion','cotizado','pagado','a_despachar','cerrado'];
const STATUS_LABELS = {
  nuevo:'🆕 Nuevos', en_conversacion:'💬 En conv.', cotizado:'📋 Cotizado',
  pagado:'💰 Pagado', a_despachar:'📦 A despachar', cerrado:'✅ Cerrado'
};
const STATUS_LABELS_FULL = {
  nuevo:'NUEVO', en_conversacion:'EN CONV.', cotizado:'COTIZADO',
  pagado:'PAGADO', a_despachar:'A DESPACHAR', cerrado:'CERRADO'
};

async function api(path, opts){
  const r = await fetch(path, {credentials:'same-origin', ...(opts||{})});
  if(r.status === 401){ location.reload(); return null; }
  if(!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

function fmtTime(ts){
  if(!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if(sameDay) return d.toLocaleTimeString('es-CR',{hour:'2-digit',minute:'2-digit'});
  const diffDays = Math.floor((now-d)/86400000);
  if(diffDays < 7) return d.toLocaleDateString('es-CR',{weekday:'short'});
  return d.toLocaleDateString('es-CR',{day:'2-digit',month:'2-digit'});
}

function initials(name, phone){
  if(name && name.trim()){
    const parts = name.trim().split(/\\s+/);
    const a = (parts[0] && parts[0][0]) ? parts[0][0] : '';
    const b = (parts[1] && parts[1][0]) ? parts[1][0] : '';
    return ((a + b).toUpperCase()) || (phone || '?').slice(-2);
  }
  return (phone || '?').slice(-2);
}

async function loadStats(){
  try{
    const s = await api('/api/stats');
    if(!s) return;
    const f = CURRENT_QUICK_FILTER;
    document.getElementById('stats').innerHTML = `
      <div class="stat ${f===''?'active':''}" onclick="setQuickFilter('')" title="Mostrar todas las conversaciones"><div class="stat-label">Total</div><div class="stat-val">${s.total}</div></div>
      <div class="stat ${f==='unread'?'active':''}" onclick="setQuickFilter('unread')" title="Solo conversaciones sin leer"><div class="stat-label">Sin leer</div><div class="stat-val yellow">${s.unread}</div></div>
      <div class="stat ${f==='escalated'?'active':''}" onclick="setQuickFilter('escalated')" title="Solo conversaciones escaladas a humano"><div class="stat-label">Escaladas</div><div class="stat-val red">${s.escalated}</div></div>
      <div class="stat ${f==='today'?'active':''}" onclick="setQuickFilter('today')" title="Solo conversaciones con actividad hoy"><div class="stat-label">Hoy</div><div class="stat-val blue">${s.msgs_today}</div></div>
    `;
    const hb = document.getElementById('hoursBadge');
    // Solo el círculo + texto compacto. El CSS @media oculta el .badge-text en topbar estrecho.
    if(s.business_hours){ hb.innerHTML = '<span class="badge-text">En horario</span>'; hb.className = 'badge live'; }
    else { hb.innerHTML = '<span class="badge-text">Fuera horario</span>'; hb.className = 'badge off'; }
    // Render tabs estado
    const totalAll = STATUS_ORDER.reduce((a,k)=>a+(s.by_status?.[k]||0),0);
    const tabsEl = document.getElementById('statusTabs');
    tabsEl.innerHTML = `<button class="status-tab ${CURRENT_STATUS_FILTER===''?'active':''}" data-status="" onclick="setStatusFilter('')">Todos <span class="cnt">${totalAll}</span></button>`
      + STATUS_ORDER.map(k => `<button class="status-tab ${CURRENT_STATUS_FILTER===k?'active':''}" data-status="${k}" onclick="setStatusFilter('${k}')">${STATUS_LABELS[k]} <span class="cnt">${s.by_status?.[k]||0}</span></button>`).join('');
  }catch(e){ console.error(e); }
}

let ALL_CONVS = []; // todas las conversaciones (sin filtro) — alimenta la píldora móvil

async function loadConvs(){
  try{
    const qs = CURRENT_STATUS_FILTER ? ('?status='+encodeURIComponent(CURRENT_STATUS_FILTER)) : '';
    CONVS = await api('/api/conversations'+qs) || [];
    renderConvs();
    // ALL_CONVS solo si hay filtro activo (si no, reutilizamos CONVS)
    if(CURRENT_STATUS_FILTER){
      try{ ALL_CONVS = await api('/api/conversations') || []; }
      catch(_){ ALL_CONVS = CONVS; }
    }else{
      ALL_CONVS = CONVS;
    }
    updateMobilePill();
  }catch(e){ console.error(e); }
}

// Píldora flotante móvil — visible solo dentro de un chat. Cuenta unread y conversaciones pendientes de despacho.
function updateMobilePill(){
  const el = document.getElementById('mobilePill');
  if(!el) return;
  let unread = 0, pendingShip = 0, escalated = 0, otherUnreadName = null;
  for(const c of (ALL_CONVS || [])){
    if(c.unread && c.phone !== CURRENT_PHONE){
      unread++;
      if(!otherUnreadName) otherUnreadName = c.name || c.phone;
    }
    if(c.status === 'pagado' || c.status === 'a_despachar') pendingShip++;
    if(c.escalated) escalated++;
  }
  const parts = [];
  if(unread > 0){
    const tail = (unread===1 && otherUnreadName) ? (' · ' + String(otherUnreadName).split(' ')[0]) : '';
    parts.push(`<span class="pill-dot"></span>${unread} sin leer${tail}`);
  }
  if(pendingShip > 0){
    parts.push(`<span>📦 ${pendingShip} por despachar</span>`);
  }
  if(escalated > 0){
    parts.push(`<span>⚠️ ${escalated} escaladas</span>`);
  }
  if(parts.length === 0){
    el.classList.remove('has-content');
    el.innerHTML = '';
    return;
  }
  el.classList.add('has-content');
  el.innerHTML = parts.join('<span class="pill-sep">·</span>');
}

function setStatusFilter(s){
  CURRENT_STATUS_FILTER = s;
  // Repintar tabs activas (loadStats lo hace, pero también acá para feedback inmediato)
  document.querySelectorAll('.status-tab').forEach(b => b.classList.toggle('active', (b.dataset.status||'') === s));
  loadConvs();
}

function setQuickFilter(name){
  // Toggle: si tocas el mismo, lo apagás
  if(CURRENT_QUICK_FILTER === name) name = '';
  CURRENT_QUICK_FILTER = name;
  // Repintar inmediatamente
  document.querySelectorAll('.stats .stat').forEach((el, idx) => {
    const map = ['', 'unread', 'escalated', 'today'];
    el.classList.toggle('active', map[idx] === name);
  });
  renderConvs();
}

function _matchQuickFilter(c){
  switch(CURRENT_QUICK_FILTER){
    case 'unread':    return (c.unread || 0) > 0;
    case 'escalated': return !!c.escalated;
    case 'today': {
      if(!c.last_seen) return false;
      const d = new Date(c.last_seen * 1000);
      const now = new Date();
      return d.toDateString() === now.toDateString();
    }
    default: return true;
  }
}

function renderConvs(){
  const q = (document.getElementById('searchInput').value || '').toLowerCase().trim();
  // Quick filter primero (sobre todas las CONVS), luego search por texto
  const base = (CURRENT_QUICK_FILTER ? CONVS.filter(_matchQuickFilter) : CONVS);
  const filtered = q
    ? base.filter(c => (c.name||'').toLowerCase().includes(q) || (c.phone||'').includes(q))
    : base;
  const list = document.getElementById('convList');
  if(!filtered.length){
    const emptyMsg = CURRENT_QUICK_FILTER
      ? `Sin conversaciones para el filtro <strong>${CURRENT_QUICK_FILTER==='unread'?'Sin leer':CURRENT_QUICK_FILTER==='escalated'?'Escaladas':'Hoy'}</strong><br><button class="status-tab" style="margin-top:10px" onclick="setQuickFilter('')">Quitar filtro</button>`
      : 'Sin conversaciones';
    list.innerHTML = `<div style="padding:30px;text-align:center;color:var(--text3);font-size:.78rem">${emptyMsg}</div>`;
    return;
  }
  list.innerHTML = filtered.map(c => {
    const st = c.status || 'nuevo';
    return `
    <div class="conv ${c.phone === CURRENT_PHONE ? 'active' : ''} ${c.unread>0?'has-unread':''}" onclick="openConv('${c.phone}')">
      <div class="conv-avatar">${initials(c.name, c.phone)}</div>
      <div class="conv-info">
        <div class="conv-name">${escapeHtml(c.name || '+' + c.phone)}</div>
        <div style="display:flex; gap:5px; align-items:center; margin-top:2px">
          <span class="status-badge sb-${st}">${STATUS_LABELS_FULL[st] || st}</span>
          ${c.odoo_sale_order_name ? `<span style="font-size:.55rem; color:var(--text3); font-family:monospace">${c.odoo_sale_order_name}</span>` : ''}
        </div>
        <div class="conv-prev" style="margin-top:3px">${escapeHtml(c.last_message_preview || '')}</div>
      </div>
      <div class="conv-meta">
        <div class="conv-time">${fmtTime(c.last_seen)}</div>
        ${c.unread > 0 ? `<div class="conv-unread">${c.unread}</div>` : ''}
        ${c.escalated ? '<span class="esc-icon" title="Escalada">⚠</span>' : ''}
      </div>
    </div>`;
  }).join('');
}

async function openConv(phone){
  const isSwitching = (CURRENT_PHONE !== phone);
  CURRENT_PHONE = phone;
  if(isSwitching) LAST_MSGS_SIG = '';
  document.getElementById('app').classList.add('show-chat');
  document.getElementById('empty').style.display = 'none';
  document.getElementById('chatHead').style.display = 'flex';
  document.getElementById('chatBody').style.display = '';
  document.getElementById('chatFoot').style.display = 'flex';
  renderConvs();
  updateMobilePill();
  try{
    const d = await api('/api/conversation/' + encodeURIComponent(phone));
    if(!d) return;
    CURRENT_INFO = d.info;
    CURRENT_PARTNER = d.partner;
    document.getElementById('chatName').textContent = d.info?.name || '+' + phone;
    document.getElementById('chatPhone').textContent = '+' + phone;
    renderStatusBadge();
    renderActions();
    renderPartner(d.partner);
    renderMessages(d.messages || [], {force: isSwitching});
    // refresca conteos
    loadConvs();
    loadStats();
  }catch(e){ console.error(e); }
}

function renderStatusBadge(){
  const el = document.getElementById('chatStatusBadge');
  const st = CURRENT_INFO?.status || 'nuevo';
  el.className = 'status-badge sb-' + st;
  el.textContent = STATUS_LABELS_FULL[st] || st;
}

// Menú flotante para cambiar el estado manualmente (botón badge del chat header).
// Útil cuando el flujo automático no llegó al estado real — p.ej. el cliente llamó por
// teléfono y la conversación quedó colgada en "en_conversacion" pero ya está cerrada.
const STATUS_COLORS = {
  nuevo:'#fbc02d', en_conversacion:'#8696a0', cotizado:'#3b6cb5',
  pagado:'#008069', a_despachar:'#a855f7', cerrado:'#54656f'
};
function openStatusMenu(ev){
  if(ev){ ev.stopPropagation(); ev.preventDefault(); }
  const menu = document.getElementById('statusMenu');
  const badge = document.getElementById('chatStatusBadge');
  if(!menu || !badge) return;
  if(menu.classList.contains('open')){
    menu.classList.remove('open');
    return;
  }
  const cur = CURRENT_INFO?.status || 'nuevo';
  menu.innerHTML = STATUS_ORDER.map(s => {
    const isCur = (s === cur);
    return `<div class="opt${isCur?' current':''}" ${isCur?'':`onclick="pickStatus('${s}')"`}>
      <span class="dot" style="background:${STATUS_COLORS[s]||'#999'}"></span>
      <span>${STATUS_LABELS_FULL[s] || s}</span>
      ${isCur?'<span class="check">● actual</span>':''}
    </div>`;
  }).join('');
  // Posicionamos el menú justo debajo del badge.
  const rect = badge.getBoundingClientRect();
  menu.style.top = (rect.bottom + window.scrollY + 6) + 'px';
  menu.style.left = (rect.left + window.scrollX) + 'px';
  menu.classList.add('open');
}
function closeStatusMenu(){
  document.getElementById('statusMenu')?.classList.remove('open');
}
async function pickStatus(s){
  closeStatusMenu();
  if(!s || s === CURRENT_INFO?.status) return;
  await markStatus(s);
}
document.addEventListener('click', (e) => {
  const m = document.getElementById('statusMenu');
  if(!m || !m.classList.contains('open')) return;
  if(!m.contains(e.target) && !e.target.closest('#chatStatusBadge')){
    m.classList.remove('open');
  }
});

function renderActions(){
  renderActionBanner();
  // NO re-renderizar el drawer si está abierto — el polling cada 8s borra lo que el usuario
  // está editando o seleccionando (calculadora envío, datos editables, etc.).
  // El drawer se actualiza solo cuando el usuario lo cierra y vuelve a abrirlo.
}

function renderActionBanner(){
  const banner = document.getElementById('actionBanner');
  if(!banner) return;
  if(!CURRENT_INFO){ banner.classList.remove('show'); banner.innerHTML = ''; return; }
  const st = CURRENT_INFO.status || 'nuevo';
  const name = escapeHtml(CURRENT_INFO.name || ('+' + CURRENT_PHONE));
  const orderName = CURRENT_INFO.odoo_sale_order_name || '';
  const orderId = CURRENT_INFO.odoo_sale_order_id;
  const payment = CURRENT_INFO.payment_meta_parsed || null;
  const escalated = !!CURRENT_INFO.escalated;
  const escBtn = `<button class="banner-btn ${escalated?'warning':''}" onclick="toggleEscalate()">${escalated?'⚠ Bot desactivado (volver a activar)':'👤 Tomar conversación (desactivar bot)'}</button>`;

  const collapsed = localStorage.getItem('bannerCollapsed') === '1';
  banner.className = 'action-banner show banner-' + st + (collapsed ? ' collapsed' : '');
  let html = '';

  if(st === 'nuevo'){
    html = `
      <div class="banner-header">🆕 Cliente nuevo · ${name}</div>
      <div class="banner-sub">Primera conversación. El bot va a responder automáticamente cuando el cliente escriba.</div>
      <div class="banner-actions">
        ${escBtn}
        <button class="banner-btn prim" onclick="openManualQuoteModal()">📋 Crear cotización manual para ${name}</button>
      </div>`;
  } else if(st === 'en_conversacion'){
    html = `
      <div class="banner-header">💬 En conversación con ${name}</div>
      <div class="banner-sub">El bot está atendiendo. Cuando confirme una compra, va a crear cotización automáticamente. O podés crearla manual.</div>
      <div class="banner-actions">
        ${escBtn}
        <button class="banner-btn prim" onclick="openManualQuoteModal()">📋 Crear cotización manual</button>
        <button class="banner-btn danger" onclick="confirmArchive()">✕ Archivar conversación</button>
      </div>`;
  } else if(st === 'cotizado' && orderName){
    html = `
      <div class="banner-header">📋 Cotización borrador · ${escapeHtml(orderName)} · ${name}</div>
      <div class="banner-sub">Esperando pago del cliente. Cuando envíe comprobante, el bot lo detecta y marca como pagado automáticamente.</div>
      <div class="banner-actions">
        ${escBtn}
        <a class="banner-btn" target="_blank" href="https://paracarpinteros.odoo.com/odoo/sales/${orderId}">👁 Ver ${escapeHtml(orderName)} en Odoo</a>
        <button class="banner-btn prim" onclick="confirmAndAdvanceModal()">✅ Confirmar venta ${escapeHtml(orderName)} y crear picking</button>
        <button class="banner-btn danger" onclick="confirmArchive()">✕ Archivar</button>
      </div>`;
  } else if(st === 'cotizado'){
    // Estado cotizado pero sin order_name (raro) → ofrecer crear manual
    html = `
      <div class="banner-header">📋 Cotizado · ${name}</div>
      <div class="banner-sub">No tengo número de cotización registrado. Probablemente fue creada antes del tablero.</div>
      <div class="banner-actions">
        ${escBtn}
        <button class="banner-btn prim" onclick="openManualQuoteModal()">📋 Crear cotización en Odoo</button>
      </div>`;
  } else if(st === 'pagado' || st === 'a_despachar'){
    // Render asíncrono con wizard de pasos
    banner.innerHTML = '<div style="padding:6px 0;color:var(--text3);font-size:.78rem">Cargando estado del pedido...</div>';
    renderShipmentWizard(st, name);
    return;
  } else if(st === 'cerrado'){
    html = `
      <div class="banner-header">✅ Conversación archivada · ${name}</div>
      <div class="banner-sub">Esta conversación está cerrada. Si el cliente vuelve a escribir, pasa automáticamente a "En conversación".</div>
      <div class="banner-actions">
        <button class="banner-btn" onclick="markStatus('en_conversacion')">↩ Reabrir conversación</button>
      </div>`;
  }
  // Toggle button para colapsar/expandir
  const toggleIcon = collapsed ? '▼' : '▲';
  const toggleTitle = collapsed ? 'Expandir wizard' : 'Colapsar wizard';
  banner.innerHTML = `<button class="banner-toggle" onclick="toggleBannerCollapse()" title="${toggleTitle}">${toggleIcon}</button>` + html;
}

function toggleBannerCollapse(){
  const banner = document.getElementById('actionBanner');
  if(!banner) return;
  const isCollapsed = banner.classList.toggle('collapsed');
  localStorage.setItem('bannerCollapsed', isCollapsed ? '1' : '0');
  // Cambiar el ícono inmediatamente
  const btn = banner.querySelector('.banner-toggle');
  if(btn){
    btn.textContent = isCollapsed ? '▼' : '▲';
    btn.title = isCollapsed ? 'Expandir wizard' : 'Colapsar wizard';
  }
}

// ──── Wizard de despacho (estados pagado / a_despachar) ────
let CARRIERS_CACHE = null;

async function renderShipmentWizard(st, name){
  const banner = document.getElementById('actionBanner');
  let wiz;
  try{
    wiz = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/wizard`);
  }catch(e){
    banner.innerHTML = `<div style="padding:10px;color:var(--red)">Error cargando wizard: ${e.message}</div>`;
    return;
  }
  if(!wiz?.ok){
    banner.innerHTML = `<div style="padding:10px;color:var(--red)">Error: ${wiz?.error||''}</div>`;
    return;
  }
  const order = wiz.order;
  const orderConfirmed = order && (order.state === 'sale' || order.state === 'done');
  const hasCarrier = order && order.carrier_id;
  // Pagos acumulados (vienen de CURRENT_INFO, que se carga en openConv)
  const payments = CURRENT_INFO?.payments || [];
  const totalPaid = Number(CURRENT_INFO?.total_paid || 0);
  const orderTotal = order ? Number(order.amount_total || 0) : 0;
  const balance = orderTotal - totalPaid;  // positivo: falta cobrar | 0 o negativo: pagado o sobre-pago

  // ──── PASO 1: Producto/cotización ────
  const stepProduct = order ? `
    <div class="wiz-step done">
      <div class="wiz-icon">✓</div>
      <div class="wiz-content">
        <div class="wiz-title">Producto cotizado · ${escapeHtml(order.name)}</div>
        <div class="wiz-sub">${order.lines.length} línea(s) · subtotal productos: ₡${Number(orderTotal - (hasCarrier ? (order.carrier_price||0) : 0)).toLocaleString('es-CR')}${hasCarrier ? ' · ya incluye envío' : ''}</div>
      </div>
    </div>` : `
    <div class="wiz-step blocked">
      <div class="wiz-icon">!</div>
      <div class="wiz-content">
        <div class="wiz-title">Sin cotización</div>
        <div class="wiz-sub">No hay sale.order asociado. Crealo manual para continuar.</div>
        <button class="banner-btn prim" style="margin-top:8px" onclick="openManualQuoteModal()">📋 Crear cotización manual</button>
      </div>
    </div>`;

  // ──── PASO 2: Tipo de envío ────
  let stepShipping = '';
  if(order){
    if(hasCarrier){
      stepShipping = `
        <div class="wiz-step done">
          <div class="wiz-icon">✓</div>
          <div class="wiz-content">
            <div class="wiz-title">Envío: ${escapeHtml(order.carrier_name)}</div>
            <div class="wiz-sub">Total del pedido con envío: <b>₡${orderTotal.toLocaleString('es-CR')}</b></div>
            <button class="banner-btn" style="margin-top:6px;font-size:.7rem;padding:6px 10px" onclick="openCarrierPicker()">Cambiar tipo de envío</button>
          </div>
        </div>`;
    } else {
      stepShipping = `
        <div class="wiz-step current">
          <div class="wiz-icon">2</div>
          <div class="wiz-content">
            <div class="wiz-title">Elegir tipo de envío y agregarlo al pedido</div>
            <div class="wiz-sub">El precio del envío se suma al total. Después el cliente paga el total completo (producto + envío).</div>
            <button class="banner-btn prim" style="margin-top:8px" onclick="openCarrierPicker()">🚚 Elegir método de envío</button>
          </div>
        </div>`;
    }
  }

  // ──── PASO 3: Pago recibido vs total ────
  let stepPayment = '';
  if(order){
    const payLines = payments.length
      ? payments.map(p => `<div style="font-size:.72rem;color:var(--text2);margin-top:3px">• ₡${Number(p.monto_crc||0).toLocaleString('es-CR')} · ${escapeHtml((p.metodo||'').toUpperCase())}${p.banco?' · '+escapeHtml(p.banco):''}${p.referencia?' · Ref '+escapeHtml(p.referencia):''}</div>`).join('')
      : '';
    if(!hasCarrier){
      // Sin envío aún: no podemos saber el total real
      stepPayment = `
        <div class="wiz-step blocked">
          <div class="wiz-icon">3</div>
          <div class="wiz-content">
            <div class="wiz-title-muted">Cobro al cliente</div>
            <div class="wiz-sub">Primero elegí el envío para conocer el total a cobrar. ${payments.length ? `Ya recibido: ₡${totalPaid.toLocaleString('es-CR')}` : ''}</div>
            ${payLines}
          </div>
        </div>`;
    } else if(balance <= 0.5){
      // Pagado completo (con tolerancia 0.5 colón por redondeos)
      stepPayment = `
        <div class="wiz-step done">
          <div class="wiz-icon">✓</div>
          <div class="wiz-content">
            <div class="wiz-title">Pagado completo · ₡${totalPaid.toLocaleString('es-CR')}</div>
            <div class="wiz-sub">Total del pedido: ₡${orderTotal.toLocaleString('es-CR')}${balance < -0.5 ? ` · <span style="color:#fbbf24">sobre-pago de ₡${Math.abs(balance).toLocaleString('es-CR')}</span>` : ''}</div>
            ${payLines}
          </div>
        </div>`;
    } else {
      // Pago parcial
      const partial = totalPaid > 0;
      stepPayment = `
        <div class="wiz-step current">
          <div class="wiz-icon">3</div>
          <div class="wiz-content">
            <div class="wiz-title">${partial ? 'Falta cobrar la diferencia' : 'Cobrar al cliente'}</div>
            <div class="wiz-sub">
              Total del pedido: <b>₡${orderTotal.toLocaleString('es-CR')}</b><br>
              ${partial ? `Ya recibido: ₡${totalPaid.toLocaleString('es-CR')}<br>` : ''}
              <span style="color:#fbbf24">Falta abonar: <b>₡${balance.toLocaleString('es-CR')}</b></span>
            </div>
            ${payLines}
            <button class="banner-btn prim" style="margin-top:8px" onclick="askBalanceModal(${balance})">📨 Avisar al cliente que falta ₡${balance.toLocaleString('es-CR')}</button>
          </div>
        </div>`;
    }
  }

  // ──── PASO 4: Confirmar pedido en Odoo ────
  let stepConfirm = '';
  if(order){
    if(orderConfirmed){
      stepConfirm = `
        <div class="wiz-step done">
          <div class="wiz-icon">✓</div>
          <div class="wiz-content">
            <div class="wiz-title">Pedido confirmado en Odoo</div>
            <div class="wiz-sub">${escapeHtml(order.name)}${order.picking_name ? ' · Picking ' + escapeHtml(order.picking_name) : ''}</div>
            <a class="banner-btn" style="margin-top:6px;display:inline-block;font-size:.7rem;padding:6px 10px" target="_blank" href="${order.url}">👁 Ver en Odoo</a>
          </div>
        </div>`;
    } else if(hasCarrier && balance <= 0.5){
      // Todo listo para confirmar
      stepConfirm = `
        <div class="wiz-step current">
          <div class="wiz-icon">4</div>
          <div class="wiz-content">
            <div class="wiz-title">Confirmar pedido en Odoo</div>
            <div class="wiz-sub">Ya está pagado y con envío asignado. Confirmá la venta para generar el picking.</div>
            <button class="banner-btn prim" style="margin-top:8px" onclick="confirmAndAdvanceModal()">✅ Confirmar venta ${escapeHtml(order.name)} y crear picking</button>
          </div>
        </div>`;
    } else {
      // Bloqueado
      const reason = !hasCarrier ? 'Primero asigná tipo de envío' : (balance > 0.5 ? `Falta cobrar ₡${balance.toLocaleString('es-CR')}` : 'Completar pasos anteriores');
      stepConfirm = `
        <div class="wiz-step blocked">
          <div class="wiz-icon">4</div>
          <div class="wiz-content">
            <div class="wiz-title-muted">Confirmar pedido en Odoo</div>
            <div class="wiz-sub">${reason}</div>
          </div>
        </div>`;
    }
  }

  // ──── PASO 5: Generar guía ────
  let stepGuide = '';
  if(order){
    if(orderConfirmed){
      stepGuide = `
        <div class="wiz-step current">
          <div class="wiz-icon">5</div>
          <div class="wiz-content">
            <div class="wiz-title">Generar guía de envío</div>
            <div class="wiz-sub">Abrí el panel de envíos e imprimí la etiqueta del picking ${escapeHtml(order.picking_name||'')}.</div>
            <a class="banner-btn prim" style="margin-top:8px;display:inline-block" target="_blank" href="https://panel.paracarpinteros.com/panel-envios.html">🚚 Abrir panel de envíos →</a>
            <button class="banner-btn" style="margin-top:8px" onclick="confirmCloseShipment()">✅ Marcar enviado y cerrar</button>
          </div>
        </div>`;
    } else {
      stepGuide = `
        <div class="wiz-step blocked">
          <div class="wiz-icon">5</div>
          <div class="wiz-content">
            <div class="wiz-title-muted">Generar guía de envío</div>
            <div class="wiz-sub">Disponible después de confirmar el pedido en Odoo</div>
          </div>
        </div>`;
    }
  }

  // Header con info clave
  const headerTitle = st === 'pagado'
    ? (balance > 0.5 ? '💰 Pago parcial recibido — preparar envío' : '💰 Pago completo — preparar envío')
    : '📦 Preparar envío';
  const collapsed = localStorage.getItem('bannerCollapsed') === '1';
  banner.className = 'action-banner show banner-' + st + (collapsed ? ' collapsed' : '');
  const toggleIcon = collapsed ? '▼' : '▲';
  const toggleTitle = collapsed ? 'Expandir wizard' : 'Colapsar wizard';
  banner.innerHTML = `
    <button class="banner-toggle" onclick="toggleBannerCollapse()" title="${toggleTitle}">${toggleIcon}</button>
    <div class="banner-header">${headerTitle} · ${name}</div>
    <div class="banner-sub">Flujo: producto → envío → pago completo → confirmar → generar guía.</div>
    <div class="wiz-steps">
      ${stepProduct}
      ${stepShipping}
      ${stepPayment}
      ${stepConfirm}
      ${stepGuide}
    </div>
    <div class="banner-actions" style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="banner-btn ${CURRENT_INFO?.escalated?'warning':''}" onclick="toggleEscalate()">${CURRENT_INFO?.escalated?'⚠ Bot desactivado':'👤 Tomar conversación'}</button>
      ${order ? `<a class="banner-btn" target="_blank" href="${order.url}">👁 Ver ${escapeHtml(order.name)} en Odoo</a>` : ''}
      ${st === 'pagado' ? `<button class="banner-btn warning" onclick="confirmRevertPayment()">❓ El pago no cuadra</button>` : ''}
    </div>`;
}

function askBalanceModal(amountDue){
  const name = escapeHtml(CURRENT_INFO?.name || '');
  const orderName = escapeHtml(CURRENT_INFO?.odoo_sale_order_name || '');
  const amt = Math.round(amountDue);
  genModalShow(`
    <div class="gen-modal-box">
      <h3>📨 AVISAR AL CLIENTE LA DIFERENCIA</h3>
      <p>Vas a enviar a <b>${name}</b> un mensaje pidiendo que abone <b>₡${amt.toLocaleString('es-CR')}</b> para completar el pedido ${orderName}.</p>
      <label style="display:block;font-size:.65rem;color:var(--text3);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px;font-weight:700">Nota opcional (datos bancarios, sinpe, etc.)</label>
      <textarea id="balanceNote" class="gen-modal-input" rows="3" placeholder="Ej: SINPE Móvil 8606-9717 (Gabriela Brenes) o BCR cuenta 1234..."></textarea>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="sendBalanceReq(${amt})">📨 Enviar mensaje al cliente</button>
      </div>
    </div>`);
}

async function sendBalanceReq(amountDue){
  const note = document.getElementById('balanceNote')?.value || '';
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/ask-balance`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({amount_due: amountDue, note: note})
    });
    if(r?.ok){
      genModalClose();
      alert('✓ Mensaje enviado al cliente. Esperá su comprobante.');
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error||''));
    }
  }catch(e){ alert('Error: '+e.message); }
}

async function openCarrierPicker(){
  // Cache carriers
  if(!CARRIERS_CACHE){
    try{
      const r = await api('/api/odoo/carriers');
      if(!r?.ok){ alert('Error cargando carriers: '+(r?.error||'')); return; }
      CARRIERS_CACHE = r.carriers || [];
    }catch(e){ alert('Error: '+e.message); return; }
  }
  // Dos secciones: ASIGNAR uno al pedido (radio-like) vs COTIZAR varios al cliente (checkbox)
  const optionsHtml = CARRIERS_CACHE.map((c,i) => {
    const priceLbl = c.fixed_price
      ? `~₡${Number(c.fixed_price).toLocaleString('es-CR')}`
      : (c.delivery_type === 'base_on_rule' ? 'por peso/zona' : 'según peso');
    return `
      <div class="carrier-row" data-id="${c.id}" style="display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--border);border-radius:8px;margin-bottom:6px;background:var(--card)">
        <input type="checkbox" class="cq-chk" id="cq-${c.id}" data-id="${c.id}" style="width:18px;height:18px;cursor:pointer;accent-color:#25D366">
        <label for="cq-${c.id}" style="flex:1;cursor:pointer">
          <div style="font-size:.82rem;font-weight:600">${escapeHtml(c.name)}</div>
          <div style="font-size:.7rem;color:var(--text3);margin-top:2px">${priceLbl}</div>
        </label>
        <button class="banner-btn" style="font-size:.7rem;padding:6px 10px" onclick="selectCarrier(${c.id}, ${JSON.stringify(c.name).replace(/"/g,'&quot;')})">Asignar al pedido</button>
      </div>`;
  }).join('');
  genModalShow(`
    <div class="gen-modal-box" style="max-width:560px">
      <h3>🚚 TIPO DE ENVÍO</h3>
      <p style="margin-bottom:14px"><b>Opción A:</b> "Asignar al pedido" en una sola opción → queda fijada en Odoo.<br>
      <b>Opción B:</b> Marcá varias y pulsá <i>Cotizar al cliente</i> → mandamos las opciones al WhatsApp para que él elija.</p>
      <div style="max-height:50vh;overflow-y:auto;margin:14px 0">${optionsHtml}</div>
      <div class="gen-modal-actions" style="justify-content:space-between">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="quoteShippingToClient()">📨 Cotizar marcadas al cliente</button>
      </div>
    </div>`);
}

async function quoteShippingToClient(){
  const checks = document.querySelectorAll('.cq-chk:checked');
  const ids = Array.from(checks).map(c => parseInt(c.dataset.id));
  if(!ids.length){ alert('Marcá al menos una opción para cotizar al cliente.'); return; }
  if(!confirm(`¿Enviar ${ids.length} opción(es) de envío al cliente por WhatsApp?`)) return;
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/quote-shipping`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({carrier_ids: ids})
    });
    if(r?.ok){
      genModalClose();
      alert(`✓ ${r.options} opción(es) enviadas al cliente. Esperá su respuesta para asignar el envío definitivo.`);
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error||''));
    }
  }catch(e){ alert('Error: '+e.message); }
}

async function selectCarrier(carrierId, carrierName){
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/set-carrier`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({carrier_id: carrierId})
    });
    if(r?.ok){
      genModalClose();
      // Recargar wizard
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error||''));
    }
  }catch(e){ alert('Error: '+e.message); }
}

function confirmCloseShipment(){
  const name = escapeHtml(CURRENT_INFO?.name || '');
  genModalShow(`
    <div class="gen-modal-box">
      <h3>✅ MARCAR ENVIADO Y CERRAR</h3>
      <p>¿Confirmás que ya generaste la guía y el pedido fue enviado a ${name}?</p>
      <p>Esto archiva la conversación. Si el cliente vuelve a escribir, reaparece automáticamente.</p>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="genModalClose(); markStatus('cerrado')">✅ Marcar enviado y cerrar</button>
      </div>
    </div>`);
}

// ──── Modal genérico ────
function genModalShow(html){
  let m = document.getElementById('genModal');
  if(!m){
    m = document.createElement('div');
    m.id = 'genModal';
    m.className = 'gen-modal-bg';
    m.onclick = (e) => { if(e.target === m) genModalClose(); };
    document.body.appendChild(m);
  }
  m.innerHTML = html;
  m.classList.add('show');
}
function genModalClose(){
  const m = document.getElementById('genModal');
  if(m) m.classList.remove('show');
}

// ──── Cotización manual ────
function openManualQuoteModal(){
  if(!CURRENT_PHONE) return;
  const name = escapeHtml(CURRENT_INFO?.name || '+' + CURRENT_PHONE);
  genModalShow(`
    <div class="gen-modal-box">
      <h3>📋 COTIZACIÓN MANUAL · ${name}</h3>
      <p>Ingresá uno o varios productos con su código de Odoo (default_code) y cantidad. La cotización se crea en estado borrador y queda asociada al cliente.</p>
      <div id="mqItems">${mqRow()}</div>
      <button class="banner-btn" onclick="addMQRow()" style="margin-bottom:14px">+ Agregar otro producto</button>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="submitManualQuote()">📋 Crear cotización para ${name}</button>
      </div>
    </div>`);
  setTimeout(()=>document.querySelector('.mq-code')?.focus(), 50);
}
function mqRow(){
  return `<div class="mq-row" style="display:flex;gap:8px;margin-bottom:8px">
    <input class="gen-modal-input mq-code" placeholder="Código (ej. A805)" style="flex:1">
    <input class="gen-modal-input mq-qty" placeholder="Cant." type="number" value="1" min="1" style="width:80px">
    <button class="banner-btn" onclick="this.parentElement.remove()" title="Quitar">✕</button>
  </div>`;
}
function addMQRow(){
  document.getElementById('mqItems').insertAdjacentHTML('beforeend', mqRow());
}
async function submitManualQuote(){
  const rows = document.querySelectorAll('.mq-row');
  const items = [];
  rows.forEach(r => {
    const code = r.querySelector('.mq-code').value.trim();
    const qty = parseFloat(r.querySelector('.mq-qty').value) || 1;
    if(code) items.push({codigo: code, cantidad: qty});
  });
  if(!items.length){ alert('Agregá al menos un producto'); return; }
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/manual-quote`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({items})
    });
    if(r?.ok){
      genModalClose();
      alert(`✓ Cotización ${r.order_name} creada · ₡${Number(r.total_crc).toLocaleString('es-CR')}`);
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error || 'desconocido'));
    }
  }catch(e){ alert('Error: ' + e.message); }
}

// ──── Confirmaciones destructivas ────
function confirmAndAdvanceModal(){
  const name = escapeHtml(CURRENT_INFO?.name || '');
  const order = escapeHtml(CURRENT_INFO?.odoo_sale_order_name || '');
  genModalShow(`
    <div class="gen-modal-box">
      <h3>✅ CONFIRMAR VENTA EN ODOO</h3>
      <p>Vas a confirmar la cotización <b>${order}</b> de <b>${name}</b>.</p>
      <ul>
        <li>El sale.order pasa de "Borrador" a "Confirmado"</li>
        <li>Se genera automáticamente el picking de salida</li>
        <li>La conversación pasa a 📦 A despachar</li>
      </ul>
      <div class="warn-text">⚠ Esta acción es difícil de deshacer en Odoo.</div>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="genModalClose(); doConfirmOrder()">✅ Confirmar ${order}</button>
      </div>
    </div>`);
}
async function doConfirmOrder(){
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/confirm-order`, {method:'POST'});
    if(r?.ok){
      alert(`✓ Confirmado: ${r.order_name}\nPicking generado: ${r.picking_name}`);
      markStatus('a_despachar');
    } else {
      alert('Error: ' + (r?.error || 'desconocido'));
    }
  }catch(e){ alert('Error: ' + e.message); }
}
function confirmArchive(){
  const name = escapeHtml(CURRENT_INFO?.name || '');
  genModalShow(`
    <div class="gen-modal-box">
      <h3>✕ ARCHIVAR CONVERSACIÓN</h3>
      <p>¿Marcar la conversación con <b>${name}</b> como cerrada?</p>
      <p>Si el cliente vuelve a escribir, la conversación reaparece automáticamente en "En conversación".</p>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn danger" onclick="genModalClose(); markStatus('cerrado')">✕ Archivar</button>
      </div>
    </div>`);
}
function confirmRevertPayment(){
  genModalShow(`
    <div class="gen-modal-box">
      <h3>❓ PAGO NO CUADRA</h3>
      <p>Vas a revertir la conversación a "En conversación" para revisar con el cliente.</p>
      <p>El comprobante queda guardado en el historial. La conversación deja de aparecer en "💰 Pagado".</p>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn warning" onclick="genModalClose(); markStatus('en_conversacion')">↩ Revertir a "En conversación"</button>
      </div>
    </div>`);
}

// ──── Drawer info partner con datos EDITABLES ────
let PARTNER_FULL_CACHE = null;

async function openPartnerDrawer(){
  let drawer = document.getElementById('partnerDrawer');
  if(!drawer){
    drawer = document.createElement('div');
    drawer.id = 'partnerDrawer';
    drawer.className = 'partner-drawer';
    drawer.style.width = '420px';
    document.body.appendChild(drawer);
  }
  drawer.classList.add('open');
  const info = CURRENT_INFO;
  const p = CURRENT_PARTNER;
  const phone = CURRENT_PHONE || '';
  if(!info){
    drawer.innerHTML = '<div class="drawer-head"><div class="drawer-title">FICHA</div><button class="close-x" onclick="closePartnerDrawer()">✕</button></div><div class="drawer-body">Sin datos</div>';
    return;
  }
  drawer.innerHTML = '<div class="drawer-head"><div class="drawer-title">FICHA DEL CLIENTE</div><div style="display:flex;gap:4px"><button class="close-x" onclick="openPartnerDrawer()" title="Refrescar datos">↻</button><button class="close-x" onclick="closePartnerDrawer()">✕</button></div></div><div class="drawer-body" id="partnerDrawerBody"><div style="padding:20px;text-align:center;color:var(--text2)">Cargando…</div></div>';
  // Cargar datos completos del partner si hay ID
  let full = null;
  if(info.odoo_partner_id){
    try{
      const r = await api('/api/partner/' + info.odoo_partner_id + '/full');
      if(r?.ok) full = r;
    }catch(e){ console.warn('partner full', e); }
  }
  PARTNER_FULL_CACHE = full;
  renderPartnerDrawerBody(full);
}

function renderPartnerDrawerBody(full){
  const body = document.getElementById('partnerDrawerBody');
  if(!body) return;
  const info = CURRENT_INFO;
  const phone = CURRENT_PHONE || '';
  const payment = info.payment_meta_parsed;
  const payments = info.payments || [];
  const totalPaid = info.total_paid || 0;
  const partnerId = info.odoo_partner_id;
  const f = full || {};

  body.innerHTML = `
    <!-- Datos editables del partner -->
    <div class="psec">
      <h4>Nombre</h4>
      <input class="pf-inp" id="pf-name" value="${escapeHtml(f.name || info.name || '')}" placeholder="Nombre completo">
    </div>
    <div class="psec">
      <h4>WhatsApp (no editable)</h4>
      <div class="val">+${escapeHtml(phone)}</div>
    </div>
    <div class="psec">
      <h4>Email</h4>
      <input class="pf-inp" id="pf-email" value="${escapeHtml(f.email || '')}" placeholder="correo@ejemplo.com">
    </div>
    <div class="psec">
      <h4>Teléfono adicional</h4>
      <input class="pf-inp" id="pf-phone" value="${escapeHtml(f.phone || ('+' + phone))}" placeholder="+506 XXXX-XXXX">
    </div>
    <div class="psec">
      <h4>Dirección</h4>
      <input class="pf-inp" id="pf-street" value="${escapeHtml(f.street || '')}" placeholder="Calle, número, señas" style="margin-bottom:6px">
      <input class="pf-inp" id="pf-street2" value="${escapeHtml(f.street2 || '')}" placeholder="Detalles (opcional)">
    </div>
    <div class="psec" style="display:flex;gap:6px">
      <div style="flex:2">
        <h4>Ciudad / Cantón</h4>
        <input class="pf-inp" id="pf-city" value="${escapeHtml(f.city || '')}" placeholder="Ej: Turrialba">
      </div>
      <div style="flex:1">
        <h4>CP</h4>
        <input class="pf-inp" id="pf-zip" value="${escapeHtml(f.zip || '')}" placeholder="30504">
      </div>
    </div>
    ${f.state || f.country ? `<div class="psec"><h4>Provincia / País</h4><div class="val muted">${escapeHtml(f.state||'')}${f.state && f.country?' · ':''}${escapeHtml(f.country||'')}</div></div>` : ''}

    <button class="banner-btn prim" onclick="savePartnerChanges(${partnerId})" style="width:100%;margin-bottom:8px">💾 Guardar cambios en Odoo</button>

    <div style="border-top:1px solid var(--border);margin:14px 0"></div>

    <!-- Estado / pedido en curso -->
    <div class="psec">
      <h4>Estado conversación</h4>
      <div class="val"><span class="status-badge sb-${info.status||'nuevo'}">${STATUS_LABELS_FULL[info.status||'nuevo']}</span></div>
    </div>
    ${full ? `<div class="psec"><h4>Historial Odoo</h4><div class="val">${f.sale_count||0} pedidos · ₡${Math.round(f.total_invoiced||0).toLocaleString('es-CR')} facturado</div></div>` : ''}
    ${info.odoo_sale_order_name ? `
      <div class="psec">
        <h4>Cotización en curso</h4>
        <div class="val">${escapeHtml(info.odoo_sale_order_name)}</div>
        <a class="banner-btn" target="_blank" href="https://paracarpinteros.odoo.com/odoo/sales/${info.odoo_sale_order_id}" style="display:inline-block;margin-top:6px;font-size:.72rem">Abrir cotización en Odoo</a>
      </div>` : ''}

    ${payments.length ? `
      <div class="psec">
        <h4>Pagos recibidos · ₡${totalPaid.toLocaleString('es-CR')}</h4>
        ${payments.map(p => `<div class="val muted" style="margin-top:3px">• ₡${Number(p.monto_crc||0).toLocaleString('es-CR')} · ${escapeHtml((p.metodo||'').toUpperCase())}${p.banco?' · '+escapeHtml(p.banco):''}${p.referencia?' · Ref '+escapeHtml(p.referencia):''}</div>`).join('')}
      </div>` : ''}

    <div style="border-top:1px solid var(--border);margin:14px 0"></div>

    <!-- Calculadora de envío -->
    <div class="psec">
      <h4>💰 Calculadora de envío</h4>
      <p style="font-size:.78rem;color:var(--text2);margin-bottom:8px">Estima el costo del envío según carrier + peso aproximado.</p>
      <div style="display:flex;gap:6px;margin-bottom:8px">
        <select id="calcCarrier" class="pf-inp" style="flex:2">
          <option value="">Elegir carrier...</option>
        </select>
        <input id="calcWeight" class="pf-inp" type="number" placeholder="Peso (g)" value="500" style="flex:1">
      </div>
      <button class="banner-btn" onclick="calcShipping()" style="width:100%;margin-bottom:6px">Calcular precio</button>
      <div id="calcResult" style="font-size:.82rem;color:var(--text);margin-top:8px"></div>
    </div>

    <div style="border-top:1px solid var(--border);margin:14px 0"></div>

    <!-- Atajos del flujo -->
    <div class="psec">
      <h4>Acciones rápidas</h4>
      <button class="banner-btn" onclick="closePartnerDrawer(); openManualQuoteModal()" style="width:100%;margin-bottom:6px">📋 Crear cotización manual</button>
      ${info.odoo_sale_order_id ? `<button class="banner-btn" onclick="closePartnerDrawer(); openCarrierPicker()" style="width:100%;margin-bottom:6px">🚚 Elegir/cotizar envío</button>` : ''}
      ${info.status === 'a_despachar' ? `<a class="banner-btn prim" target="_blank" href="https://panel.paracarpinteros.com/panel-envios.html" style="width:100%;margin-bottom:6px;text-align:center;text-decoration:none">📦 Generar guía ahora →</a>` : ''}
    </div>

    ${partnerId ? `<a class="banner-btn" target="_blank" href="https://paracarpinteros.odoo.com/odoo/contacts/${partnerId}" style="display:block;margin-top:14px;text-align:center">Ver partner #${partnerId} en Odoo →</a>` : ''}
  `;
  // Llenar carriers en el dropdown
  loadCarriersForCalc();
}

async function savePartnerChanges(partnerId){
  if(!partnerId){ alert('Sin partner_id'); return; }
  const data = {
    name: document.getElementById('pf-name').value.trim(),
    email: document.getElementById('pf-email').value.trim(),
    phone: document.getElementById('pf-phone').value.trim(),
    street: document.getElementById('pf-street').value.trim(),
    street2: document.getElementById('pf-street2').value.trim(),
    city: document.getElementById('pf-city').value.trim(),
    zip: document.getElementById('pf-zip').value.trim(),
  };
  try{
    const r = await api('/api/partner/' + partnerId + '/update', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    if(r?.ok){
      alert('✓ Guardado en Odoo: ' + (r.updated || []).join(', '));
      openConv(CURRENT_PHONE);
    } else {
      alert('Error: ' + (r?.error || ''));
    }
  }catch(e){ alert('Error: '+e.message); }
}

async function loadCarriersForCalc(){
  const sel = document.getElementById('calcCarrier');
  if(!sel) return;
  try{
    if(!CARRIERS_CACHE){
      const r = await api('/api/odoo/carriers');
      CARRIERS_CACHE = r?.carriers || [];
    }
    sel.innerHTML = '<option value="">Elegir carrier...</option>' + CARRIERS_CACHE.map(c =>
      `<option value="${c.id}">${escapeHtml(c.name)}</option>`
    ).join('');
  }catch(e){}
}

async function calcShipping(){
  const carrierId = document.getElementById('calcCarrier').value;
  const weight = parseFloat(document.getElementById('calcWeight').value || 500);
  const res = document.getElementById('calcResult');
  if(!carrierId){ res.innerHTML = '<span style="color:var(--text2)">Elegí un carrier</span>'; return; }
  try{
    const r = await api(`/api/odoo/carriers/${carrierId}/quote`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({weight_g: weight, partner_id: CURRENT_INFO?.odoo_partner_id})
    });
    if(r?.ok){
      res.innerHTML = `<div style="padding:8px 10px;background:#e8f7ee;border:1px solid #80d4ad;border-radius:6px"><b>${escapeHtml(r.carrier_name)}</b>: ₡${Number(r.price).toLocaleString('es-CR')}${r.delivery_type==='base_on_rule' ? ' (estimado · varía por zona/peso real)' : ''}</div>`;
    } else {
      res.innerHTML = `<span style="color:var(--red)">Error: ${r?.error||''}</span>`;
    }
  }catch(e){ res.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`; }
}
function closePartnerDrawer(){
  const d = document.getElementById('partnerDrawer');
  if(d) d.classList.remove('open');
}

// ──── Knowledge Base ────
async function openKnowledgeDrawer(){
  let drawer = document.getElementById('knowledgeDrawer');
  if(!drawer){
    drawer = document.createElement('div');
    drawer.id = 'knowledgeDrawer';
    drawer.className = 'partner-drawer';
    drawer.style.width = '500px';
    document.body.appendChild(drawer);
  }
  drawer.innerHTML = `
    <div class="drawer-head">
      <div class="drawer-title">📚 CONOCIMIENTOS DEL BOT</div>
      <button class="close-x" onclick="closeKnowledgeDrawer()">✕</button>
    </div>
    <div class="drawer-body" id="kbBody">
      <div style="text-align:center;color:var(--text2);padding:20px">Cargando...</div>
    </div>`;
  drawer.classList.add('open');
  try{
    const items = await api('/api/knowledge');
    renderKnowledge(items || []);
  }catch(e){
    document.getElementById('kbBody').innerHTML = `<div style="color:var(--red);padding:10px">Error: ${e.message}</div>`;
  }
}
function closeKnowledgeDrawer(){
  const d = document.getElementById('knowledgeDrawer');
  if(d) d.classList.remove('open');
}

// ───────── TERMÓMETRO META ─────────
async function openMetaDrawer(force){
  let drawer = document.getElementById('metaDrawer');
  if(!drawer){
    drawer = document.createElement('div');
    drawer.id = 'metaDrawer';
    drawer.className = 'partner-drawer';
    drawer.style.width = '520px';
    document.body.appendChild(drawer);
  }
  drawer.innerHTML = `
    <div class="drawer-head">
      <div class="drawer-title">📡 ESTADO CUENTA WHATSAPP</div>
      <button class="close-x" onclick="closeMetaDrawer()">✕</button>
    </div>
    <div class="drawer-body" id="metaBody">
      <div style="text-align:center;color:var(--text2);padding:20px">Cargando…</div>
    </div>`;
  drawer.classList.add('open');
  try{
    const [d, modeInfo] = await Promise.all([
      api('/api/meta/health' + (force ? '?force=1' : '')),
      api('/api/bot/mode'),
    ]);
    renderMetaHealth(d, modeInfo);
  }catch(e){
    document.getElementById('metaBody').innerHTML = `<div style="color:var(--red);padding:10px">Error: ${e.message}</div>`;
  }
}

async function setBotMode(mode){
  // Doble confirm si es el destructivo
  if(mode === 'escalate_all'){
    const msg = [
      '⚠️ Vas a desactivar todas las respuestas automáticas del bot.',
      '',
      'Todos los mensajes entrantes quedarán "sin leer" y un humano tendrá que contestar cada uno.',
      '',
      '¿Confirmás?'
    ].join(String.fromCharCode(10));
    if(!confirm(msg)) return;
  }
  try{
    const r = await api('/api/bot/mode', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({mode})
    });
    if(r && r.ok){
      openMetaDrawer(); // refrescar drawer y chip
      updateMetaChip();
    }
  }catch(e){
    alert('No se pudo cambiar el modo: ' + e.message);
  }
}
function closeMetaDrawer(){
  const d = document.getElementById('metaDrawer');
  if(d) d.classList.remove('open');
}

// ───────── NUEVO CONTACTO (lead pre-cargado + wa.me link) ─────────
async function openNewContactDrawer(){
  let drawer = document.getElementById('newContactDrawer');
  if(!drawer){
    drawer = document.createElement('div');
    drawer.id = 'newContactDrawer';
    drawer.className = 'partner-drawer';
    drawer.style.width = '480px';
    document.body.appendChild(drawer);
  }
  drawer.innerHTML = `
    <div class="drawer-head">
      <div class="drawer-title">➕ NUEVO CONTACTO</div>
      <button class="close-x" onclick="closeNewContactDrawer()">✕</button>
    </div>
    <div class="drawer-body">
      <p style="font-size:.78rem;color:var(--text2);line-height:1.5;margin-bottom:14px">
        Cargá un lead en el panel + obtené un link wa.me que podés compartir para que <strong>el cliente te escriba primero</strong> (cumple política de Meta: no se envía nada proactivo).
      </p>

      <div style="margin-bottom:12px">
        <label style="font-size:.72rem;color:var(--text2);font-weight:600;display:block;margin-bottom:4px">NOMBRE *</label>
        <input id="nc_name" class="pf-inp" style="width:100%" placeholder="Ej: Juan Pérez" maxlength="120">
      </div>

      <div style="margin-bottom:12px">
        <label style="font-size:.72rem;color:var(--text2);font-weight:600;display:block;margin-bottom:4px">TELÉFONO *</label>
        <input id="nc_phone" class="pf-inp" style="width:100%;font-family:monospace" placeholder="86069717 o +506 8606 9717" maxlength="20">
        <div style="font-size:.65rem;color:var(--text3);margin-top:3px">Si es CR sin prefijo, se agrega 506 automáticamente</div>
      </div>

      <div style="margin-bottom:12px">
        <label style="font-size:.72rem;color:var(--text2);font-weight:600;display:block;margin-bottom:4px">NOTA INTERNA (opcional)</label>
        <textarea id="nc_note" class="pf-inp" style="width:100%;min-height:60px" placeholder="Ej: Vino por la tapeteadora A704, quedó en confirmar peso..." maxlength="500"></textarea>
        <div style="font-size:.65rem;color:var(--text3);margin-top:3px">No se envía al cliente. Queda como mensaje informativo en la conversación.</div>
      </div>

      <div style="margin-bottom:14px">
        <label style="font-size:.72rem;color:var(--text2);font-weight:600;display:block;margin-bottom:4px">MENSAJE PRE-LLENADO PARA wa.me (opcional)</label>
        <textarea id="nc_wa_message" class="pf-inp" style="width:100%;min-height:60px" placeholder="Hola {nombre}, te escribimos de Paracarpinteros 👋" maxlength="300"></textarea>
        <div style="font-size:.65rem;color:var(--text3);margin-top:3px">Es lo que va a aparecer pre-escrito en el WhatsApp del cliente cuando toque el link. Si lo dejás vacío, se usa un saludo genérico.</div>
      </div>

      <button class="banner-btn prim" id="nc_submit" onclick="submitNewContact()" style="width:100%">Crear contacto</button>

      <div id="nc_result" style="margin-top:16px"></div>
    </div>`;
  drawer.classList.add('open');
  setTimeout(() => document.getElementById('nc_name').focus(), 100);
}

function closeNewContactDrawer(){
  const d = document.getElementById('newContactDrawer');
  if(d) d.classList.remove('open');
}

async function submitNewContact(){
  const name = document.getElementById('nc_name').value.trim();
  const phone = document.getElementById('nc_phone').value.trim();
  const note = document.getElementById('nc_note').value.trim();
  const waMsg = document.getElementById('nc_wa_message').value.trim();
  const resultEl = document.getElementById('nc_result');
  const submitBtn = document.getElementById('nc_submit');

  if(!name){ resultEl.innerHTML = '<div style="color:var(--red);font-size:.78rem">Falta el nombre</div>'; return; }
  if(!phone){ resultEl.innerHTML = '<div style="color:var(--red);font-size:.78rem">Falta el teléfono</div>'; return; }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Creando...';
  resultEl.innerHTML = '';

  try{
    const r = await api('/api/conversation/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({phone, name, note, wa_message: waMsg}),
    });
    if(!r || !r.ok){ throw new Error((r && r.error) || 'Error desconocido'); }

    const partnerStr = r.partner_id ? `<span style="color:var(--green);font-weight:600">Partner Odoo #${r.partner_id}</span>` : '<span style="color:var(--yellow)">Sin partner Odoo</span>';
    const statusStr = r.created_new ? '✅ Contacto nuevo creado' : '↻ Contacto actualizado (ya existía en el panel)';

    resultEl.innerHTML = `
      <div style="background:#e8f5e9;border:1px solid #a8e5b8;border-radius:8px;padding:14px;margin-bottom:10px">
        <div style="font-size:.85rem;font-weight:600;color:#0f5e1f;margin-bottom:6px">${statusStr}</div>
        <div style="font-size:.72rem;color:var(--text2);line-height:1.5">
          <strong>${escapeHtml(r.name)}</strong> · <span style="font-family:monospace">+${r.phone}</span><br>
          ${partnerStr}
        </div>
      </div>

      <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:10px">
        <div style="font-size:.72rem;text-transform:uppercase;color:var(--text2);font-weight:600;margin-bottom:6px">Link wa.me para compartir</div>
        <div style="font-size:.7rem;color:var(--text3);font-family:monospace;word-break:break-all;background:#fff;padding:8px;border-radius:4px;margin-bottom:10px">${escapeHtml(r.wa_link)}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="banner-btn prim" onclick="copyToClip('${r.wa_link.replace(/'/g, "\\\\'")}')">📋 Copiar link</button>
          <button class="banner-btn" onclick="window.open('${r.wa_link.replace(/'/g, "\\\\'")}', '_blank')">↗ Abrir en mi WhatsApp</button>
        </div>
        <div style="font-size:.65rem;color:var(--text3);margin-top:8px;line-height:1.5">
          💡 Pasale el link al cliente por mail, SMS o redes. Cuando toque el link, le abre WhatsApp con el mensaje pre-escrito y nuestro número como destinatario. Apenas envíe, el bot lo atiende con el contexto que cargaste.
        </div>
      </div>

      <div style="display:flex;gap:6px">
        <button class="banner-btn" onclick="openConv('${r.phone}')">Ver conversación en el panel</button>
        <button class="banner-btn" onclick="resetNewContactForm()">+ Otro contacto</button>
      </div>
    `;

    // Refrescar lista de conversaciones para que aparezca la nueva
    loadConvs();
    loadStats();
  }catch(e){
    resultEl.innerHTML = `<div style="color:var(--red);font-size:.78rem;padding:8px;background:#fde2e2;border-radius:6px">Error: ${escapeHtml(e.message || String(e))}</div>`;
  }finally{
    submitBtn.disabled = false;
    submitBtn.textContent = 'Crear contacto';
  }
}

function resetNewContactForm(){
  document.getElementById('nc_name').value = '';
  document.getElementById('nc_phone').value = '';
  document.getElementById('nc_note').value = '';
  document.getElementById('nc_wa_message').value = '';
  document.getElementById('nc_result').innerHTML = '';
  document.getElementById('nc_name').focus();
}

async function copyToClip(text){
  try{
    await navigator.clipboard.writeText(text);
    // Mini toast
    const t = document.createElement('div');
    t.textContent = '✓ Copiado al portapapeles';
    t.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:var(--green);color:#fff;padding:10px 18px;border-radius:8px;font:600 .82rem inherit;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,.25)';
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2000);
  }catch(e){
    alert('No se pudo copiar: ' + e.message);
  }
}
function metaPill(level, label){
  const colors = {
    ok:    {bg:'#c5f0bd', fg:'#0f5e1f', dot:'#1bb24a'},
    warn:  {bg:'#fff2c2', fg:'#7a5a00', dot:'#d49a00'},
    err:   {bg:'#fde2e2', fg:'#921313', dot:'#c53030'},
    muted: {bg:'#e1e6ea', fg:'#3b4a54', dot:'#8696a0'},
  };
  const c = colors[level] || colors.muted;
  return `<span style="display:inline-flex;align-items:center;gap:6px;background:${c.bg};color:${c.fg};border-radius:12px;padding:3px 10px;font:600 .72rem inherit">
    <span style="width:8px;height:8px;border-radius:50%;background:${c.dot}"></span>${label}
  </span>`;
}
function renderMetaHealth(d, modeInfo){
  if(!d || !d.ok){
    document.getElementById('metaBody').innerHTML = '<div style="color:var(--red);padding:10px">Sin datos</div>';
    return;
  }
  const p = d.phone || {};
  const w = d.waba || {};
  const t = d.templates || {};
  const a = d.analytics_7d || {};
  const currentMode = (modeInfo && modeInfo.mode) || 'normal';
  const modes = (modeInfo && modeInfo.modes) || {};

  // Mapas auxiliares
  const verifMap = {verified:'ok', pending:'warn', failed:'err', rejected:'err', unverified:'muted'};
  const verifLevel = verifMap[(w.verification||'').toLowerCase()] || 'muted';
  const throughputLabel = ({STANDARD:'STANDARD · 80 msg/s', HIGH:'HIGH · 1000 msg/s'})[p.throughput] || (p.throughput || '—');
  const nameMap = {APPROVED:'ok', PENDING:'warn', REJECTED:'err'};
  const nameLevel = nameMap[p.name_status] || 'muted';

  const tmplItems = (t.items || []).slice(0, 12).map(it => {
    const sLevel = ({APPROVED:'ok', PENDING:'warn', REJECTED:'err', IN_APPEAL:'warn', PENDING_DELETION:'warn'})[it.status] || 'muted';
    const qScore = (it.quality || 'UNKNOWN');
    return `<tr>
      <td style="padding:6px 4px;font-family:monospace;font-size:.78rem">${escapeHtml(it.name || '—')}</td>
      <td style="padding:6px 4px">${metaPill(sLevel, it.status || '—')}</td>
      <td style="padding:6px 4px;font-size:.72rem;color:var(--text2)">${it.category||'—'} · ${it.language||''}</td>
      <td style="padding:6px 4px;font-size:.72rem;color:var(--text2)">${qScore}</td>
    </tr>`;
  }).join('');

  const catRows = Object.entries(a.by_category || {}).map(([k,v]) =>
    `<tr><td style="padding:4px 4px;font-size:.78rem">${k}</td><td style="padding:4px 4px;text-align:right;font-weight:600">${v}</td></tr>`
  ).join('') || '<tr><td colspan="2" style="color:var(--text3);padding:8px;text-align:center;font-size:.78rem">Sin conversaciones aún en este número</td></tr>';

  const ageStr = d.cached ? `(cache ${d.age_s||0}s)` : '(fresco)';

  // Sugerencia de modo según quality
  const qLevel = (p.quality && p.quality.level) || 'muted';
  const suggested = qLevel === 'err' ? 'escalate_all' : (qLevel === 'warn' ? 'conservative' : 'normal');
  const modeColors = {
    normal:       {bg:'#c5f0bd', border:'#7ac28a', fg:'#0f5e1f', icon:'🤖'},
    conservative: {bg:'#fff2c2', border:'#f1d488', fg:'#7a5a00', icon:'🛡️'},
    escalate_all: {bg:'#fde2e2', border:'#f8b4b4', fg:'#921313', icon:'🚨'},
  };
  const modeButtons = ['normal','conservative','escalate_all'].map(k => {
    const m = modes[k] || {label:k, desc:''};
    const c = modeColors[k];
    const active = currentMode === k;
    const isSuggested = suggested === k && !active;
    return `<button onclick="setBotMode('${k}')" style="
        display:block;width:100%;text-align:left;
        background:${active?c.bg:'#fff'};
        border:2px solid ${active?c.border:'var(--border)'};
        color:${active?c.fg:'var(--text)'};
        border-radius:10px;padding:11px 13px;margin-bottom:8px;cursor:pointer;
        font-family:inherit;transition:.15s;position:relative;
      "${active?'':' onmouseover="this.style.background=\\'#f5f6f6\\'" onmouseout="this.style.background=\\'#fff\\'"'}>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="font-size:1.05rem">${c.icon}</span>
        <strong style="font-size:.92rem">${m.label}</strong>
        ${active ? '<span style="margin-left:auto;font-size:.65rem;font-weight:700;letter-spacing:.5px">● ACTIVO</span>' : ''}
        ${isSuggested ? '<span style="margin-left:auto;background:#3b6cb5;color:#fff;font-size:.6rem;font-weight:700;padding:2px 6px;border-radius:8px">SUGERIDO</span>' : ''}
      </div>
      <div style="font-size:.74rem;color:${active?c.fg:'var(--text2)'};line-height:1.45">${escapeHtml(m.desc||'')}</div>
    </button>`;
  }).join('');

  document.getElementById('metaBody').innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div style="font-size:.7rem;color:var(--text3)">${ageStr}</div>
      <button class="action-btn" onclick="openMetaDrawer(true)">↻ Refrescar</button>
    </div>

    <!-- MODO DEL BOT — sección destacada, lo más accionable -->
    <div style="background:#fff;border:2px solid var(--border);border-radius:10px;padding:12px;margin-bottom:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div style="font-size:.78rem;font-weight:700;color:var(--text);text-transform:uppercase;letter-spacing:.5px">Modo de respuesta del bot</div>
        ${metaPill(qLevel, 'Quality: ' + ((p.quality && p.quality.raw) || '—'))}
      </div>
      ${modeButtons}
      <div style="font-size:.7rem;color:var(--text3);line-height:1.5;margin-top:8px;padding:8px;background:var(--card);border-radius:6px">
        💡 El bot se adapta al estado de la cuenta en Meta. Si <strong>Quality baja a YELLOW</strong>, te sugerimos <strong>Conservador</strong>. Si baja a <strong>RED</strong>, pasar a <strong>Solo humano</strong> mientras se diagnostica qué disparó el problema. El cambio es instantáneo y aplica al próximo mensaje entrante.
      </div>
    </div>

    <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:12px">
      <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text2);font-weight:600;margin-bottom:8px">Número</div>
      <div style="font-size:1.05rem;font-weight:600;color:var(--text);font-family:monospace">${p.number || '—'}</div>
      <div style="font-size:.78rem;color:var(--text2);margin-top:2px">${p.verified_name || '—'}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
        ${metaPill(p.quality?.level || 'muted', 'Quality · ' + (p.quality?.raw || '—'))}
        ${metaPill(nameLevel, 'Nombre · ' + (p.name_status || '—'))}
        ${metaPill('muted', throughputLabel)}
      </div>
      <div style="font-size:.66rem;color:var(--text3);margin-top:8px">${(p.quality && p.quality.label) || ''}</div>
    </div>

    <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:12px">
      <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text2);font-weight:600;margin-bottom:8px">Cuenta de empresa (WABA)</div>
      <div style="font-size:.92rem;font-weight:500;color:var(--text)">${escapeHtml(w.business || w.name || '—')}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
        ${metaPill(verifLevel, 'Verificación · ' + (w.verification || '—'))}
        ${w.business_status ? metaPill(w.business_status==='APPROVED'?'ok':'warn', 'Negocio · ' + w.business_status) : ''}
        ${w.ownership ? metaPill('muted', 'Ownership · ' + w.ownership) : ''}
      </div>
    </div>

    <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text2);font-weight:600">Plantillas (${t.total||0})</div>
        <div style="font-size:.7rem;color:var(--text2)">
          ${(t.approved||0)} OK · ${(t.pending||0)} pend · ${(t.rejected||0)} rej
        </div>
      </div>
      ${tmplItems
        ? `<table style="width:100%;border-collapse:collapse"><tbody>${tmplItems}</tbody></table>`
        : '<div style="color:var(--text3);padding:8px;text-align:center;font-size:.78rem">Sin plantillas</div>'}
    </div>

    <div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text2);font-weight:600">Últimos 7 días</div>
        <div style="font-size:.7rem;color:var(--text2)">${a.total||0} conversaciones${a.cost ? (' · $' + a.cost) : ''}</div>
      </div>
      <table style="width:100%;border-collapse:collapse"><tbody>${catRows}</tbody></table>
    </div>

    <div style="font-size:.7rem;color:var(--text3);line-height:1.5;padding:6px 4px">
      Cache 5 min. Refrescá manualmente si querés datos en vivo.
      Quality rating <strong>RED/YELLOW</strong> = pausá los proactivos y revisá las últimas respuestas del bot.
    </div>
  `;
}
function renderKnowledge(items){
  const body = document.getElementById('kbBody');
  const info = `
    <p style="font-size:.78rem;color:var(--text2);line-height:1.5;margin-bottom:14px">
      Estos textos se le pasan al bot en cada respuesta para que tenga el contexto correcto sobre tu empresa. Editá lo que no sea cierto (ej. si dice algo de un local en San José que no existe), agregá nuevos puntos, o desactivá los que no querés usar.
    </p>
    <button class="banner-btn prim" onclick="addKnowledge()" style="margin-bottom:18px;width:100%">+ Agregar nuevo conocimiento</button>
  `;
  const list = items.map(k => `
    <div class="kb-item" style="border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px;background:var(--card);opacity:${k.active?'1':'.5'}">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
        <span style="background:#e1edff;color:#3b6cb5;padding:2px 8px;border-radius:10px;font-size:.62rem;font-weight:600;text-transform:uppercase">${escapeHtml(k.category)}</span>
        <span style="flex:1"></span>
        <label style="font-size:.7rem;color:var(--text2);cursor:pointer"><input type="checkbox" ${k.active?'checked':''} onchange="toggleKnowledge(${k.id}, this.checked)" style="margin-right:4px;accent-color:var(--green)">activo</label>
        <button class="banner-btn" style="padding:4px 8px;font-size:.7rem" onclick="editKnowledge(${k.id})">✎</button>
        <button class="banner-btn danger" style="padding:4px 8px;font-size:.7rem" onclick="deleteKnowledge(${k.id})">🗑</button>
      </div>
      <div style="font-weight:600;font-size:.88rem;margin-bottom:4px">${escapeHtml(k.title)}</div>
      <div style="font-size:.78rem;color:var(--text2);line-height:1.45;white-space:pre-wrap">${escapeHtml(k.content)}</div>
    </div>
  `).join('');
  body.innerHTML = info + (list || '<div style="text-align:center;color:var(--text2);padding:20px">Sin conocimientos cargados</div>');
}
function addKnowledge(){
  openKnowledgeEditor({id:null, category:'general', title:'', content:'', active:1});
}
async function editKnowledge(id){
  const items = await api('/api/knowledge');
  const k = items.find(x => x.id === id);
  if(!k){ alert('No encontrado'); return; }
  openKnowledgeEditor(k);
}
function openKnowledgeEditor(k){
  genModalShow(`
    <div class="gen-modal-box" style="max-width:560px">
      <h3>${k.id ? '✎ Editar' : '+ Nuevo'} conocimiento</h3>
      <label style="display:block;font-size:.65rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-weight:600">Categoría</label>
      <select id="kbCat" class="gen-modal-input" style="margin-bottom:10px">
        <option value="empresa" ${k.category==='empresa'?'selected':''}>Empresa</option>
        <option value="ubicacion" ${k.category==='ubicacion'?'selected':''}>Ubicación</option>
        <option value="horarios" ${k.category==='horarios'?'selected':''}>Horarios</option>
        <option value="envios" ${k.category==='envios'?'selected':''}>Envíos</option>
        <option value="pagos" ${k.category==='pagos'?'selected':''}>Pagos</option>
        <option value="productos" ${k.category==='productos'?'selected':''}>Productos</option>
        <option value="garantia" ${k.category==='garantia'?'selected':''}>Garantía/devoluciones</option>
        <option value="general" ${k.category==='general'?'selected':''}>General</option>
      </select>
      <label style="display:block;font-size:.65rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-weight:600">Título corto</label>
      <input id="kbTitle" class="gen-modal-input" value="${escapeHtml(k.title||'')}" placeholder="Ej: Tarifa Pymex 1kg a San José" style="margin-bottom:10px">
      <label style="display:block;font-size:.65rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-weight:600">Contenido (el bot lo va a leer y usar)</label>
      <textarea id="kbContent" class="gen-modal-input" rows="8" placeholder="Explicale al bot, en lenguaje natural. Ej: 'Para envíos por Pymex a San José y GAM, hasta 1kg cobramos ₡2,500. Para 1-5kg cobramos ₡4,500. Más de 5kg cotizamos.'">${escapeHtml(k.content||'')}</textarea>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cancelar</button>
        <button class="banner-btn prim" onclick="saveKnowledge(${k.id||'null'})">💾 Guardar</button>
      </div>
    </div>`);
  setTimeout(()=>document.getElementById('kbTitle')?.focus(), 50);
}
async function saveKnowledge(id){
  const category = document.getElementById('kbCat').value;
  const title = document.getElementById('kbTitle').value.trim();
  const content = document.getElementById('kbContent').value.trim();
  if(!title || !content){ alert('Título y contenido son obligatorios'); return; }
  try{
    if(id){
      await api('/api/knowledge/'+id, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({category, title, content})});
    } else {
      await api('/api/knowledge', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({category, title, content})});
    }
    genModalClose();
    openKnowledgeDrawer();  // refrescar
  }catch(e){ alert('Error: '+e.message); }
}
async function toggleKnowledge(id, active){
  try{
    await api('/api/knowledge/'+id, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({active})});
    openKnowledgeDrawer();
  }catch(e){ alert('Error: '+e.message); }
}
async function deleteKnowledge(id){
  if(!confirm('¿Borrar este conocimiento?')) return;
  try{
    await api('/api/knowledge/'+id, {method:'DELETE'});
    openKnowledgeDrawer();
  }catch(e){ alert('Error: '+e.message); }
}

// ──── Backups ────
async function openBackupsModal(){
  genModalShow(`
    <div class="gen-modal-box" style="max-width:580px">
      <h3>💾 BACKUPS</h3>
      <p>El backup contiene toda la base de datos (conversaciones, knowledge, sesiones) + las fotos/audios. Se guarda en el VPS, en <code style="background:var(--card);padding:2px 6px;border-radius:3px;font-size:.78rem">/var/backups/whatsapp-bot/</code>.</p>
      <p>Cron automático todos los días a las 3 AM. Mantiene los últimos 30 backups.</p>
      <div class="gen-modal-actions" style="justify-content:flex-start;margin-bottom:14px">
        <button class="banner-btn prim" onclick="runBackupNow()">▶ Hacer backup ahora</button>
      </div>
      <div id="backupsList">Cargando...</div>
      <div class="gen-modal-actions">
        <button class="banner-btn" onclick="genModalClose()">Cerrar</button>
      </div>
    </div>`);
  loadBackupsList();
}

async function loadBackupsList(){
  const el = document.getElementById('backupsList');
  if(!el) return;
  try{
    const r = await api('/api/backups');
    const list = r.backups || [];
    if(!list.length){
      el.innerHTML = '<div style="color:var(--text2);padding:14px;text-align:center;border:1px dashed var(--border2);border-radius:6px">No hay backups aún. Pulsá "Hacer backup ahora" para crear el primero.</div>';
      return;
    }
    el.innerHTML = `
      <div style="font-size:.7rem;color:var(--text2);letter-spacing:.4px;text-transform:uppercase;margin-bottom:6px;font-weight:600">Últimos backups · ${list.length} archivos</div>
      <div style="max-height:300px;overflow-y:auto">
        ${list.map(b => {
          const d = new Date(b.modified*1000).toLocaleString('es-CR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
          return `<div style="display:flex;gap:10px;align-items:center;padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;background:var(--card)">
            <div style="flex:1;min-width:0">
              <div style="font-weight:600;font-size:.85rem">${escapeHtml(b.filename)}</div>
              <div style="font-size:.72rem;color:var(--text2);margin-top:2px">${d} · ${escapeHtml(b.size_human)}</div>
            </div>
            <a class="banner-btn" href="/api/backups/${encodeURIComponent(b.filename)}" download style="padding:5px 10px;font-size:.72rem">📥 Descargar</a>
          </div>`;
        }).join('')}
      </div>`;
  }catch(e){
    el.innerHTML = `<div style="color:var(--red);padding:10px">Error: ${e.message}</div>`;
  }
}

async function runBackupNow(){
  const el = document.getElementById('backupsList');
  if(el) el.innerHTML = '<div style="text-align:center;color:var(--text2);padding:20px">Generando backup... esto puede tardar varios segundos.</div>';
  try{
    const r = await api('/api/backups/run-now', {method:'POST'});
    if(r?.ok){
      alert(`✓ Backup creado: ${r.filename} (${r.size_human}) en ${r.duration_s}s`);
      loadBackupsList();
    } else {
      alert('Error: ' + (r?.error||''));
      loadBackupsList();
    }
  }catch(e){
    alert('Error: ' + e.message);
    loadBackupsList();
  }
}

async function markStatus(newStatus){
  if(!CURRENT_PHONE) return;
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/status`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({status: newStatus})
    });
    if(r?.ok){
      if(CURRENT_INFO) CURRENT_INFO.status = newStatus;
      renderStatusBadge();
      renderActions();
      loadConvs();
      loadStats();
    }
  }catch(e){ alert('Error: ' + e.message); }
}

async function confirmOrderInOdoo(){
  if(!CURRENT_PHONE || !CURRENT_INFO?.odoo_sale_order_id) return;
  if(!confirm('¿Confirmar la cotización ' + (CURRENT_INFO.odoo_sale_order_name||'') + ' en Odoo? Esto crea el picking de salida.')) return;
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/confirm-order`, {method:'POST'});
    if(r?.ok){
      alert('✓ Confirmado: ' + (r.order_name||'') + ' → picking ' + (r.picking_name||''));
      markStatus('a_despachar');
    } else {
      alert('Error: ' + (r?.error || 'desconocido'));
    }
  }catch(e){ alert('Error: ' + e.message); }
}

function renderPartner(p){
  const el = document.getElementById('partnerInfo');
  if(!p){
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  const isClient = p.sale_count > 0;
  const badge = isClient
    ? `<span style="background:rgba(76,175,110,.15); color:#5cd684; padding:1px 6px; border-radius:8px; font-size:.55rem; font-weight:700">CLIENTE · ${p.sale_count} pedidos</span>`
    : `<span style="background:rgba(232,168,0,.15); color:#fbbf24; padding:1px 6px; border-radius:8px; font-size:.55rem; font-weight:700">NUEVO</span>`;
  const ciudad = p.city ? ' · ' + escapeHtml(p.city) : '';
  const mail = p.email ? ' · ' + escapeHtml(p.email) : '';
  el.innerHTML = `${badge} <a href="${p.url}" target="_blank" style="color:#7eb1ff; text-decoration:none">Odoo #${p.id}</a>${ciudad}${mail}`;
  el.style.display = 'block';
}

function closeChat(){
  document.getElementById('app').classList.remove('show-chat');
  CURRENT_PHONE = null;
  updateMobilePill();
}

async function toggleEscalate(){
  if(!CURRENT_PHONE) return;
  const newVal = !CURRENT_INFO?.escalated;
  try{
    const r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/escalate`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({escalated: newVal})
    });
    if(r){ CURRENT_INFO.escalated = r.escalated; renderActions(); loadStats(); loadConvs(); }
  }catch(e){ alert('Error: '+e.message); }
}

// Firma simple del último estado renderizado, para evitar re-render si nada cambió.
let LAST_MSGS_SIG = '';

function renderMessages(msgs, opts){
  opts = opts || {};
  const body = document.getElementById('chatBody');
  if(!msgs.length){
    body.innerHTML = '<div class="empty"><span class="emoji">📭</span><h3>Sin mensajes</h3></div>';
    LAST_MSGS_SIG = '';
    return;
  }
  // Firma: cantidad + último id + último status + último ts. Si no cambió, no tocamos el DOM
  // (eso evita que el polling cada 8s interrumpa la lectura y borre la selección de texto).
  const last = msgs[msgs.length - 1] || {};
  const sig = msgs.length + '|' + (last.wa_msg_id || last.ts || '') + '|' + (last.status || '') + '|' + (last.text||'').length;
  if(!opts.force && sig === LAST_MSGS_SIG){
    return;
  }
  // Si el usuario tiene texto seleccionado dentro del chat, no re-renderizamos
  // (perdería su selección). Esperamos al siguiente tick del polling.
  if(!opts.force){
    const sel = window.getSelection && window.getSelection();
    if(sel && !sel.isCollapsed && sel.anchorNode && body.contains(sel.anchorNode)){
      return;
    }
  }
  // Capturamos si el usuario estaba leyendo arriba o pegado al fondo, para decidir
  // si scrollear al fondo (mensaje nuevo) o mantener su posición exacta.
  const distFromBottom = body.scrollHeight - body.scrollTop - body.clientHeight;
  const wasAtBottom = distFromBottom < 100;
  const prevScrollTop = body.scrollTop;
  const prevScrollHeight = body.scrollHeight;

  body.innerHTML = msgs.map(m => {
    const time = new Date(m.ts*1000).toLocaleTimeString('es-CR',{hour:'2-digit',minute:'2-digit'});
    const cls = m.direction === 'in' ? 'in' : (m.bot_replied ? 'out bot' : 'out');
    let tick = '';
    if(m.direction === 'out' && m.wa_msg_id){
      const st = (m.status || 'sent').toLowerCase();
      if(st === 'failed') tick = ' <span title="No entregado" style="color:#d33;font-weight:700">!</span>';
      else if(st === 'read') tick = ' <span title="Leído" style="color:#53bdeb;font-weight:700;letter-spacing:-2px">✓✓</span>';
      else if(st === 'delivered') tick = ' <span title="Entregado" style="color:rgba(0,0,0,.45);font-weight:700;letter-spacing:-2px">✓✓</span>';
      else tick = ' <span title="Enviado" style="color:rgba(0,0,0,.45);font-weight:700">✓</span>';
    }
    const meta = (m.direction === 'out' && m.bot_replied ? '🤖 ' + time : time) + tick;
    let bubble = '';
    if(m.media_path){
      const isAudio = /\\.(ogg|oga|mp3|m4a|mp4|wav)$/i.test(m.media_path);
      if(isAudio){
        const transcript = (m.text||'').replace(/^🎙️\\s*/,'').replace(/^\\[AUDIO\\][^a-zA-Z0-9]*/,'');
        bubble = `<div class="bubble" style="padding:8px 10px">
          <audio controls preload="none" src="/media/${encodeURIComponent(m.media_path)}" style="width:240px;max-width:100%;display:block;margin-bottom:6px"></audio>
          <div style="font-size:.7rem; opacity:.85; line-height:1.35">🎙️ ${escapeHtml(transcript)}</div>
        </div>`;
      } else {
        bubble = `<div class="bubble" style="padding:6px"><img src="/media/${encodeURIComponent(m.media_path)}" style="max-width:240px; max-height:300px; border-radius:10px; display:block" alt="foto" loading="lazy"><div style="padding:4px 6px 2px; font-size:.7rem; opacity:.85">${escapeHtml(m.text||'').replace(/^\\[FOTO\\]\\s*/,'')}</div></div>`;
      }
    } else {
      bubble = `<div class="bubble">${escapeHtml(m.text||'')}</div>`;
    }
    return `<div class="msg ${cls}"><div>${bubble}<div class="bubble-meta">${meta}</div></div></div>`;
  }).join('');
  LAST_MSGS_SIG = sig;
  if(opts.force || wasAtBottom){
    body.scrollTop = body.scrollHeight;
  } else {
    // Mantener posición visual: ajustar por delta de altura por si entraron mensajes arriba.
    body.scrollTop = prevScrollTop + (body.scrollHeight - prevScrollHeight);
  }
}

function escapeHtml(s){
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function sendReply(){
  const ta = document.getElementById('replyText');
  const text = ta.value.trim();
  if(!CURRENT_PHONE) return;
  if(!text && !PENDING_IMAGE) return;
  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  try{
    let r;
    if(PENDING_IMAGE){
      const fd = new FormData();
      fd.append('image', PENDING_IMAGE.blob, PENDING_IMAGE.name);
      if(text) fd.append('caption', text);
      const resp = await fetch(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/reply-image`, {
        method:'POST', credentials:'same-origin', body: fd
      });
      if(resp.status === 401){ location.reload(); return; }
      r = await resp.json().catch(() => ({ok:false, error:'respuesta inválida'}));
    } else {
      r = await api(`/api/conversation/${encodeURIComponent(CURRENT_PHONE)}/reply`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text})
      });
    }
    if(r?.ok){
      ta.value = '';
      ta.style.height = 'auto';
      clearPendingImage();
      openConv(CURRENT_PHONE);  // recarga
    } else {
      alert('Error: ' + (r?.error || 'desconocido'));
    }
  }catch(e){ alert('Error: '+e.message); }
  finally{ btn.disabled = false; }
}

function onReplyKey(e){
  // Enter = enviar (como WhatsApp). Shift+Enter = salto de línea.
  if(e.key === 'Enter' && !e.shiftKey && !e.isComposing){
    e.preventDefault();
    sendReply();
    return;
  }
  // Auto-resize textarea
  setTimeout(() => {
    const ta = e.target;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
  }, 0);
}

// ───── Pegar / adjuntar imágenes ─────
let PENDING_IMAGE = null; // {blob, name, mime, dataUrl}

function onReplyPaste(e){
  const items = (e.clipboardData || window.clipboardData)?.items || [];
  for(const it of items){
    if(it.kind === 'file' && it.type.startsWith('image/')){
      e.preventDefault();
      const blob = it.getAsFile();
      if(blob) showImagePreview(blob);
      return;
    }
  }
}

function onImgFileChosen(e){
  const f = e.target.files && e.target.files[0];
  if(f) showImagePreview(f);
  e.target.value = ''; // reset
}

function showImagePreview(blob){
  const reader = new FileReader();
  reader.onload = ev => {
    PENDING_IMAGE = {blob, name: blob.name || 'imagen.jpg', mime: blob.type || 'image/jpeg', dataUrl: ev.target.result};
    let box = document.getElementById('imgPreviewBox');
    if(!box){
      box = document.createElement('div');
      box.id = 'imgPreviewBox';
      box.style.cssText = 'position:absolute;bottom:60px;left:10px;right:10px;background:#fff;border:1px solid var(--border);border-radius:8px;padding:8px;display:flex;gap:10px;align-items:center;box-shadow:0 2px 8px rgba(0,0,0,.15);z-index:50';
      const composer = document.querySelector('.composer') || document.getElementById('replyText').parentElement;
      composer.style.position = 'relative';
      composer.appendChild(box);
    }
    box.innerHTML = `
      <img src="${ev.target.result}" style="width:60px;height:60px;object-fit:cover;border-radius:6px">
      <div style="flex:1;font-size:.85rem;color:var(--text)">Imagen lista para enviar<br><span style="font-size:.7rem;color:var(--text2)">Escribí un caption (opcional) y dale Enter o ➤</span></div>
      <button onclick="clearPendingImage()" style="background:transparent;border:none;font-size:1.1rem;cursor:pointer;color:var(--text2)" title="Descartar">✕</button>
    `;
  };
  reader.readAsDataURL(blob);
}

function clearPendingImage(){
  PENDING_IMAGE = null;
  const box = document.getElementById('imgPreviewBox');
  if(box) box.remove();
}

async function doLogout(){
  await fetch('/logout', {method:'POST', credentials:'same-origin'});
  location.reload();
}

// Menú ⋮ del topbar (solo móvil) — toggle + cerrar al click fuera
function toggleTopbarMenu(ev){
  if(ev) ev.stopPropagation();
  const m = document.getElementById('topbarMenu');
  if(!m) return;
  m.classList.toggle('open');
}
function closeTopbarMenu(){
  const m = document.getElementById('topbarMenu');
  if(m) m.classList.remove('open');
}
document.addEventListener('click', (e) => {
  const m = document.getElementById('topbarMenu');
  if(!m || !m.classList.contains('open')) return;
  // Si el click fue fuera del menú y fuera del botón toggle, cerrar
  if(!m.contains(e.target) && !e.target.closest('.topbar-menu-toggle')){
    m.classList.remove('open');
  }
});

// Polling cada 8s para refrescar
function startPolling(){
  if(POLL_TIMER) clearInterval(POLL_TIMER);
  POLL_TIMER = setInterval(() => {
    loadStats();
    loadConvs();
    if(CURRENT_PHONE){
      api('/api/conversation/' + encodeURIComponent(CURRENT_PHONE))
        .then(d => { if(d){ renderMessages(d.messages || []); CURRENT_INFO = d.info; CURRENT_PARTNER = d.partner; renderStatusBadge(); renderActions(); }})
        .catch(()=>{});
    }
  }, 8000);
}

// Diagnóstico visible: si hay error JS, mostramos un banner rojo arriba del panel.
// Así no hace falta abrir DevTools para detectar bugs que rompen el polling.
(function(){
  function showErr(msg, src){
    let b = document.getElementById('jsErrBanner');
    if(!b){
      b = document.createElement('div');
      b.id = 'jsErrBanner';
      b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#c53030;color:#fff;padding:8px 14px;font:600 .75rem/1.3 monospace;text-align:center;box-shadow:0 2px 6px rgba(0,0,0,.3);max-height:120px;overflow:auto';
      document.body.appendChild(b);
    }
    const line = '[JS error] ' + msg + (src ? ' · ' + src : '') + ' · ' + new Date().toLocaleTimeString();
    const NL = String.fromCharCode(10);
    b.innerText = (line + NL + (b.innerText || '')).split(NL).slice(0,5).join(NL);
  }
  window.addEventListener('error', e => showErr(e.message, e.filename ? (e.filename + ':' + e.lineno) : ''));
  window.addEventListener('unhandledrejection', e => showErr('Promise: ' + (e.reason && (e.reason.message || e.reason)), ''));
})();

loadStats();
loadConvs();
startPolling();

// ───────── Meta chip (termómetro siempre visible) ─────────
let META_CHIP_TIMER = null;
async function updateMetaChip(){
  const chip = document.getElementById('metaChip');
  if(!chip) return;
  try{
    const [d, modeInfo] = await Promise.all([
      api('/api/meta/health'),
      api('/api/bot/mode'),
    ]);
    if(!d || !d.ok){ chip.className = 'meta-chip loading'; chip.querySelector('.meta-label').textContent = 'Cuenta'; return; }
    const lvl = (d.phone && d.phone.quality && d.phone.quality.level) || 'muted';
    const raw = (d.phone && d.phone.quality && d.phone.quality.raw) || '—';
    const tier = (d.phone && d.phone.throughput) || '';
    const conv7 = (d.analytics_7d && d.analytics_7d.total) || 0;
    const mode = (modeInfo && modeInfo.mode) || 'normal';
    // Base: nivel de quality
    let cls = 'meta-chip ' + (lvl === 'ok' ? 'ok' : (lvl === 'warn' ? 'warn' : (lvl === 'err' ? 'err' : 'loading')));
    // Modificador: si el modo del bot está alterado lo añadimos como clase
    if(mode !== 'normal') cls += ' mode-' + mode;
    chip.className = cls;
    // Texto siempre "Cuenta", el dot adyacente comunica el modo
    chip.querySelector('.meta-label').textContent = 'Cuenta';
    const modeStr = mode === 'normal' ? 'modo normal' : (mode === 'conservative' ? 'modo CONSERVADOR' : 'modo SOLO HUMANO');
    chip.title = `Cuenta WhatsApp\nQuality: ${raw}${tier ? ' · ' + tier : ''}\n${conv7} conversaciones últimos 7 días\nBot: ${modeStr}\n— Click para detalle y control —`;
  }catch(e){
    chip.className = 'meta-chip loading';
    chip.querySelector('.meta-label').textContent = 'Cuenta';
  }
}
function startMetaChipPolling(){
  if(META_CHIP_TIMER) clearInterval(META_CHIP_TIMER);
  updateMetaChip();
  META_CHIP_TIMER = setInterval(updateMetaChip, 5 * 60 * 1000); // 5 min (alineado con cache backend)
}
startMetaChipPolling();

// ───────── PWA: Service Worker + Web Push ─────────
let SW_REG = null;

function urlBase64ToUint8Array(base64String){
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for(let i=0;i<raw.length;i++) arr[i]=raw.charCodeAt(i);
  return arr;
}

async function registerSW(){
  if(!('serviceWorker' in navigator)) return null;
  try{
    const reg = await navigator.serviceWorker.register('/sw.js', {scope:'/'});
    SW_REG = reg;
    // Listener: SW pide abrir un chat al hacer click en una notificación
    navigator.serviceWorker.addEventListener('message', (ev) => {
      const d = ev.data || {};
      if(d.type === 'open-chat' && d.phone){
        try{ openConv(d.phone); }catch(_){}
      }
    });
    return reg;
  }catch(e){
    console.warn('[sw] register failed', e);
    return null;
  }
}

async function refreshNotifButton(){
  const btn = document.getElementById('notifBtn');
  const menuBtn = document.getElementById('menuNotifBtn');
  if(!btn) return;
  if(!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)){
    btn.style.display = 'none';
    if(menuBtn) menuBtn.style.display = 'none';
    return;
  }
  btn.style.display = 'inline-flex';
  if(menuBtn) menuBtn.style.display = 'flex';
  const reg = SW_REG || await navigator.serviceWorker.getRegistration();
  let subbed = false;
  if(reg){
    try{ const sub = await reg.pushManager.getSubscription(); subbed = !!sub; }catch(_){}
  }
  const perm = Notification.permission;
  if(subbed && perm === 'granted'){
    btn.textContent = '🔔'; btn.title = 'Notificaciones activadas (click para desactivar)';
  }else if(perm === 'denied'){
    btn.textContent = '🚫'; btn.title = 'Notificaciones bloqueadas — habilitalas en ajustes del navegador';
  }else{
    btn.textContent = '🔕'; btn.title = 'Activar notificaciones del panel';
  }
}

async function togglePushSubscription(){
  if(!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)){
    alert('Tu navegador no soporta notificaciones push.');
    return;
  }
  const reg = SW_REG || await navigator.serviceWorker.getRegistration() || await registerSW();
  if(!reg){ alert('No se pudo registrar el service worker.'); return; }

  const existing = await reg.pushManager.getSubscription();
  if(existing){
    // Desactivar
    try{
      await fetch('/api/push/unsubscribe', {
        method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({endpoint: existing.endpoint}),
      });
      await existing.unsubscribe();
    }catch(e){ console.warn(e); }
    refreshNotifButton();
    return;
  }

  if(Notification.permission === 'denied'){
    alert('Las notificaciones están bloqueadas. Habilitalas en los ajustes del navegador y volvé a intentar.');
    return;
  }
  const perm = await Notification.requestPermission();
  if(perm !== 'granted'){ refreshNotifButton(); return; }

  // Pedir VAPID y suscribir
  try{
    const r = await fetch('/api/push/vapid-key', {credentials:'same-origin'});
    if(!r.ok){ alert('VAPID no configurado en el servidor.'); return; }
    const {key} = await r.json();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    const subJson = sub.toJSON();
    await fetch('/api/push/subscribe', {
      method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        endpoint: sub.endpoint,
        keys: subJson.keys,
        ua: navigator.userAgent,
      }),
    });
    refreshNotifButton();
    // Push de prueba opcional
    fetch('/api/push/test', {method:'POST', credentials:'same-origin'}).catch(()=>{});
  }catch(e){
    console.warn('[push subscribe] error', e);
    alert('No se pudo activar las notificaciones: ' + (e.message || e));
  }
}

// Arrancar PWA en background
registerSW().then(() => refreshNotifButton());
</script>
</body></html>
"""


# ───────── ROOT (panel) ─────────
@app.get("/", response_class=HTMLResponse)
async def root(session: Optional[str] = Cookie(None)):
    if not _session_is_valid(session):
        return HTMLResponse(LOGIN_HTML)
    return HTMLResponse(PANEL_HTML)
