# -*- coding: utf-8 -*-
from odoo import fields, models, api, _


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('correos_cr', 'Correos de Costa Rica (Pymexpress)')],
        ondelete={'correos_cr': 'set default'},
    )

    # ───────────────────── HOOKS Odoo delivery ─────────────────────

    def correos_cr_rate_shipment(self, order):
        """
        Calcula tarifa para el carrito e-commerce.
        Usa ccrTarifa con peso total del pedido.
        """
        self.ensure_one()
        settings = self.env['res.config.settings']
        try:
            client = settings._get_correos_cr_client()
        except Exception as e:
            return {
                'success': False,
                'price': 0.0,
                'error_message': _("Error conectando con Correos CR: %s") % str(e),
                'warning_message': False,
            }

        partner = order.partner_shipping_id
        company_partner = order.company_id.partner_id

        # Provincia/cantón: leemos de campos custom en res.partner (ver res_partner.py)
        prov_o = company_partner.correos_cr_provincia_code or '3'
        cant_o = company_partner.correos_cr_canton_code or '05'
        prov_d = partner.correos_cr_provincia_code
        cant_d = partner.correos_cr_canton_code

        if not (prov_d and cant_d):
            return {
                'success': False,
                'price': 0.0,
                'error_message': _(
                    "Dirección del cliente incompleta: falta provincia/cantón CR."
                ),
                'warning_message': False,
            }

        peso_g = self._correos_cr_peso_order(order)
        try:
            tarifa = client.get_tarifa(prov_o, cant_o, prov_d, cant_d, peso_g)
        except Exception as e:
            return {
                'success': False, 'price': 0.0,
                'error_message': str(e), 'warning_message': False,
            }

        price = float(tarifa['monto'] or 0) + float(tarifa['impuesto'] or 0) - float(tarifa['descuento'] or 0)
        return {
            'success': True,
            'price': price,
            'error_message': False,
            'warning_message': False,
        }

    def correos_cr_send_shipping(self, pickings):
        """
        Se llama al validar picking → genera guía y registra envío.
        Devuelve lista con dict por picking (tracking_number y exact_price).
        """
        return pickings._correos_cr_process_shipment()

    def correos_cr_get_tracking_link(self, picking):
        tracking = picking.carrier_tracking_ref
        if not tracking:
            return False
        env = self.env['ir.config_parameter'].sudo().get_param(
            'delivery_correos_cr.environment', 'test'
        )
        base = 'https://servicios.correos.go.cr/rastreoQA/consulta_envios/rastreo.aspx' \
            if env == 'test' else 'https://correos.go.cr/rastreo'
        return f"{base}?tracking={tracking}"

    def correos_cr_cancel_shipment(self, picking):
        """
        El WS actual no expone método de cancelación.
        Se limpia la referencia localmente para que Odoo no bloquee.
        """
        picking.message_post(body=_(
            "Envío Correos CR cancelado localmente. "
            "El WS no permite cancelar guías — contactar a Correos si es necesario."
        ))
        picking.carrier_tracking_ref = False
        return True

    # ───────────────────── HELPERS ─────────────────────

    @staticmethod
    def _correos_cr_peso_order(order):
        """Peso total del pedido en gramos (tolera productos sin peso)."""
        ICP = order.env['ir.config_parameter'].sudo()
        default_g = int(ICP.get_param('delivery_correos_cr.default_weight_g', '500'))
        total = 0.0
        for line in order.order_line.filtered(lambda l: not l.display_type):
            weight_kg = line.product_id.weight or 0
            if weight_kg > 0:
                total += weight_kg * 1000 * line.product_uom_qty
            else:
                total += default_g * line.product_uom_qty
        return max(int(total), default_g)
