# -*- coding: utf-8 -*-
# Multi-courier panel (pymex/tavo/dual) — refactor 2026-05-01
"""
API REST para el panel de envíos.
Todos los endpoints requieren header X-Panel-Token (login).

Endpoints:
  GET  /api/carriers                       Lista carriers detectados (pymex/tavo/dual)
  GET  /api/pendientes?courier=...         Lista pickings sin guía (todos couriers o filtrado)
  GET  /api/picking/{id}                   Detalle: cliente + líneas + fotos
  POST /api/picking/{id}/preparar          Marca/desmarca línea como preparada
  POST /api/picking/{id}/generar           Genera guía Pymexpress (API Correos CR)
  POST /api/picking/{id}/registrar-manual  Adjunta etiqueta Tavo/Dual al picking + tracking
  POST /api/manual/generar                 Genera guía Pymex manual sin pedido
  GET  /api/historico?courier=&q=&desde=&hasta=   Lista guías con filtros
  GET  /api/calendario?mes=YYYY-MM&courier=       Guías agrupadas por día
  GET  /api/buscar-partner?q=...           Autocompletar partners CR
  GET  /api/producto-imagen/{id}           Imagen miniatura producto (PNG)
  GET  /api/picking/{id}/etiqueta          Devuelve PDF de etiqueta ya generada
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Body, Depends, HTTPException, Header, Response
from pydantic import BaseModel, Field
import requests as _requests  # alias para evitar shadow con otras importaciones

from .config import settings
from .processor import Processor, _in_flight, _in_flight_lock, build_dest_direccion, _m2o_name

_logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api', tags=['panel'])

# Etiquetas en español para los estados de stock.picking de Odoo.
# Se envían como 'state_label' junto al 'state' raw para que el panel los muestre
# sin tener que traducir en cliente.
STATE_LABELS = {
    'draft': 'Borrador',
    'waiting': 'En espera',
    'confirmed': 'Esperando stock',
    'assigned': 'Listo',
    'done': 'Hecho',
    'cancel': 'Cancelado',
}


def _state_label(state: Optional[str]) -> str:
    return STATE_LABELS.get(state or '', state or '')


def _cr_codes_from_zip(zip_str: Optional[str]) -> dict:
    """
    Deriva los códigos provincia/cantón/distrito de un ZIP CR (5 dígitos).

    Formato oficial: PCCDD
      - P = provincia (1 dígito, 1=SJO, 2=Alajuela, 3=Cartago, 4=Heredia,
            5=Guanacaste, 6=Puntarenas, 7=Limón)
      - CC = cantón (2 dígitos)
      - DD = distrito (2 dígitos)

    Lo usamos para precargar los selects del modal de generación de guía sin
    depender de campos custom en el partner (que no existen en este Odoo
    porque el módulo delivery_correos_cr no está instalado allá).

    Devuelve {'provincia_code', 'canton_code', 'distrito_code'} con strings
    vacíos si el ZIP no tiene formato válido.
    """
    z = ''.join(c for c in (zip_str or '') if c.isdigit())
    if len(z) != 5 or z[0] not in '1234567':
        return {'provincia_code': '', 'canton_code': '', 'distrito_code': ''}
    return {
        'provincia_code': z[0],
        'canton_code': z[1:3],
        'distrito_code': z[3:5],
    }

# ───────── PANEL AUTH (login con password compartido) ─────────
# Password leído de .env: PANEL_PASSWORD
PANEL_PASSWORD = os.environ.get('PANEL_PASSWORD', '')
# SESSION_SECRET: env var dedicado; si no está, derivamos del api_token con
# un dominio distinto para no compartir el mismo secreto entre dos sistemas.
_explicit_secret = os.environ.get('PANEL_SESSION_SECRET', '')
if _explicit_secret:
    SESSION_SECRET = _explicit_secret
else:
    SESSION_SECRET = hashlib.sha256(
        (settings.api_token + '::panel-session').encode()
    ).hexdigest()
SESSION_TTL_S = 30 * 24 * 3600  # 30 días (sesión persistente — uso interno)

def _sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def _make_token() -> str:
    expires = int(time.time()) + SESSION_TTL_S
    payload = f'{expires}'
    sig = _sign(payload)
    return f'{payload}.{sig}'

def _verify_token(token: Optional[str]) -> bool:
    if not token or '.' not in token:
        return False
    try:
        payload, sig = token.split('.', 1)
        expected = _sign(payload)
        if not hmac.compare_digest(sig, expected):
            return False
        expires = int(payload)
        return expires > int(time.time())
    except Exception:
        return False

def verify_session(x_panel_token: Optional[str] = Header(None)):
    """Auth para endpoints del panel. Exige cookie/header del login."""
    if not _verify_token(x_panel_token):
        raise HTTPException(status_code=401, detail='Sesión inválida o expirada')


class LoginPayload(BaseModel):
    password: str

@router.post('/auth/login')
def login(payload: LoginPayload):
    if not PANEL_PASSWORD:
        raise HTTPException(500, 'PANEL_PASSWORD no configurada en el servidor')
    # Comparación constante para evitar timing attacks
    ok = hmac.compare_digest(payload.password.encode(), PANEL_PASSWORD.encode())
    if not ok:
        raise HTTPException(401, 'Contraseña incorrecta')
    return {'ok': True, 'token': _make_token(), 'ttl_s': SESSION_TTL_S}

@router.get('/auth/check')
def auth_check(x_panel_token: Optional[str] = Header(None)):
    return {'valid': _verify_token(x_panel_token)}

# Estado: una sola instancia de Processor reutilizada
_processor: Optional[Processor] = None
def get_processor() -> Processor:
    global _processor
    if _processor is None:
        _processor = Processor()
    return _processor

# ───────── Carriers map (Pymex/Tavo/Dual) ─────────
# Cache en memoria del processo. Se refresca cada hora.
_carriers_cache: dict = {'data': None, 'ts': 0}
CARRIER_CACHE_TTL = 3600  # 1 hora

# Patrones de match por nombre (case-insensitive). Orden de evaluación importante:
# tavo y dual primero (más específicos); pymex captura cualquier "Correos" / "Encomienda" residual.
# 'mensajería privada' y 'retirada en almacén' NO matchean (a propósito).
CARRIER_PATTERNS_ORDER = ['tavo', 'dual', 'pymex']
CARRIER_PATTERNS = {
    'tavo':   ['tavo', 'transtusa'],
    'dual':   ['dual'],
    'pymex':  ['pymex', 'correos', 'encomienda nacional', 'encomienda regional'],
}
# Multi-id por courier (ej. pymex puede tener "Pymexpress" + "Sucursal encomienda")
CARRIER_TRACKING_PREFIX = {
    'pymex': 'PY',
    'tavo':  'TV',
    'dual':  'DG',
}
COURIER_HUMAN = {'pymex': 'Pymexpress', 'tavo': 'Encomiendas Tavo', 'dual': 'Dual Global'}

def _detect_carriers() -> dict:
    """
    Devuelve {'pymex': id, 'tavo': id, 'dual': id} matcheando delivery.carrier por nombre.
    Si hay varios pymex (ej. domicilio + sucursal), guarda el primero como representante.
    Adicionalmente devuelve _all (lista completa) y _multi (todos los matches por slug).
    """
    now = int(time.time())
    if _carriers_cache['data'] and (now - _carriers_cache['ts']) < CARRIER_CACHE_TTL:
        return _carriers_cache['data']
    p = get_processor()
    p.odoo.authenticate()
    try:
        rows = p.odoo.execute_kw('delivery.carrier', 'search_read',
            [[]], {'fields': ['id', 'name'], 'limit': 200})
    except Exception as e:
        _logger.warning(f'No pude listar carriers: {e}')
        rows = []
    out = {k: None for k in CARRIER_PATTERNS}
    multi = {k: [] for k in CARRIER_PATTERNS}
    all_list = []
    for r in rows:
        nm = (r.get('name') or '').lower()
        all_list.append({'id': r['id'], 'name': r['name']})
        for slug in CARRIER_PATTERNS_ORDER:
            pats = CARRIER_PATTERNS[slug]
            if any(pat in nm for pat in pats):
                multi[slug].append(r['id'])
                if out[slug] is None:
                    out[slug] = r['id']
                    _logger.info(f"Carrier {slug}: id={r['id']} name='{r['name']}'")
                break  # ya matcheó este courier, no probar otros
    out['_all'] = all_list
    out['_multi'] = multi
    _carriers_cache['data'] = out
    _carriers_cache['ts'] = now
    return out

@router.get('/carriers', dependencies=[Depends(verify_session)])
def list_carriers():
    """Devuelve los carriers detectados y la lista completa de Odoo (debug)."""
    cmap = _detect_carriers()
    return {'carriers': {k: v for k, v in cmap.items() if not k.startswith('_')},
            'all': cmap.get('_all', []),
            'multi': cmap.get('_multi', {})}

@router.post('/admin/refresh-carriers', dependencies=[Depends(verify_session)])
def refresh_carriers():
    """Invalida el cache y vuelve a detectar."""
    _carriers_cache['data'] = None
    _carriers_cache['ts'] = 0
    return list_carriers()

# ────────────────────────────────────────────────────
#  GET /api/stats?desde=YYYY-MM-DD&hasta=YYYY-MM-DD
#  Métricas agregadas para cabecera (envíos por courier en el rango)
# ────────────────────────────────────────────────────
@router.get('/stats', dependencies=[Depends(verify_session)])
def stats(desde: Optional[str] = None, hasta: Optional[str] = None):
    p = get_processor()
    odoo = p.odoo
    odoo.authenticate()

    # Default: mes actual
    if not desde:
        today = datetime.now()
        desde = today.replace(day=1).strftime('%Y-%m-%d')
    if not hasta:
        hasta = datetime.now().strftime('%Y-%m-%d')

    cmap = _detect_carriers()
    multi = cmap.get('_multi', {})
    inv = {}
    for slug, ids in multi.items():
        for cid in ids:
            inv[cid] = slug

    domain = [
        ('carrier_tracking_ref', '!=', False),
        ('picking_type_code', '=', 'outgoing'),
        ('write_date', '>=', desde + ' 00:00:00'),
        ('write_date', '<=', hasta + ' 23:59:59'),
    ]
    ids = odoo.execute_kw('stock.picking', 'search', [domain], {'limit': 2000})
    pks = odoo.execute_kw('stock.picking', 'read', [ids],
        {'fields': ['carrier_tracking_ref', 'carrier_id']}) if ids else []

    counts = {'pymex': 0, 'tavo': 0, 'dual': 0, 'unknown': 0, 'total': 0}
    for pk in pks:
        ref = (pk.get('carrier_tracking_ref') or '').upper()
        if ref.startswith('TV'):
            slug = 'tavo'
        elif ref.startswith('DG'):
            slug = 'dual'
        elif ref.startswith('PY'):
            slug = 'pymex'
        else:
            cid = (pk.get('carrier_id') or [None])[0] if pk.get('carrier_id') else None
            slug = inv.get(cid) or 'unknown'
        counts[slug] += 1
        counts['total'] += 1
    return {'desde': desde, 'hasta': hasta, 'counts': counts}

# ────────────────────────────────────────────────────
#  GET /api/picking/{id}/etiqueta-cualquiera
#  Devuelve el último PDF adjunto del picking (cualquier nombre tipo Etiqueta_*)
# ────────────────────────────────────────────────────
@router.get('/picking/{picking_id}/etiqueta-cualquiera')
def picking_etiqueta_cualquiera(picking_id: int, t: str = '', x_panel_token: Optional[str] = Header(None)):
    token = x_panel_token or t
    if not _verify_token(token):
        raise HTTPException(401, 'Sesión inválida')
    p = get_processor()
    p.odoo.authenticate()
    atts = p.odoo.execute_kw('ir.attachment', 'search_read',
        [[('res_model', '=', 'stock.picking'),
          ('res_id', '=', picking_id),
          ('name', 'like', 'Etiqueta_%')]],
        {'fields': ['datas', 'name'], 'limit': 1, 'order': 'create_date desc'})
    if not atts:
        raise HTTPException(404, 'No hay etiqueta para este picking')
    pdf_bytes = base64.b64decode(atts[0]['datas'])
    return Response(content=pdf_bytes, media_type='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{atts[0]["name"]}"'})

# ─── Auth header ───
def verify_token(x_api_token: str = Header(None)):
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail='Invalid API token')

# ─── SQLite local: estado de líneas preparadas + envíos manuales ───
# /app/data está montado como volumen en docker-compose, así sobrevive a
# rebuilds del contenedor. Si por alguna razón el directorio no existe (uso
# fuera de Docker, p.ej. tests), caemos a /app/panel.sqlite legacy.
DB_DIR = '/app/data' if os.path.isdir('/app/data') else '/app'
DB_PATH = os.path.join(DB_DIR, 'panel.sqlite')

# Migración perezosa: si todavía existe la DB vieja en /app/panel.sqlite y
# /app/data está vacío, la movemos. Una sola vez, al arrancar.
_LEGACY_DB = '/app/panel.sqlite'
if DB_DIR == '/app/data' and os.path.isfile(_LEGACY_DB) and not os.path.isfile(DB_PATH):
    try:
        os.rename(_LEGACY_DB, DB_PATH)
        _logger.info('Migrada panel.sqlite legacy a %s', DB_PATH)
    except OSError as e:
        _logger.warning('No se pudo migrar panel.sqlite: %s', e)

def db_init():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS line_prepared (
            picking_id INTEGER NOT NULL,
            move_id    INTEGER NOT NULL,
            checked_at TEXT NOT NULL,
            PRIMARY KEY (picking_id, move_id)
        );
        CREATE TABLE IF NOT EXISTS envio_manual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking TEXT UNIQUE,
            tipo TEXT,
            destinatario TEXT,
            direccion TEXT,
            cp TEXT,
            telefono TEXT,
            peso INTEGER,
            observaciones TEXT,
            notas_internas TEXT,
            pdf_b64 TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS entrega_mano (
            picking_id INTEGER PRIMARY KEY,
            entregado_a TEXT,
            notas TEXT,
            entregado_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

db_init()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ────────────────────────────────────────────────────
#  GET /api/pendientes
# ────────────────────────────────────────────────────
@router.get('/pendientes', dependencies=[Depends(verify_session)])
def list_pendientes(limit: int = 50, courier: str = 'pymex', solo_sin_agendar: bool = False):
    """
    Pickings sin guía (carrier_tracking_ref vacío), state=done.
    courier: pymex|tavo|dual|all (filtra por carrier_id).
    solo_sin_agendar: si True, excluye pickings con scheduled_date entre hoy y hoy+7 días
                      (porque ya están en la Agenda Semanal).
    Para 'pymex' aplica la exclusión 'no-pymexpress' en partner.category.
    """
    p = get_processor()
    odoo = p.odoo
    odoo.authenticate()

    cmap = _detect_carriers()
    multi = cmap.get('_multi', {})

    domain = [
        ('state', 'in', ['waiting', 'assigned', 'done']),
        ('carrier_tracking_ref', '=', False),
        ('picking_type_code', '=', 'outgoing'),
        '|', ('partner_id.country_id.code', '=', 'CR'),
             ('partner_id.country_id', '=', False),
    ]

    # Nota: el filtrado solo_sin_agendar se aplica en Python después de leer
    # los registros (ver más abajo), porque requiere comparar scheduled_date
    # con create_date — no se puede expresar limpiamente en el dominio Odoo.

    # Para Pymex aplicamos exclusión por tag
    if courier in ('pymex', 'all'):
        tag = odoo.execute_kw('res.partner.category', 'search',
                              [[('name', '=', 'no-pymexpress')]])
        tag_id = tag[0] if tag else None
        if tag_id and courier == 'pymex':
            domain.append(('partner_id.category_id', '!=', tag_id))

    ids = odoo.execute_kw('stock.picking', 'search', [domain],
                          {'limit': limit, 'order': 'id desc'})
    if not ids:
        return {'count': 0, 'items': [], 'carriers': {k: v for k, v in cmap.items() if not k.startswith('_')}}

    pickings = odoo.execute_kw('stock.picking', 'read', [ids],
        {'fields': ['id', 'name', 'partner_id', 'origin', 'date_done',
                    'scheduled_date', 'create_date', 'state',
                    'sale_id', 'move_ids', 'carrier_id']})

    # Si solo_sin_agendar: excluir pickings que el panel ha agendado explícitamente.
    # Convención: cuando Odoo crea un picking, scheduled_date == create_date (al segundo).
    # Cuando el panel llama /schedule, pone hora distinta — entonces difieren.
    if solo_sin_agendar:
        def _is_agendado(pk):
            sd = (pk.get('scheduled_date') or '')[:16]  # YYYY-MM-DD HH:MM
            cd = (pk.get('create_date') or '')[:16]
            if not sd or not cd: return False
            return sd != cd
        pickings = [pk for pk in pickings if not _is_agendado(pk)]

    # Total CRC, peso, líneas
    sale_ids = [p['sale_id'][0] for p in pickings if p.get('sale_id')]
    sales_map = {}
    if sale_ids:
        sales = odoo.execute_kw('sale.order', 'read', [sale_ids],
            {'fields': ['id', 'amount_total', 'currency_id']})
        sales_map = {s['id']: s for s in sales}

    # Mapa inverso carrier_id → slug (incluye multi)
    inv = {}
    for slug, ids in multi.items():
        for cid in ids:
            inv[cid] = slug

    # Fallback: detectar courier desde productos de envío en las líneas del sale order.
    # Útil cuando el carrier_id no se propaga del sale al picking.
    sale_courier = {}  # sale_id → slug
    sale_ids_for_lookup = list({pk['sale_id'][0] for pk in pickings if pk.get('sale_id')})
    if sale_ids_for_lookup:
        try:
            sales_full = odoo.execute_kw('sale.order', 'read', [sale_ids_for_lookup],
                {'fields': ['id', 'order_line']})
            all_line_ids = []
            for s in sales_full:
                all_line_ids.extend(s.get('order_line') or [])
            line_to_product = {}
            if all_line_ids:
                lines = odoo.execute_kw('sale.order.line', 'read', [all_line_ids],
                    {'fields': ['id', 'order_id', 'product_id']})
                product_ids_set = list({l['product_id'][0] for l in lines if l.get('product_id')})
                products_name_map = {}
                if product_ids_set:
                    prows = odoo.execute_kw('product.product', 'read', [product_ids_set],
                        {'fields': ['id', 'name']})
                    products_name_map = {p['id']: (p.get('name') or '') for p in prows}
                # Para cada sale, escoger el primer slug que matchee un producto de la línea
                sale_lines = {}
                for ln in lines:
                    if not ln.get('order_id') or not ln.get('product_id'):
                        continue
                    sid = ln['order_id'][0]
                    sale_lines.setdefault(sid, []).append(ln['product_id'][0])
                for sid, pids in sale_lines.items():
                    found = None
                    for pid in pids:
                        nm = products_name_map.get(pid, '').lower()
                        for slug in CARRIER_PATTERNS_ORDER:
                            if any(pat in nm for pat in CARRIER_PATTERNS[slug]):
                                found = slug
                                break
                        if found:
                            break
                    if found:
                        sale_courier[sid] = found
        except Exception as e:
            _logger.warning(f'No pude inferir courier desde sale lines: {e}')

    # Lectura masiva de partners para saber si tienen ZIP/dirección (estado real)
    partner_ids = list({pk['partner_id'][0] for pk in pickings if pk.get('partner_id')})
    partners_map = {}
    if partner_ids:
        prows = odoo.execute_kw('res.partner', 'read', [partner_ids],
            {'fields': ['id', 'name', 'street', 'zip', 'city', 'phone']})
        partners_map = {pr['id']: pr for pr in prows}

    # Estado de preparación (cuántas líneas marcadas)
    conn = db()
    items = []
    for pk in pickings:
        sale = sales_map.get(pk['sale_id'][0]) if pk.get('sale_id') else {}
        moves = pk.get('move_ids') or []
        prep_count = conn.execute(
            "SELECT COUNT(*) FROM line_prepared WHERE picking_id=?",
            (pk['id'],)
        ).fetchone()[0]
        cid = (pk.get('carrier_id') or [None])[0] if pk.get('carrier_id') else None
        # Slug: primero por carrier_id en picking; si no, inferido desde sale lines
        slug = inv.get(cid) if cid else None
        if not slug and pk.get('sale_id'):
            slug = sale_courier.get(pk['sale_id'][0])
        partner = partners_map.get(pk['partner_id'][0], {}) if pk.get('partner_id') else {}
        # Estado:
        #  - Pymex requiere CP + dirección (Correos los necesita en la guía)
        #  - Tavo/Dual no usan dirección postal (cliente recoge en sucursal); solo nombre y tel
        #  - 'preparando' si hay líneas marcadas pero no todas; 'listo' en otro caso
        is_pymex = (slug == 'pymex' or slug is None)
        if is_pymex and (not partner.get('zip') or not partner.get('street')):
            estado = 'incompleto'
            warn = 'Falta CP' if not partner.get('zip') else 'Falta dirección'
        elif slug in ('tavo', 'dual') and not partner.get('name'):
            estado = 'incompleto'
            warn = 'Falta nombre'
        elif prep_count > 0 and prep_count < len(moves):
            estado = 'preparando'
            warn = ''
        else:
            estado = 'listo'
            warn = ''
        items.append({
            'picking_id': pk['id'],
            'picking_name': pk['name'],
            'sale_name': pk['origin'] or pk['sale_id'][1] if pk.get('sale_id') else pk['name'],
            'partner_id': pk['partner_id'][0] if pk.get('partner_id') else None,
            'partner_name': pk['partner_id'][1] if pk.get('partner_id') else '',
            'date_done': pk.get('date_done') or pk.get('scheduled_date') or '',
            'state': pk.get('state'),
            'state_label': _state_label(pk.get('state')),
            'amount_total': sale.get('amount_total', 0),
            'currency': (sale.get('currency_id') or [None, 'CRC'])[1],
            'lines_count': len(moves),
            'lines_prepared': prep_count,
            'carrier_id': cid,
            'carrier_slug': slug,
            'carrier_name': pk['carrier_id'][1] if pk.get('carrier_id') else (
                f"Inferido: {COURIER_HUMAN.get(slug,'?')}" if slug else ''),
            'estado': estado,
            'warn': warn,
            'partner_zip': partner.get('zip') or '',
            'partner_city': partner.get('city') or '',
            'partner_phone': partner.get('phone') or '',
        })
    conn.close()

    # Filtrado en Python por courier (ya con slug inferido desde sale lines si toca)
    if courier in ('pymex', 'tavo', 'dual'):
        items = [it for it in items if it.get('carrier_slug') == courier]
    elif courier == 'unassigned':
        items = [it for it in items if not it.get('carrier_slug')]

    return {'count': len(items), 'items': items, 'carriers': {k: v for k, v in cmap.items() if not k.startswith('_')}}


# ────────────────────────────────────────────────────
#  GET /api/picking/{id}
# ────────────────────────────────────────────────────
@router.get('/picking/{picking_id}', dependencies=[Depends(verify_session)])
def get_picking_detail(picking_id: int):
    p = get_processor()
    odoo = p.odoo
    odoo.authenticate()

    pks = odoo.execute_kw('stock.picking', 'read', [[picking_id]],
        {'fields': ['id', 'name', 'partner_id', 'origin', 'sale_id',
                    'date_done', 'state', 'carrier_tracking_ref']})
    if not pks:
        raise HTTPException(status_code=404, detail='Picking no encontrado')
    pk = pks[0]

    partner_id = pk['partner_id'][0] if pk.get('partner_id') else None
    partner = odoo.read_partner(partner_id) if partner_id else {}

    # Líneas
    move_ids = odoo.execute_kw('stock.move', 'search',
                                [[('picking_id', '=', picking_id)]])
    moves = odoo.execute_kw('stock.move', 'read', [move_ids],
        {'fields': ['id', 'product_id', 'product_uom_qty', 'quantity',
                    'price_unit']}) if move_ids else []

    product_ids = list({m['product_id'][0] for m in moves if m.get('product_id')})
    products_map = {}
    if product_ids:
        prods = odoo.execute_kw('product.product', 'read', [product_ids],
            {'fields': ['id', 'name', 'default_code', 'description_sale',
                        'weight', 'list_price']})
        products_map = {p['id']: p for p in prods}

    # Estado preparado
    conn = db()
    prepared = {row['move_id'] for row in conn.execute(
        "SELECT move_id FROM line_prepared WHERE picking_id=?", (picking_id,)
    )}
    conn.close()

    lines = []
    for m in moves:
        pid = m['product_id'][0] if m.get('product_id') else None
        prod = products_map.get(pid, {})
        lines.append({
            'move_id': m['id'],
            'product_id': pid,
            'product_code': prod.get('default_code') or '',
            'product_name': prod.get('name') or '',
            'description': prod.get('description_sale') or '',
            'qty': m.get('product_uom_qty') or 0,
            'qty_done': m.get('quantity') or 0,
            'unit_price': m.get('price_unit') or 0,
            'weight_kg': prod.get('weight') or 0,
            'image_url': f'/api/producto-imagen/{pid}' if pid else None,
            'prepared': m['id'] in prepared,
        })

    # Peso total (del calculador real)
    peso_g = p._calc_peso(picking_id)

    return {
        'picking': {
            'id': pk['id'],
            'name': pk['name'],
            'origin': pk.get('origin'),
            'date_done': pk.get('date_done'),
            'state': pk.get('state'),
            'state_label': _state_label(pk.get('state')),
            'tracking_ref': pk.get('carrier_tracking_ref'),
        },
        'partner': {
            'id': partner.get('id'),
            'name': partner.get('name'),
            'street': partner.get('street'),
            'street2': partner.get('street2'),
            'city': partner.get('city'),
            'zip': partner.get('zip'),
            'phone': partner.get('phone'),
            'email': partner.get('email'),
            'state_id': partner.get('state_id'),
            # Nombres del partner para que el modal los muestre como texto,
            # no como [id, name] (que era el bug "Distrito 10" → IDs en
            # vez de nombres). Vacío si el partner no tiene el campo.
            'cr_address': {
                'provincia_name': _m2o_name(partner.get('state_id')).replace(' (CR)', '').strip(),
                'canton_id': partner.get('x_studio_canton_cr')[0]
                    if partner.get('x_studio_canton_cr') else None,
                'canton_name': _m2o_name(partner.get('x_studio_canton_cr')),
                'distrito_id': partner.get('x_studio_distrito_cr')[0]
                    if partner.get('x_studio_distrito_cr') else None,
                'distrito_name': _m2o_name(partner.get('x_studio_distrito_cr')),
                'senas': partner.get('x_studio_senas') or partner.get('street') or '',
            },
            # Códigos CR derivados del ZIP (PCCDD). El modal los usa para
            # precargar los selects cuando el partner no tiene los Studio
            # fields rellenos.
            'cr_codes': _cr_codes_from_zip(partner.get('zip')),
        },
        'lines': lines,
        'peso_total_g': peso_g,
        'remitente': {
            'name': settings.sender_name,
            'address': settings.sender_address,
            'zip': settings.sender_zip,
            'phone': settings.sender_phone,
        },
    }


# ────────────────────────────────────────────────────
#  POST /api/picking/{id}/preparar
# ────────────────────────────────────────────────────
class PrepararPayload(BaseModel):
    move_id: int
    prepared: bool

@router.post('/picking/{picking_id}/preparar', dependencies=[Depends(verify_session)])
def toggle_preparar(picking_id: int, payload: PrepararPayload):
    _logger.info(
        'toggle_preparar: picking_id=%s move_id=%s prepared=%s',
        picking_id, payload.move_id, payload.prepared,
    )
    conn = db()
    if payload.prepared:
        conn.execute(
            "INSERT OR REPLACE INTO line_prepared (picking_id, move_id, checked_at) VALUES (?, ?, ?)",
            (picking_id, payload.move_id, datetime.now().isoformat())
        )
    else:
        conn.execute(
            "DELETE FROM line_prepared WHERE picking_id=? AND move_id=?",
            (picking_id, payload.move_id)
        )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM line_prepared WHERE picking_id=?", (picking_id,)
    ).fetchone()[0]
    conn.close()
    return {'ok': True, 'prepared_count': n}


# ────────────────────────────────────────────────────
#  POST /api/picking/{id}/generar
# ────────────────────────────────────────────────────
class GenerarPayload(BaseModel):
    dest_nombre: str
    dest_direccion: str
    dest_telefono: str = ''
    dest_zip: str
    peso_g: int = Field(..., ge=1)
    observaciones: str = 'Herramientas'

@router.post('/picking/{picking_id}/generar', dependencies=[Depends(verify_session)])
def generar_guia_picking(picking_id: int, payload: GenerarPayload):
    p = get_processor()
    p.odoo.authenticate()

    # Lock por picking_id: evita carrera entre panel y worker automático.
    with _in_flight_lock:
        if picking_id in _in_flight:
            raise HTTPException(409, 'Esta guía se está generando en este momento')
        _in_flight.add(picking_id)

    try:
        # Verificar que sigue elegible (con el lock ya tomado)
        pks = p.odoo.execute_kw('stock.picking', 'read', [[picking_id]],
            {'fields': ['name', 'state', 'carrier_tracking_ref', 'partner_id']})
        if not pks:
            raise HTTPException(404, 'Picking no encontrado')
        if pks[0].get('carrier_tracking_ref'):
            raise HTTPException(409, f"Ya tiene guía: {pks[0]['carrier_tracking_ref']}")

        # Releer el partner para enriquecer la dirección con prov/cantón/distrito.
        # Sin esto, la etiqueta de Correos solo imprime las señas y se pierde
        # la info geográfica que el cartero necesita.
        partner_id = pks[0]['partner_id'][0] if pks[0].get('partner_id') else None
        partner_data = p.odoo.read_partner(partner_id) if partner_id else {}
        dest_direccion_full = build_dest_direccion(partner_data, senas_override=payload.dest_direccion)

        # 1) Generar guía
        envio_id = p.correos.generar_guia()
        _logger.info(f'API: guía {envio_id} para picking {picking_id}')

        # 2) Registrar envío
        envio_data = {
            'fecha_envio': datetime.now(),
            'monto_flete': 0,
            'dest_nombre': payload.dest_nombre,
            'dest_direccion': dest_direccion_full,
            'dest_telefono': payload.dest_telefono.replace(' ', '').replace('-', ''),
            'dest_zip': payload.dest_zip,
            'send_nombre': settings.sender_name,
            'send_direccion': settings.sender_address,
            'send_zip': settings.sender_zip,
            'send_telefono': settings.sender_phone.replace(' ', '').replace('-', ''),
            'observaciones': payload.observaciones,
            'peso': int(payload.peso_g),
        }
        cod, msg, pdf_b64 = p.correos.registrar_envio(envio_id, envio_data)

        # 3) Adjuntar PDF al picking + tracking
        if pdf_b64:
            att_id = p.odoo.attach_pdf(
                picking_id,
                f'Etiqueta_CorreosCR_{envio_id}.pdf',
                pdf_b64 if isinstance(pdf_b64, str) else base64.b64encode(pdf_b64).decode()
            )
            p.odoo.post_message(
                picking_id,
                f'✅ <b>Guía Correos CR generada desde panel</b><br/>'
                f'Número: <b>{envio_id}</b><br/>Peso: {payload.peso_g} g',
                attachment_ids=[att_id]
            )
        p.odoo.set_tracking(picking_id, envio_id)

        # Releer el picking para devolver el estado post-write — así el frontend
        # puede actualizar la fila localmente sin re-fetch de toda la agenda.
        updated = p.odoo.execute_kw('stock.picking', 'read', [[picking_id]],
            {'fields': ['state', 'carrier_tracking_ref']})
        upd = updated[0] if updated else {}

        return {
            'ok': True,
            'tracking': envio_id,
            'pdf_b64': pdf_b64,
            'message': msg,
            'picking': {
                'id': picking_id,
                'state': upd.get('state'),
                'state_label': _state_label(upd.get('state')),
                'tracking': upd.get('carrier_tracking_ref') or envio_id,
                'has_guide': True,
            },
        }
    finally:
        with _in_flight_lock:
            _in_flight.discard(picking_id)


# ────────────────────────────────────────────────────
#  POST /api/picking/{id}/registrar-manual
#  Adjunta una etiqueta generada en cliente (Tavo/Dual) al picking
#  y graba el carrier_tracking_ref. PDF llega en base64.
# ────────────────────────────────────────────────────
class RegistrarManualPayload(BaseModel):
    courier: str = Field(..., description='tavo|dual')
    tracking: str = ''  # opcional. Si vacío, generamos consecutivo TV0001/DG0001
    pdf_b64: str = Field(..., description='PDF de la etiqueta en base64')
    dest_nombre: str = ''
    dest_direccion: str = ''
    dest_telefono: str = ''
    dest_zip: str = ''
    sucursal: str = ''
    identificacion: str = ''
    peso_g: int = 0
    observaciones: str = 'Herramientas'

def _next_consecutivo(prefix: str) -> str:
    """Devuelve el siguiente consecutivo PREFIX0001 buscando en Odoo el max actual."""
    p = get_processor()
    p.odoo.authenticate()
    rows = p.odoo.execute_kw('stock.picking', 'search_read',
        [[('carrier_tracking_ref', 'like', f'{prefix}%')]],
        {'fields': ['carrier_tracking_ref'], 'limit': 50, 'order': 'id desc'})
    n = 0
    for r in rows:
        ref = (r.get('carrier_tracking_ref') or '').strip()
        if ref.startswith(prefix):
            try:
                rest = ref[len(prefix):]
                digits = ''.join(c for c in rest if c.isdigit())
                if digits:
                    n = max(n, int(digits))
            except Exception:
                pass
    return f'{prefix}{n+1:04d}'

# ────────────────────────────────────────────────────
#  POST /api/partner/{id}/update
#  Actualiza campos del partner en Odoo (solo los que vengan)
# ────────────────────────────────────────────────────
class PartnerUpdatePayload(BaseModel):
    name: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = None
    phone: Optional[str] = None

@router.post('/partner/{partner_id}/update', dependencies=[Depends(verify_session)])
def update_partner(partner_id: int, payload: PartnerUpdatePayload):
    if not partner_id:
        raise HTTPException(400, 'partner_id requerido')
    p = get_processor()
    p.odoo.authenticate()
    vals = {}
    for f in ('name', 'street', 'city', 'zip', 'phone'):
        v = getattr(payload, f, None)
        if v is not None:
            vals[f] = v
    if not vals:
        return {'ok': True, 'updated': []}
    try:
        p.odoo.execute_kw('res.partner', 'write', [[partner_id], vals])
    except Exception as e:
        raise HTTPException(500, f'No se pudo actualizar partner: {e}')
    return {'ok': True, 'updated': list(vals.keys())}


@router.get('/next-tracking', dependencies=[Depends(verify_session)])
def next_tracking(courier: str):
    """Devuelve el siguiente consecutivo TV/DG sin reservarlo. courier: tavo|dual"""
    if courier == 'tavo':
        prefix = 'TV'
    elif courier == 'dual':
        prefix = 'DG'
    else:
        raise HTTPException(400, "courier debe ser 'tavo' o 'dual'")
    return {'tracking': _next_consecutivo(prefix), 'prefix': prefix}

@router.post('/picking/{picking_id}/registrar-manual', dependencies=[Depends(verify_session)])
def registrar_manual(picking_id: int, payload: RegistrarManualPayload):
    if payload.courier not in ('tavo', 'dual'):
        raise HTTPException(400, "courier debe ser 'tavo' o 'dual'")
    p = get_processor()
    p.odoo.authenticate()

    pks = p.odoo.execute_kw('stock.picking', 'read', [[picking_id]],
        {'fields': ['name', 'state', 'carrier_tracking_ref']})
    if not pks:
        raise HTTPException(404, 'Picking no encontrado')
    if pks[0].get('carrier_tracking_ref'):
        raise HTTPException(409, f"Ya tiene guía: {pks[0]['carrier_tracking_ref']}")

    # Tracking: si viene vacío, autogenerar consecutivo TV/DG
    tracking = (payload.tracking or '').strip()
    if not tracking:
        prefix = 'TV' if payload.courier == 'tavo' else 'DG'
        tracking = _next_consecutivo(prefix)

    courier_lbl = {'tavo': 'Tavo', 'dual': 'Dual Global'}[payload.courier]
    fname = f'Etiqueta_{courier_lbl.replace(" ","")}_{tracking}.pdf'

    # Saneamos pdf_b64 (puede venir con prefijo data:)
    pdf_clean = payload.pdf_b64
    if pdf_clean.startswith('data:'):
        pdf_clean = pdf_clean.split(',', 1)[-1]

    att_id = p.odoo.attach_pdf(picking_id, fname, pdf_clean)

    extra = ''
    if payload.sucursal:
        extra += f'<br/>Sucursal: <b>{payload.sucursal}</b>'
    if payload.identificacion:
        extra += f'<br/>Identificación: {payload.identificacion}'

    p.odoo.post_message(
        picking_id,
        f'✅ <b>Etiqueta {courier_lbl} registrada desde panel</b><br/>'
        f'Referencia: <b>{tracking}</b>'
        f'{extra}',
        attachment_ids=[att_id]
    )
    p.odoo.set_tracking(picking_id, tracking)
    return {'ok': True, 'tracking': tracking, 'courier': payload.courier}


# ────────────────────────────────────────────────────
#  POST /api/manual/generar
# ────────────────────────────────────────────────────
class ManualPayload(BaseModel):
    tipo: str = 'Otro'
    dest_nombre: str
    dest_direccion: str
    dest_telefono: str = ''
    dest_zip: str
    peso_g: int = Field(..., ge=1)
    observaciones: str = 'Herramientas'
    notas_internas: str = ''

@router.post('/manual/generar', dependencies=[Depends(verify_session)])
def generar_guia_manual(payload: ManualPayload):
    p = get_processor()
    envio_id = p.correos.generar_guia()
    _logger.info(f'API manual: guía {envio_id}')

    envio_data = {
        'fecha_envio': datetime.now(),
        'monto_flete': 0,
        'dest_nombre': payload.dest_nombre,
        'dest_direccion': payload.dest_direccion,
        'dest_telefono': payload.dest_telefono.replace(' ', '').replace('-', ''),
        'dest_zip': payload.dest_zip,
        'send_nombre': settings.sender_name,
        'send_direccion': settings.sender_address,
        'send_zip': settings.sender_zip,
        'send_telefono': settings.sender_phone.replace(' ', '').replace('-', ''),
        'observaciones': payload.observaciones,
        'peso': int(payload.peso_g),
    }
    cod, msg, pdf_b64 = p.correos.registrar_envio(envio_id, envio_data)

    # Guardar en SQLite
    conn = db()
    conn.execute(
        """INSERT INTO envio_manual
           (tracking, tipo, destinatario, direccion, cp, telefono, peso,
            observaciones, notas_internas, pdf_b64, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (envio_id, payload.tipo, payload.dest_nombre, payload.dest_direccion,
         payload.dest_zip, payload.dest_telefono, payload.peso_g,
         payload.observaciones, payload.notas_internas, pdf_b64, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    return {'ok': True, 'tracking': envio_id, 'pdf_b64': pdf_b64, 'message': msg}


# ────────────────────────────────────────────────────
#  GET /api/historico
# ────────────────────────────────────────────────────
@router.get('/historico', dependencies=[Depends(verify_session)])
def historico(
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    courier: str = 'all',
    q: str = '',
    limit: int = 200,
):
    """
    Lista guías generadas (cualquier courier) con filtros.
    courier: pymex|tavo|dual|all
    q: texto libre — busca en tracking, picking name, partner name.
    """
    p = get_processor()
    odoo = p.odoo
    odoo.authenticate()

    cmap = _detect_carriers()
    multi = cmap.get('_multi', {})
    inv = {}
    for slug, ids in multi.items():
        for cid in ids:
            inv[cid] = slug

    domain = [('carrier_tracking_ref', '!=', False),
              ('picking_type_code', '=', 'outgoing')]
    if courier != 'all' and multi.get(courier):
        domain.append(('carrier_id', 'in', multi[courier]))
    if desde:
        domain.append(('date_done', '>=', desde + ' 00:00:00'))
    if hasta:
        domain.append(('date_done', '<=', hasta + ' 23:59:59'))
    if q:
        domain += ['|', '|',
                   ('carrier_tracking_ref', 'ilike', q),
                   ('name', 'ilike', q),
                   ('partner_id.name', 'ilike', q)]

    ids = odoo.execute_kw('stock.picking', 'search', [domain],
                          {'limit': limit, 'order': 'date_done desc'})
    pks = odoo.execute_kw('stock.picking', 'read', [ids],
        {'fields': ['name', 'carrier_tracking_ref', 'partner_id', 'date_done',
                    'scheduled_date', 'write_date',
                    'origin', 'carrier_id']}) if ids else []

    # Manuales del SQLite (solo Pymex actualmente). Filtramos por q si aplica.
    manuales = []
    if courier in ('pymex', 'all'):
        conn = db()
        params = []
        sql = "SELECT tracking, tipo, destinatario, created_at FROM envio_manual"
        wh = []
        if q:
            wh.append("(tracking LIKE ? OR destinatario LIKE ?)")
            params += [f'%{q}%', f'%{q}%']
        if desde:
            wh.append("created_at >= ?"); params.append(desde)
        if hasta:
            wh.append("created_at <= ?"); params.append(hasta + ' 23:59:59')
        if wh:
            sql += " WHERE " + " AND ".join(wh)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        manuales = [{'tracking': r['tracking'], 'tipo': r['tipo'],
                     'destinatario': r['destinatario'], 'created_at': r['created_at'],
                     'origen': 'manual', 'courier': 'pymex'} for r in rows]
        conn.close()

    def _hist_courier(tracking_ref, carrier_id):
        """Detecta el courier desde tracking_ref (TV/DG/PY) o carrier_id como fallback."""
        if tracking_ref:
            t = tracking_ref.upper()
            if t.startswith('TV'): return 'tavo'
            if t.startswith('DG'): return 'dual'
            if t.startswith('PY'): return 'pymex'
        if carrier_id:
            return inv.get(carrier_id[0])
        return None

    pickings = [{
        'tracking': pk['carrier_tracking_ref'],
        'picking': pk['name'],
        'picking_id': pk.get('id'),
        'origen': 'pedido',
        'sale': pk.get('origin'),
        'destinatario': pk['partner_id'][1] if pk.get('partner_id') else '',
        'date': pk.get('date_done') or pk.get('scheduled_date') or pk.get('write_date') or '',
        'courier': _hist_courier(pk.get('carrier_tracking_ref'), pk.get('carrier_id')),
        'carrier_name': pk['carrier_id'][1] if pk.get('carrier_id') else '',
    } for pk in pks]

    return {'pedidos': pickings, 'manuales': manuales,
            'count': len(pickings) + len(manuales)}


# ────────────────────────────────────────────────────
#  GET /api/calendario?mes=YYYY-MM
# ────────────────────────────────────────────────────
# ────────────────────────────────────────────────────
#  GET /api/agenda?fecha=YYYY-MM-DD
#  Pickings programados ese día (cualquier estado, con o sin guía)
#  Agrupados por courier para vista rápida.
# ────────────────────────────────────────────────────
# ────────────────────────────────────────────────────
#  POST /api/picking/{id}/schedule
#  Cambia scheduled_date del picking (mover de día)
# ────────────────────────────────────────────────────
class SchedulePayload(BaseModel):
    fecha: str  # YYYY-MM-DD
    hora: Optional[str] = '09:00'  # HH:MM, default 9 am

@router.post('/picking/{picking_id}/schedule', dependencies=[Depends(verify_session)])
def reschedule_picking(picking_id: int, payload: SchedulePayload):
    # Validar formato fecha
    try:
        datetime.strptime(payload.fecha, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(400, 'fecha debe ser YYYY-MM-DD')
    hora = payload.hora or '09:00'
    if len(hora) == 5 and hora[2] == ':':
        full = f'{payload.fecha} {hora}:00'
    else:
        full = f'{payload.fecha} 09:00:00'
    p = get_processor()
    p.odoo.authenticate()
    try:
        p.odoo.execute_kw('stock.picking', 'write',
                          [[picking_id], {'scheduled_date': full}])
        # Mensaje al chatter
        p.odoo.post_message(picking_id,
            f'📅 <b>Reprogramado desde panel</b><br/>Nueva fecha: {payload.fecha} {hora}')
    except Exception as e:
        raise HTTPException(500, f'No pude reprogramar: {e}')
    return {'ok': True, 'scheduled_date': full}


@router.get('/agenda', dependencies=[Depends(verify_session)])
def agenda(fecha: str):
    """Pickings cuya scheduled_date sea el día indicado, agrupados por courier."""
    p = get_processor()
    odoo = p.odoo
    odoo.authenticate()

    cmap = _detect_carriers()
    multi = cmap.get('_multi', {})
    inv = {}
    for slug, ids in multi.items():
        for cid in ids:
            inv[cid] = slug

    desde = fecha + ' 00:00:00'
    hasta = fecha + ' 23:59:59'
    domain = [
        ('picking_type_code', '=', 'outgoing'),
        ('state', 'in', ['waiting', 'assigned', 'done']),
        ('scheduled_date', '>=', desde),
        ('scheduled_date', '<=', hasta),
    ]
    ids = odoo.execute_kw('stock.picking', 'search', [domain],
                          {'limit': 200, 'order': 'scheduled_date asc'})
    pks = odoo.execute_kw('stock.picking', 'read', [ids],
        {'fields': ['id', 'name', 'partner_id', 'origin', 'sale_id',
                    'scheduled_date', 'state', 'carrier_id',
                    'carrier_tracking_ref']}) if ids else []

    # Inferir courier desde sale lines para los que no tienen carrier_id
    sale_courier = {}
    sale_ids_for_lookup = list({pk['sale_id'][0] for pk in pks if pk.get('sale_id') and not pk.get('carrier_id')})
    if sale_ids_for_lookup:
        try:
            sales_full = odoo.execute_kw('sale.order', 'read', [sale_ids_for_lookup],
                {'fields': ['id', 'order_line']})
            all_line_ids = []
            for s in sales_full: all_line_ids.extend(s.get('order_line') or [])
            if all_line_ids:
                lines = odoo.execute_kw('sale.order.line', 'read', [all_line_ids],
                    {'fields': ['order_id', 'product_id']})
                product_ids_set = list({l['product_id'][0] for l in lines if l.get('product_id')})
                pname_map = {}
                if product_ids_set:
                    prows = odoo.execute_kw('product.product', 'read', [product_ids_set],
                        {'fields': ['id', 'name']})
                    pname_map = {pr['id']: (pr.get('name') or '') for pr in prows}
                sale_lines = {}
                for ln in lines:
                    if not ln.get('order_id') or not ln.get('product_id'): continue
                    sale_lines.setdefault(ln['order_id'][0], []).append(ln['product_id'][0])
                for sid, pids in sale_lines.items():
                    found = None
                    for pid in pids:
                        nm = pname_map.get(pid, '').lower()
                        for slug in CARRIER_PATTERNS_ORDER:
                            if any(pat in nm for pat in CARRIER_PATTERNS[slug]):
                                found = slug; break
                        if found: break
                    if found: sale_courier[sid] = found
        except Exception as e:
            _logger.warning(f'agenda: no inferred courier: {e}')

    # Construir items y conteo por courier
    items = {'pymex': [], 'tavo': [], 'dual': [], 'unassigned': []}
    counts = {'pymex': 0, 'tavo': 0, 'dual': 0, 'unassigned': 0}
    for pk in pks:
        cid = (pk.get('carrier_id') or [None])[0] if pk.get('carrier_id') else None
        slug = inv.get(cid) if cid else None
        if not slug and pk.get('sale_id'):
            slug = sale_courier.get(pk['sale_id'][0])
        bucket = slug if slug in ('pymex', 'tavo', 'dual') else 'unassigned'
        items[bucket].append({
            'picking_id': pk['id'],
            'picking': pk['name'],
            'sale': pk.get('origin') or '',
            'cliente': pk['partner_id'][1] if pk.get('partner_id') else '',
            'state': pk.get('state'),
            'state_label': _state_label(pk.get('state')),
            'tracking': pk.get('carrier_tracking_ref') or '',
            'scheduled': pk.get('scheduled_date'),
            'has_guide': bool(pk.get('carrier_tracking_ref')),
        })
        counts[bucket] += 1
    return {'fecha': fecha, 'counts': counts, 'items': items, 'total': len(pks)}


@router.get('/agenda-semana', dependencies=[Depends(verify_session)])
def agenda_semana(desde: str):
    """Pickings con scheduled_date dentro de los 7 días desde 'desde' (lunes recomendado).
    Devuelve agrupados por día y por courier dentro del día.
    """
    from datetime import datetime as _dt, timedelta as _td
    try:
        d0 = _dt.strptime(desde, '%Y-%m-%d').date()
    except Exception:
        raise HTTPException(400, 'desde formato YYYY-MM-DD')
    d_fin = d0 + _td(days=6)

    p = get_processor()
    odoo = p.odoo
    odoo.authenticate()

    cmap = _detect_carriers()
    multi = cmap.get('_multi', {})
    inv = {}
    for slug, ids in multi.items():
        for cid in ids:
            inv[cid] = slug

    desde_s = d0.strftime('%Y-%m-%d') + ' 00:00:00'
    hasta_s = d_fin.strftime('%Y-%m-%d') + ' 23:59:59'
    domain = [
        ('picking_type_code', '=', 'outgoing'),
        ('state', 'in', ['waiting', 'assigned', 'done']),
        ('scheduled_date', '>=', desde_s),
        ('scheduled_date', '<=', hasta_s),
    ]
    ids = odoo.execute_kw('stock.picking', 'search', [domain],
                          {'limit': 1000, 'order': 'scheduled_date asc'})
    pks = odoo.execute_kw('stock.picking', 'read', [ids],
        {'fields': ['id', 'name', 'partner_id', 'origin', 'sale_id',
                    'scheduled_date', 'state', 'carrier_id',
                    'carrier_tracking_ref']}) if ids else []

    # Inferir courier por sale lines (mismo patrón que /agenda)
    sale_courier = {}
    sale_ids_for_lookup = list({pk['sale_id'][0] for pk in pks if pk.get('sale_id') and not pk.get('carrier_id')})
    if sale_ids_for_lookup:
        try:
            sales_full = odoo.execute_kw('sale.order', 'read', [sale_ids_for_lookup],
                {'fields': ['id', 'order_line']})
            all_line_ids = []
            for s in sales_full: all_line_ids.extend(s.get('order_line') or [])
            if all_line_ids:
                lines = odoo.execute_kw('sale.order.line', 'read', [all_line_ids],
                    {'fields': ['order_id', 'product_id']})
                product_ids_set = list({l['product_id'][0] for l in lines if l.get('product_id')})
                pname_map = {}
                if product_ids_set:
                    prows = odoo.execute_kw('product.product', 'read', [product_ids_set],
                        {'fields': ['id', 'name']})
                    pname_map = {pr['id']: (pr.get('name') or '') for pr in prows}
                sale_lines = {}
                for ln in lines:
                    if not ln.get('order_id') or not ln.get('product_id'): continue
                    sale_lines.setdefault(ln['order_id'][0], []).append(ln['product_id'][0])
                for sid, pids in sale_lines.items():
                    found = None
                    for pid in pids:
                        nm = pname_map.get(pid, '').lower()
                        for slug in CARRIER_PATTERNS_ORDER:
                            if any(pat in nm for pat in CARRIER_PATTERNS[slug]):
                                found = slug; break
                        if found: break
                    if found: sale_courier[sid] = found
        except Exception as e:
            _logger.warning(f'agenda-semana: no inferred courier: {e}')

    # Estructura por día
    days = {}
    for i in range(7):
        d = d0 + _td(days=i)
        days[d.strftime('%Y-%m-%d')] = {
            'fecha': d.strftime('%Y-%m-%d'),
            'dow': d.weekday(),  # 0=lunes
            'items': {'pymex': [], 'tavo': [], 'dual': [], 'unassigned': []},
            'counts': {'pymex': 0, 'tavo': 0, 'dual': 0, 'unassigned': 0, 'done': 0},
        }

    total = 0
    for pk in pks:
        sched = pk.get('scheduled_date') or ''
        day_key = sched[:10] if sched else None
        if day_key not in days: continue
        cid = (pk.get('carrier_id') or [None])[0] if pk.get('carrier_id') else None
        slug = inv.get(cid) if cid else None
        if not slug and pk.get('sale_id'):
            slug = sale_courier.get(pk['sale_id'][0])
        bucket = slug if slug in ('pymex','tavo','dual') else 'unassigned'
        item = {
            'picking_id': pk['id'],
            'picking': pk['name'],
            'sale': pk.get('origin') or '',
            'cliente': pk['partner_id'][1] if pk.get('partner_id') else '',
            'state': pk.get('state'),
            'state_label': _state_label(pk.get('state')),
            'tracking': pk.get('carrier_tracking_ref') or '',
            'scheduled': pk.get('scheduled_date'),
            'has_guide': bool(pk.get('carrier_tracking_ref')),
        }
        days[day_key]['items'][bucket].append(item)
        days[day_key]['counts'][bucket] += 1
        if item['has_guide']: days[day_key]['counts']['done'] += 1
        total += 1

    return {
        'desde': d0.strftime('%Y-%m-%d'),
        'hasta': d_fin.strftime('%Y-%m-%d'),
        'days': days,
        'total': total,
    }


@router.get('/calendario', dependencies=[Depends(verify_session)])
def calendario(mes: str, courier: str = 'all'):
    """mes formato YYYY-MM. courier: pymex|tavo|dual|all"""
    try:
        year, month = map(int, mes.split('-'))
    except Exception:
        raise HTTPException(400, 'Formato esperado: YYYY-MM')

    desde = f'{year:04d}-{month:02d}-01'
    if month == 12:
        hasta = f'{year+1:04d}-01-01'
    else:
        hasta = f'{year:04d}-{month+1:02d}-01'

    p = get_processor()
    odoo = p.odoo
    odoo.authenticate()

    cmap = _detect_carriers()
    multi = cmap.get('_multi', {})
    inv = {}
    for slug, ids in multi.items():
        for cid in ids:
            inv[cid] = slug

    # Usamos write_date para que Tavo/Dual (sin date_done validado) también aparezcan
    domain = [
        ('carrier_tracking_ref', '!=', False),
        ('picking_type_code', '=', 'outgoing'),
        ('write_date', '>=', desde + ' 00:00:00'),
        ('write_date', '<', hasta + ' 00:00:00'),
    ]
    if courier != 'all' and multi.get(courier):
        domain.append(('carrier_id', 'in', multi[courier]))
    ids = odoo.execute_kw('stock.picking', 'search', [domain],
        {'limit': 500, 'order': 'date_done asc'})

    pks = odoo.execute_kw('stock.picking', 'read', [ids],
        {'fields': ['name', 'carrier_tracking_ref', 'partner_id', 'date_done',
                    'write_date', 'carrier_id']}) if ids else []

    # Agrupar por día
    days = {}
    for pk in pks:
        d = (pk.get('write_date') or pk.get('date_done') or '')[:10]
        if not d: continue
        cid = (pk.get('carrier_id') or [None])[0] if pk.get('carrier_id') else None
        slug = inv.get(cid, 'unknown')
        if courier != 'all' and slug != courier:
            continue
        days.setdefault(d, []).append({
            'picking_id': pk['id'],
            'picking': pk['name'],
            'tracking': pk.get('carrier_tracking_ref') or '',
            'cliente': pk['partner_id'][1] if pk.get('partner_id') else '',
            'courier': slug,
        })

    return {'mes': mes, 'courier': courier, 'days': days}


# ─── Entrega a mano: marcar picking como entregado presencialmente ───
class EntregaManoPayload(BaseModel):
    entregado_a: Optional[str] = ''
    notas: Optional[str] = ''


@router.post('/picking/{picking_id}/entrega-mano', dependencies=[Depends(verify_session)])
def entrega_mano(picking_id: int, payload: EntregaManoPayload):
    """Marca un picking como entregado a mano (sin guía courier).
    Guarda en SQLite local y opcionalmente cierra el picking en Odoo."""
    from datetime import datetime as _dt
    conn = db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO entrega_mano (picking_id, entregado_a, notas, entregado_at) VALUES (?, ?, ?, ?)",
            (picking_id, payload.entregado_a or '', payload.notas or '',
             _dt.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    finally:
        conn.close()

    # Marcar carrier_tracking_ref con prefijo MANO-{id} en Odoo para que aparezca
    # en histórico y deje de aparecer como pendiente
    try:
        p = get_processor()
        p.odoo.authenticate()
        ref = "MANO-" + str(picking_id)
        p.odoo.execute_kw('stock.picking', 'write',
            [[picking_id], {'carrier_tracking_ref': ref}])
        return {'ok': True, 'tracking': ref}
    except Exception as e:
        _logger.warning("entrega-mano: no pude actualizar Odoo " + str(picking_id) + ": " + str(e))
        return {'ok': True, 'tracking': "MANO-" + str(picking_id), 'warn': str(e)}


# ─── Auth verify estricto (usado por nginx auth_request) ───
@router.get('/auth/verify', dependencies=[Depends(verify_session)])
def auth_verify():
    return {'ok': True}


# ─── OCR Tavo: extraer datos de un pantallazo de pedido (Claude Vision) ───
@router.post('/ocr/tavo', dependencies=[Depends(verify_session)])
def ocr_tavo(payload: dict = Body(...)):
    """
    Recibe {image_b64, media_type} y devuelve {ok, data:{nombre,direccion,canton,cp,telefono}}.
    image_b64 puede traer prefijo data:image/...;base64, o no.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise HTTPException(503, 'ANTHROPIC_API_KEY no configurada en el .env del bridge')

    image_b64 = (payload or {}).get('image_b64', '')
    if not image_b64:
        raise HTTPException(400, 'falta image_b64')

    media_type = (payload or {}).get('media_type') or 'image/png'
    if image_b64.startswith('data:'):
        # data:image/png;base64,<bytes>  o  data:application/pdf;base64,<bytes>
        try:
            head, image_b64 = image_b64.split(',', 1)
            # extraer media_type del prefijo (image/png, image/jpeg, application/pdf, etc.)
            m = head.replace('data:', '').split(';')[0].strip()
            if m:
                media_type = m
        except ValueError:
            raise HTTPException(400, 'data URL malformado')

    # Claude Vision soporta image/* directamente y application/pdf como document
    is_pdf = media_type == 'application/pdf'
    if not is_pdf and not media_type.startswith('image/'):
        raise HTTPException(400, f'media_type no soportado: {media_type}. Use image/* o application/pdf.')

    prompt = (
        "Extraé del pantallazo de pedido los datos del destinatario que va a recibir el paquete en Costa Rica:\n"
        "- nombre: nombre completo (puede aparecer como 'cliente', 'destinatario', 'para', etc.)\n"
        "- direccion: dirección física (calle/avenida/casa/edificio). NO incluyas cantón ni provincia ahí.\n"
        "- canton: cantón o distrito (p. ej. 'San José centro', 'Cartago', 'Heredia')\n"
        "- cp: código postal de Costa Rica (5 dígitos, p. ej. '10101' o '30504'). Si no aparece dejalo vacío.\n"
        "- telefono: teléfono de contacto en CR (8 dígitos típicamente)\n\n"
        "Respondé EXCLUSIVAMENTE un objeto JSON con esas 5 claves y nada más. "
        "Si algún dato no aparece, ponelo como string vacío \"\". Sin markdown, sin explicaciones, solo el JSON."
    )

    # Para PDF se usa type=document (con media_type application/pdf);
    # para imágenes se usa type=image.
    if is_pdf:
        content_block = {
            'type': 'document',
            'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': image_b64},
        }
    else:
        content_block = {
            'type': 'image',
            'source': {'type': 'base64', 'media_type': media_type, 'data': image_b64},
        }

    try:
        r = _requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 512,
                'messages': [{
                    'role': 'user',
                    'content': [
                        content_block,
                        {'type': 'text', 'text': prompt},
                    ],
                }],
            },
            timeout=60,
        )
    except _requests.RequestException as e:
        raise HTTPException(502, f'No pude contactar Claude: {e}')

    if r.status_code != 200:
        # Propagamos el mensaje exacto de Anthropic (saldo bajo, modelo inválido, etc.)
        # en vez del 502 genérico, para que el frontend lo muestre claro.
        try:
            err = r.json().get('error', {}).get('message') or r.text[:300]
        except Exception:
            err = r.text[:300]
        # Si es problema de saldo, devolvemos 402 (Payment Required) que es más correcto
        status_code = 402 if ('credit balance' in err.lower() or 'billing' in err.lower()) else 502
        raise HTTPException(status_code, f'Claude API: {err}')

    body = r.json()
    text = (body.get('content') or [{}])[0].get('text', '{}')
    text = text.strip()
    # Limpiar cercos de markdown si los hubiera
    if text.startswith('```'):
        text = text.strip('`').strip()
        if text.startswith('json'):
            text = text[4:].strip()
        if '```' in text:
            text = text.split('```', 1)[0].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(502, f'Claude devolvió respuesta no-JSON: {text[:300]}')

    # Normalizar y devolver solo los campos esperados
    return {
        'ok': True,
        'data': {
            'nombre': str(data.get('nombre', '') or ''),
            'direccion': str(data.get('direccion', '') or ''),
            'canton': str(data.get('canton', '') or ''),
            'cp': str(data.get('cp', '') or ''),
            'telefono': str(data.get('telefono', '') or ''),
        },
        'usage': body.get('usage', {}),
    }


# ─── Servir imágenes de productos como PNG ───
@router.get('/producto-imagen/{product_id}')
def producto_imagen(product_id: int, t: str = '', x_panel_token: Optional[str] = Header(None)):
    """Devuelve la imagen del producto. Auth via header o ?t= query."""
    token = x_panel_token or t
    if not _verify_token(token):
        raise HTTPException(401, 'Token inválido')
    p = get_processor()
    p.odoo.authenticate()
    try:
        rows = p.odoo.execute_kw('product.product', 'read',
            [[product_id]], {'fields': ['image_512']})
        if not rows or not rows[0].get('image_512'):
            raise HTTPException(404, 'Producto sin imagen')
        import base64 as _b64
        raw = _b64.b64decode(rows[0]['image_512'])
        from fastapi.responses import Response
        return Response(content=raw, media_type='image/png',
                        headers={'Cache-Control': 'public, max-age=86400'})
    except HTTPException:
        raise
    except Exception as e:
        _logger.error("producto-imagen " + str(product_id) + ": " + str(e))
        raise HTTPException(500, str(e))
