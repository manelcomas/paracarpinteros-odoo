# -*- coding: utf-8 -*-
from odoo import fields, models, api, _
from odoo.exceptions import UserError

from .correos_cr_client import CorreosCRClient


# Endpoints del documento oficial (PDF Sharon Rodríguez, 09/02/2023)
ENDPOINTS = {
    'test': {
        'token_url': 'https://servicios.correos.go.cr:442/Token/authenticate',
        'soap_url': 'http://amistad.correos.go.cr:84/wsAppCorreos.wsAppCorreos.svc',
        'tracking_url': 'https://servicios.correos.go.cr/rastreoQA/consulta_envios/rastreo.aspx',
    },
    'production': {
        # Se actualizan cuando Correos CR entregue los datos de producción.
        'token_url': 'https://servicios.correos.go.cr/Token/authenticate',
        'soap_url': 'https://servicios.correos.go.cr/wsAppCorreos.wsAppCorreos.svc',
        'tracking_url': 'https://correos.go.cr/rastreo',
    },
}


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    correos_cr_environment = fields.Selection(
        [('test', 'Pruebas (QA)'),
         ('production', 'Producción')],
        string='Ambiente Correos CR',
        default='test',
        config_parameter='delivery_correos_cr.environment',
    )
    correos_cr_username = fields.Char(
        string='Username',
        config_parameter='delivery_correos_cr.username',
    )
    correos_cr_password = fields.Char(
        string='Password',
        config_parameter='delivery_correos_cr.password',
    )
    correos_cr_sistema = fields.Char(
        string='Sistema',
        default='PYMEXPRESS',
        config_parameter='delivery_correos_cr.sistema',
    )
    correos_cr_user_id = fields.Char(
        string='Usuario ID',
        help='Cédula del usuario registrado (ej: 304410837)',
        config_parameter='delivery_correos_cr.user_id',
    )
    correos_cr_servicio_id = fields.Char(
        string='Servicio ID',
        default='73',
        help='73 = Pymexpress',
        config_parameter='delivery_correos_cr.servicio_id',
    )
    correos_cr_codigo_cliente = fields.Char(
        string='Código Cliente',
        config_parameter='delivery_correos_cr.codigo_cliente',
    )
    correos_cr_default_weight_g = fields.Integer(
        string='Peso por defecto (gramos)',
        default=500,
        help='Peso a usar cuando el producto no tiene peso cargado en Odoo.',
        config_parameter='delivery_correos_cr.default_weight_g',
    )

    def action_correos_cr_test_connection(self):
        """Botón 'Probar conexión' — pide un token y consulta provincias."""
        self.ensure_one()
        client = self._get_correos_cr_client()
        try:
            token = client.get_token()
            provincias = client.get_provincias()
        except Exception as e:
            raise UserError(_("Fallo la prueba de conexión: %s") % str(e))

        msg = _(
            "Conexión OK. Token obtenido y %d provincias recibidas del WS.",
            len(provincias)
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Correos CR'),
                'message': msg,
                'type': 'success',
                'sticky': False,
            }
        }

    @api.model
    def _get_correos_cr_client(self):
        """Factory central usado por settings, stock.picking y cron jobs."""
        ICP = self.env['ir.config_parameter'].sudo()
        env = ICP.get_param('delivery_correos_cr.environment', 'test')
        endpoints = ENDPOINTS.get(env, ENDPOINTS['test'])

        required = {
            'username': ICP.get_param('delivery_correos_cr.username'),
            'password': ICP.get_param('delivery_correos_cr.password'),
            'sistema': ICP.get_param('delivery_correos_cr.sistema', 'PYMEXPRESS'),
            'user_id': ICP.get_param('delivery_correos_cr.user_id'),
            'servicio_id': ICP.get_param('delivery_correos_cr.servicio_id', '73'),
            'codigo_cliente': ICP.get_param('delivery_correos_cr.codigo_cliente'),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise UserError(_(
                "Faltan parámetros de Correos CR en Ajustes: %s"
            ) % ', '.join(missing))

        return CorreosCRClient(
            token_url=endpoints['token_url'],
            soap_url=endpoints['soap_url'],
            **required,
        )
