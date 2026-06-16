# -*- coding: utf-8 -*-
"""
Cliente XML-RPC para Odoo Online.
Permite leer pickings, adjuntar PDFs, y escribir en campos de stock.picking.
"""

import logging
import xmlrpc.client
from typing import Optional

_logger = logging.getLogger(__name__)


class OdooError(Exception):
    pass


# Campos de res.partner que la etiqueta de Correos necesita. Compartido entre
# read_partner (un partner) y list_addresses (varias direcciones de un cliente).
_PARTNER_FIELDS = [
    'id', 'name', 'street', 'street2', 'city', 'zip',
    'phone', 'email', 'country_id', 'state_id', 'comment',
    # type/parent/comercial para distinguir dirección principal vs de envío
    'type', 'parent_id', 'commercial_partner_id',
    # Studio fields de Paracarpinteros (verificados en prod): cantón y distrito
    # CR como Many2one a modelos custom, señas como texto. Sin estos, la etiqueta
    # de Correos no incluye prov/cantón/distrito y solo imprime la dirección.
    'x_studio_canton_cr', 'x_studio_distrito_cr', 'x_studio_senas',
]


class OdooClient:
    def __init__(self, url: str, db: str, username: str, api_key: str):
        self.url = url.rstrip('/')
        self.db = db
        self.username = username
        self.api_key = api_key
        self._uid: Optional[int] = None
        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        # Mapa ZIP CR → (distrito_id, distrito_name, canton_id, canton_name).
        # Se carga perezosamente en read_partner cuando un partner no tiene
        # los Studio fields rellenos pero sí ZIP, para sintetizar la geografía.
        self._zip_map: Optional[dict] = None

    def authenticate(self) -> int:
        if self._uid is not None:
            return self._uid
        try:
            uid = self._common.authenticate(self.db, self.username, self.api_key, {})
        except Exception as e:
            raise OdooError(f"Error autenticando con Odoo: {e}")
        if not uid:
            raise OdooError("Credenciales Odoo inválidas (uid=False)")
        self._uid = uid
        _logger.info("Autenticado en Odoo como uid=%s", uid)
        return uid

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict = None):
        kwargs = kwargs or {}
        for intento in range(2):
            # authenticate() también puede caer en ResponseNotReady/Idle si el ServerProxy
            # tiene la conexión TCP rota; por eso va dentro del try para que el retry la cubra.
            try:
                uid = self.authenticate()
                return self._models.execute_kw(
                    self.db, uid, self.api_key,
                    model, method, args, kwargs,
                )
            except xmlrpc.client.Fault as e:
                raise OdooError(f"Odoo fault en {model}.{method}: {e.faultString}")
            except Exception as e:
                err_str = str(e)
                # xmlrpc.client.ServerProxy no es thread-safe — bajo carga, dos requests
                # concurrentes pueden pisar la conexión HTTP y dejarla en estado
                # Request-sent / Idle / ResponseNotReady. Reabrimos transport y reautenticamos.
                transient = (
                    'Request-sent' in err_str or 'CannotSendRequest' in err_str
                    or 'BrokenPipe' in err_str or 'Connection' in err_str
                    or 'ResponseNotReady' in err_str or 'Idle' in err_str
                )
                if intento == 0 and transient:
                    _logger.warning(f"Conexion XMLRPC corrupta en {model}.{method}, reabriendo: {err_str}")
                    self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
                    self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
                    self._uid = None
                    continue
                raise OdooError(f"Error Odoo en {model}.{method}: {e}")

    # ───────────── Pickings ─────────────

    def search_pickings_pendientes(self, limit: int = 50) -> list:
        """
        Busca pickings validados (state='done') que aún no tienen guía.
        Criterio: state='done' AND carrier_tracking_ref is False AND country CR.
        """
        domain = [
            ('state', '=', 'done'),
            ('carrier_tracking_ref', '=', False),
            ('partner_id.country_id.code', '=', 'CR'),
            ('picking_type_code', '=', 'outgoing'),
            ('carrier_id', '=', 2),  # solo Pymexpress
            ('date_done', '>=', '2026-04-23 18:00:00'),  # solo salidas
        ]
        ids = self.execute_kw(
            'stock.picking', 'search',
            [domain],
            {'limit': limit, 'order': 'date_done desc'}
        )
        if not ids:
            return []
        return self.execute_kw(
            'stock.picking', 'read',
            [ids],
            {'fields': [
                'id', 'name', 'partner_id', 'origin', 'date_done',
                'carrier_tracking_ref', 'weight', 'move_ids',
                'company_id',
            ]}
        )

    def _load_zip_map(self) -> dict:
        """Construye {zip: (dist_id, dist_name, cant_id, cant_name)} desde la
        tabla maestra x_distrito_cr. Se llama una vez por proceso."""
        if self._zip_map is not None:
            return self._zip_map
        try:
            rows = self.execute_kw(
                'x_distrito_cr', 'search_read',
                [[('x_studio_zip', '!=', False)]],
                {'fields': ['id', 'x_name', 'x_studio_zip', 'x_studio_canton_cr']}
            )
        except OdooError as e:
            _logger.warning("No se pudo cargar x_distrito_cr (zip fallback deshabilitado): %s", e)
            self._zip_map = {}
            return self._zip_map
        m = {}
        for d in rows:
            z = (d.get('x_studio_zip') or '').strip()
            cant = d.get('x_studio_canton_cr')
            if z and len(z) == 5 and cant:
                m[z] = (d['id'], d.get('x_name') or '', cant[0], cant[1])
        self._zip_map = m
        _logger.info("Cargado mapa ZIP→distrito (%d entradas)", len(m))
        return m

    def _apply_zip_fallback(self, partner: dict) -> dict:
        """Si el partner no tiene cantón/distrito pero sí ZIP CR válido,
        sintetiza los valores desde el master x_distrito_cr. Así la etiqueta
        de Correos imprime distrito+cantón+provincia incluso para partners
        nuevos que aún no se hayan rellenado. Muta y devuelve el dict."""
        z = (partner.get('zip') or '').strip().replace(' ', '').replace('-', '')
        if (z.isdigit() and len(z) == 5
            and not partner.get('x_studio_canton_cr')
            and not partner.get('x_studio_distrito_cr')):
            entry = self._load_zip_map().get(z)
            if entry:
                dist_id, dist_name, cant_id, cant_name = entry
                partner['x_studio_distrito_cr'] = [dist_id, dist_name]
                partner['x_studio_canton_cr'] = [cant_id, cant_name]
        return partner

    def read_partner(self, partner_id: int) -> dict:
        r = self.execute_kw(
            'res.partner', 'read', [[partner_id]],
            {'fields': _PARTNER_FIELDS}
        )
        if not r:
            return {}
        return self._apply_zip_fallback(r[0])

    def list_addresses(self, partner_id: int) -> list:
        """Devuelve las direcciones del cliente al que pertenece `partner_id`:
        la principal (partner comercial) + sus direcciones de envío hijas
        (type='delivery'). La entrada que corresponde a `partner_id` (la que el
        picking ya usa) lleva is_current=True para que el panel la preseleccione.

        Sirve para que el panel ofrezca elegir entre las direcciones reales del
        cliente en Odoo en vez de tipear a mano (clientes con varias direcciones)."""
        base = self.execute_kw('res.partner', 'read', [[partner_id]],
                               {'fields': ['commercial_partner_id']})
        if not base:
            return []
        commercial_id = (base[0].get('commercial_partner_id') or [partner_id])[0]
        child_ids = self.execute_kw(
            'res.partner', 'search',
            [[('parent_id', '=', commercial_id), ('type', '=', 'delivery')]],
            {'order': 'id desc'}
        )
        # Orden: principal primero, luego direcciones de envío. Dedup conservando
        # orden y garantizando que el partner actual del picking esté incluido.
        ordered = []
        for i in [commercial_id] + list(child_ids) + [partner_id]:
            if i not in ordered:
                ordered.append(i)
        rows = self.execute_kw('res.partner', 'read', [ordered],
                               {'fields': _PARTNER_FIELDS})
        by_id = {r['id']: r for r in rows}
        out = []
        for i in ordered:
            r = by_id.get(i)
            if not r:
                continue
            self._apply_zip_fallback(r)
            r['is_current'] = (i == partner_id)
            out.append(r)
        return out

    def create_delivery_address(self, commercial_id: int, vals: dict) -> int:
        """Crea un contacto hijo type='delivery' bajo el cliente comercial.
        Nunca toca la ficha principal. Devuelve el id del nuevo partner."""
        base = self.execute_kw('res.partner', 'read', [[commercial_id]],
                               {'fields': ['country_id']})
        country = (base[0].get('country_id') or [None])[0] if base else None
        create_vals = {'type': 'delivery', 'parent_id': commercial_id}
        if country:
            create_vals['country_id'] = country
        create_vals.update(vals)
        return self.execute_kw('res.partner', 'create', [create_vals])

    def read_picking_moves(self, picking_id: int) -> list:
        """Lee las líneas del picking con producto + cantidad + peso."""
        move_ids = self.execute_kw(
            'stock.move', 'search',
            [[('picking_id', '=', picking_id)]],
        )
        if not move_ids:
            return []
        return self.execute_kw(
            'stock.move', 'read', [move_ids],
            {'fields': ['product_id', 'product_uom_qty', 'quantity']}
        )

    def read_product_weights(self, product_ids: list) -> dict:
        """Devuelve dict {product_id: weight_kg}"""
        if not product_ids:
            return {}
        rows = self.execute_kw(
            'product.product', 'read', [product_ids],
            {'fields': ['id', 'weight']}
        )
        return {r['id']: r.get('weight') or 0 for r in rows}

    # ───────────── Escritura ─────────────

    def set_tracking(self, picking_id: int, tracking_ref: str):
        self.execute_kw(
            'stock.picking', 'write',
            [[picking_id], {'carrier_tracking_ref': tracking_ref}]
        )

    def attach_pdf(self, picking_id: int, filename: str, pdf_b64: str) -> int:
        """Crea un ir.attachment asociado al picking y devuelve su ID."""
        att_id = self.execute_kw(
            'ir.attachment', 'create',
            [{
                'name': filename,
                'type': 'binary',
                'datas': pdf_b64,
                'mimetype': 'application/pdf',
                'res_model': 'stock.picking',
                'res_id': picking_id,
            }]
        )
        return att_id

    def post_message(self, picking_id: int, body: str, attachment_ids: list = None):
        self.execute_kw(
            'stock.picking', 'message_post',
            [[picking_id]],
            {
                'body': body,
                'attachment_ids': attachment_ids or [],
                'message_type': 'comment',
            }
        )
