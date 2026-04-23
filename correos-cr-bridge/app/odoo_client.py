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


class OdooClient:
    def __init__(self, url: str, db: str, username: str, api_key: str):
        self.url = url.rstrip('/')
        self.db = db
        self.username = username
        self.api_key = api_key
        self._uid: Optional[int] = None
        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

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
        uid = self.authenticate()
        kwargs = kwargs or {}
        try:
            return self._models.execute_kw(
                self.db, uid, self.api_key,
                model, method, args, kwargs,
            )
        except xmlrpc.client.Fault as e:
            raise OdooError(f"Odoo fault en {model}.{method}: {e.faultString}")
        except Exception as e:
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
            ('picking_type_code', '=', 'outgoing'),  # solo salidas
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
                'carrier_tracking_ref', 'weight', 'move_ids_without_package',
                'company_id',
            ]}
        )

    def read_partner(self, partner_id: int) -> dict:
        r = self.execute_kw(
            'res.partner', 'read', [[partner_id]],
            {'fields': [
                'id', 'name', 'street', 'street2', 'city', 'zip',
                'phone', 'mobile', 'email', 'country_id', 'state_id',
            ]}
        )
        return r[0] if r else {}

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
