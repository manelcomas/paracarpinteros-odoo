{
    'name': 'Correos de Costa Rica - Pymexpress',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'Integración con Web Service Pymexpress (Correos CR) - Generación automática de guías',
    'description': """
Integración con Correos de Costa Rica (Pymexpress)
====================================================

Genera automáticamente la guía de envío y etiqueta PDF al validar el albarán (picking).

Funcionalidades:
 * Autenticación con token (cache 5 min)
 * Generación de número de guía (ccrGenerarGuia)
 * Registro de envío y obtención de PDF etiqueta (ccrRegistroEnvio)
 * Rastreo de envíos (ccrMovilTracking)
 * Ambientes de pruebas y producción configurables
 * Peso: usa el del producto si existe, default configurable si no

Desarrollado para Paracarpinteros (Gabriela Brenes Solano).
    """,
    'author': 'Paracarpinteros',
    'website': 'https://www.paracarpinteros.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'stock',
        'delivery',
        'contacts',
    ],
    'external_dependencies': {
        'python': ['zeep', 'requests'],
    },
    'data': [
        'security/ir.model.access.csv',
        'data/delivery_correos_cr_data.xml',
        'views/res_config_settings_views.xml',
        'views/delivery_carrier_views.xml',
        'views/stock_picking_views.xml',
        'views/res_partner_views.xml',
        'wizards/correos_cr_test_connection_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
