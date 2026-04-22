# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError


class CorreosCRTestConnection(models.TransientModel):
    _name = 'correos.cr.test.connection'
    _description = 'Prueba de conexión con Correos CR'

    result = fields.Text(string='Resultado', readonly=True)

    def action_run(self):
        client = self.env['res.config.settings']._get_correos_cr_client()
        lines = []
        try:
            token = client.get_token()
            lines.append("✓ Token obtenido OK (caché 5 min activa)")
        except Exception as e:
            lines.append(f"✗ Token: {e}")
            self.result = '\n'.join(lines)
            return self._return_wizard()

        try:
            provs = client.get_provincias()
            lines.append(f"✓ {len(provs)} provincias recibidas")
            for code, desc in provs[:10]:
                lines.append(f"   {code} — {desc}")
        except Exception as e:
            lines.append(f"✗ Provincias: {e}")

        self.result = '\n'.join(lines)
        return self._return_wizard()

    def _return_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'correos.cr.test.connection',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
