# -*- coding: utf-8 -*-
import base64
import logging

from odoo import fields, models, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    correos_cr_peso_g = fields.Integer(
        string='Peso calculado (g)',
        compute='_compute_correos_cr_peso',
        help='Peso calculado en gramos para Correos CR. Usa peso del producto si existe, '
             'default configurable si no.',
    )
    correos_cr_label_attachment_id = fields.Many2one(
        'ir.attachment',
        string='Etiqueta Correos CR',
        readonly=True,
    )

    @api.depends('move_ids_without_package.product_id', 'move_ids_without_package.product_uom_qty')
    def _compute_correos_cr_peso(self):
        ICP = self.env['ir.config_parameter'].sudo()
        default_g = int(ICP.get_param('delivery_correos_cr.default_weight_g', '500'))
        for pk in self:
            total = 0.0
            for move in pk.move_ids_without_package:
                weight_kg = move.product_id.weight or 0
                qty = move.product_uom_qty or 0
                if weight_kg > 0:
                    total += weight_kg * 1000 * qty
                else:
                    total += default_g * qty
            pk.correos_cr_peso_g = max(int(total), default_g)

    # ───────────────── flujo de envío ─────────────────

    def _correos_cr_process_shipment(self):
        """
        Para cada picking: pide número de guía, registra envío y adjunta el PDF.
        Devuelve la estructura que Odoo delivery espera.
        """
        settings = self.env['res.config.settings']
        client = settings._get_correos_cr_client()
        results = []

        for pk in self:
            if pk.carrier_tracking_ref:
                # Ya tiene guía — no duplicar
                results.append({
                    'exact_price': pk.carrier_price or 0.0,
                    'tracking_number': pk.carrier_tracking_ref,
                })
                continue

            # 1) Generar guía
            envio_id = client.generar_guia()
            _logger.info("Correos CR: guía generada %s para picking %s", envio_id, pk.name)

            # 2) Registrar envío
            envio_data = pk._correos_cr_build_envio_data()
            try:
                cod, msg, pdf_b64 = client.registrar_envio(envio_id, envio_data)
            except UserError:
                raise
            except Exception as e:
                raise UserError(_(
                    "Error registrando envío %(envio)s en Correos CR: %(err)s",
                    envio=envio_id, err=str(e)
                ))

            # 3) Adjuntar PDF
            if pdf_b64:
                attachment = self.env['ir.attachment'].create({
                    'name': f"Etiqueta_CorreosCR_{envio_id}.pdf",
                    'type': 'binary',
                    'datas': pdf_b64 if isinstance(pdf_b64, str) else base64.b64encode(pdf_b64).decode(),
                    'mimetype': 'application/pdf',
                    'res_model': 'stock.picking',
                    'res_id': pk.id,
                })
                pk.correos_cr_label_attachment_id = attachment.id

            pk.carrier_tracking_ref = envio_id
            pk.message_post(
                body=_("Guía Correos CR generada: <b>%s</b>") % envio_id,
                attachment_ids=[pk.correos_cr_label_attachment_id.id] if pk.correos_cr_label_attachment_id else [],
            )

            results.append({
                'exact_price': 0.0,  # Correos cobra por contrato, no por envío individual
                'tracking_number': envio_id,
            })

        return results

    def _correos_cr_build_envio_data(self):
        """Construye dict para ccrRegistroEnvio desde el picking."""
        self.ensure_one()
        dest = self.partner_id
        company_partner = self.company_id.partner_id

        if not dest:
            raise UserError(_("El picking no tiene cliente asignado."))

        # Validación de campos CR
        missing_dest = []
        if not dest.correos_cr_zip:
            missing_dest.append('Código postal')
        if not (dest.street or dest.street2):
            missing_dest.append('Dirección')
        if missing_dest:
            raise UserError(_(
                "Dirección del destinatario '%(name)s' incompleta: falta %(fields)s. "
                "Revisa el contacto antes de validar el picking.",
                name=dest.name,
                fields=', '.join(missing_dest),
            ))

        direccion_dest = ', '.join(filter(None, [dest.street, dest.street2, dest.city]))
        direccion_send = ', '.join(filter(None, [
            company_partner.street,
            company_partner.street2,
            company_partner.city,
        ]))

        return {
            'fecha_envio': fields.Datetime.now(),
            'monto_flete': 0,  # opcional; el contrato fija tarifa
            'dest_nombre': dest.name or '',
            'dest_direccion': direccion_dest,
            'dest_telefono': (dest.phone or dest.mobile or '').replace(' ', ''),
            'dest_zip': dest.correos_cr_zip or '',
            'send_nombre': company_partner.name or '',
            'send_direccion': direccion_send,
            'send_zip': company_partner.correos_cr_zip or '',
            'send_telefono': (company_partner.phone or '').replace(' ', ''),
            'observaciones': self.origin or self.name,
            'peso': self.correos_cr_peso_g,
        }

    # ───────────────── acciones manuales ─────────────────

    def action_correos_cr_reprint_label(self):
        """Re-imprime la etiqueta existente (no pide nueva guía)."""
        self.ensure_one()
        if not self.correos_cr_label_attachment_id:
            raise UserError(_("Este picking no tiene etiqueta Correos CR generada."))
        return {
            'type': 'ir.actions.act_url',
            'url': f"/web/content/{self.correos_cr_label_attachment_id.id}?download=true",
            'target': 'self',
        }

    def action_correos_cr_update_tracking(self):
        """Consulta ccrMovilTracking y publica eventos en el chatter."""
        self.ensure_one()
        if not self.carrier_tracking_ref:
            raise UserError(_("No hay número de guía para consultar."))
        client = self.env['res.config.settings']._get_correos_cr_client()
        data = client.tracking(self.carrier_tracking_ref)
        eventos = data.get('eventos') or []
        if not eventos:
            self.message_post(body=_("Sin eventos aún — el envío debe estar admitido en planta para aparecer."))
            return True
        html = "<b>Rastreo Correos CR:</b><ul>"
        for e in eventos:
            html += f"<li>{e['fecha']} — {e['evento']} ({e['unidad']})</li>"
        html += "</ul>"
        self.message_post(body=html)
        return True
