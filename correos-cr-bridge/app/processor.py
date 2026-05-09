# -*- coding: utf-8 -*-
"""
Orquestador: detecta pickings pendientes en Odoo, llama a Correos CR,
y adjunta el PDF de la etiqueta al picking.
"""

import base64
import logging
from datetime import datetime

from .config import settings
from .correos_client import CorreosCRClient, CorreosCRError
from .odoo_client import OdooClient, OdooError

_logger = logging.getLogger(__name__)


class Processor:
    def __init__(self):
        self.correos = CorreosCRClient(
            username=settings.correos_username,
            password=settings.correos_password,
            sistema=settings.correos_sistema,
            user_id=settings.correos_user_id,
            servicio_id=settings.correos_servicio_id,
            codigo_cliente=settings.correos_codigo_cliente,
            token_url=settings.correos_token_url,
            soap_url=settings.correos_soap_url,
        )
        self.odoo = OdooClient(
            url=settings.odoo_url,
            db=settings.odoo_db,
            username=settings.odoo_username,
            api_key=settings.odoo_api_key,
        )

    def run_once(self) -> dict:
        """Una pasada: procesa todos los pickings pendientes."""
        start = datetime.now()
        stats = {'checked': 0, 'processed': 0, 'errors': 0, 'errors_detail': []}

        try:
            pickings = self.odoo.search_pickings_pendientes(limit=20)
        except OdooError as e:
            _logger.error("No pude leer pickings de Odoo: %s", e)
            stats['errors'] += 1
            stats['errors_detail'].append(f"Odoo read: {e}")
            return stats

        stats['checked'] = len(pickings)
        _logger.info("Pickings pendientes encontrados: %d", len(pickings))

        for pk in pickings:
            try:
                self._process_one(pk)
                stats['processed'] += 1
            except Exception as e:
                stats['errors'] += 1
                stats['errors_detail'].append(f"Picking {pk.get('name')}: {e}")
                _logger.exception("Error procesando picking %s", pk.get('name'))
                # Publicar el error en el chatter del picking
                try:
                    self.odoo.post_message(
                        pk['id'],
                        f"<b>⚠ Error generando guía Correos CR:</b><br/>{e}"
                    )
                except Exception:
                    pass

        stats['duration_s'] = (datetime.now() - start).total_seconds()
        return stats

    def _process_one(self, picking: dict):
        pk_id = picking['id']
        pk_name = picking['name']
        _logger.info("Procesando picking %s (id=%d)", pk_name, pk_id)

        # 1) Leer partner destino
        partner_id = picking['partner_id'][0] if picking.get('partner_id') else None
        if not partner_id:
            raise Exception("Picking sin cliente asignado")
        partner = self.odoo.read_partner(partner_id)

        # Validaciones mínimas
        if not partner.get('zip'):
            raise Exception(f"Cliente '{partner.get('name')}' sin código postal")
        provincia = partner.get('state_id')
        provincia_name = provincia[1] if provincia and isinstance(provincia, (list, tuple)) and len(provincia) > 1 else None
        direccion_dest = ', '.join(filter(None, [
            partner.get('street'), partner.get('street2'), partner.get('city'), provincia_name,
        ]))
        if not direccion_dest:
            raise Exception(f"Cliente '{partner.get('name')}' sin dirección")

        # 2) Calcular peso real
        peso_g = self._calc_peso(pk_id)

        # 3) Llamar al WS
        envio_id = self.correos.generar_guia()
        _logger.info("Guía generada: %s", envio_id)

        envio_data = {
            'fecha_envio': datetime.now(),
            'monto_flete': 0,
            'dest_nombre': partner.get('name', ''),
            'dest_direccion': direccion_dest,
            'dest_telefono': (partner.get('phone') or '').replace(' ', '').replace('-', ''),
            'dest_zip': partner.get('zip', ''),
            'send_nombre': settings.sender_name,
            'send_direccion': settings.sender_address,
            'send_zip': settings.sender_zip,
            'send_telefono': settings.sender_phone.replace(' ', '').replace('-', ''),
            'observaciones': 'Herramientas',
            'peso': peso_g,
        }
        cod, msg, pdf_b64 = self.correos.registrar_envio(envio_id, envio_data)

        # 4) Adjuntar PDF al picking
        if pdf_b64:
            pdf_str = pdf_b64 if isinstance(pdf_b64, str) else base64.b64encode(pdf_b64).decode()
            att_id = self.odoo.attach_pdf(
                pk_id,
                f"Etiqueta_CorreosCR_{envio_id}.pdf",
                pdf_str,
            )
            self.odoo.post_message(
                pk_id,
                f"✅ <b>Guía Correos CR generada</b><br/>"
                f"Número: <b>{envio_id}</b><br/>"
                f"Peso: {peso_g} g<br/>"
                f"Rastreo: <a href=\"https://correos.go.cr/rastreo?tracking={envio_id}\" target=\"_blank\">Ver en Correos CR</a>",
                attachment_ids=[att_id],
            )

        # 5) Guardar tracking
        self.odoo.set_tracking(pk_id, envio_id)
        _logger.info("Picking %s actualizado con tracking %s", pk_name, envio_id)

    def _calc_peso(self, pk_id: int) -> int:
        moves = self.odoo.read_picking_moves(pk_id)
        if not moves:
            return settings.default_weight_g
        product_ids = list({m['product_id'][0] for m in moves if m.get('product_id')})
        weights = self.odoo.read_product_weights(product_ids)

        total_g = 0.0
        for m in moves:
            pid = m['product_id'][0] if m.get('product_id') else None
            qty = m.get('product_uom_qty') or 0
            w_kg = weights.get(pid, 0) or 0
            if w_kg > 0:
                total_g += w_kg * 1000 * qty
            else:
                total_g += settings.default_weight_g * qty
        return max(int(total_g), settings.default_weight_g)
