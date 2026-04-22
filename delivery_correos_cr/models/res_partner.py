# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Códigos CR según el WS de Correos (Provincia-Cantón-Distrito).
    # No los usamos como selection todavía porque son dinámicos desde el WS;
    # guardamos el código que pide el WS (string corto).
    correos_cr_provincia_code = fields.Char(
        string='Cód. Provincia CR',
        size=1,
        help='Código de provincia según Correos CR. Ej: 3 = Cartago.',
    )
    correos_cr_canton_code = fields.Char(
        string='Cód. Cantón CR',
        size=2,
        help='Código de cantón según Correos CR. Ej: 05 = Turrialba.',
    )
    correos_cr_distrito_code = fields.Char(
        string='Cód. Distrito CR',
        size=2,
        help='Código de distrito según Correos CR. Ej: 04 = Santa Cruz.',
    )
    correos_cr_zip = fields.Char(
        string='Código Postal CR',
        size=8,
        help='Código postal de 5 dígitos (ej: 30504). Se puede consultar vía WS con provincia/cantón/distrito.',
    )
