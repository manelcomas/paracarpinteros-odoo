# paracarpinteros-odoo

Módulos custom para Odoo 19 Enterprise de Paracarpinteros (Gabriela Brenes Solano, Turrialba, CR).

## Módulos

### delivery_correos_cr

Integración con el Web Service de Correos de Costa Rica (Pymexpress) para generar guías de envío automáticamente al validar el albarán (picking) en Odoo.

**Flujo:**

1. Validar picking → llamada `ccrGenerarGuia` (obtiene número de guía)
2. Llamada `ccrRegistroEnvio` con ese número → devuelve PDF de etiqueta en Base64
3. PDF se adjunta al picking y el número de guía se guarda en `carrier_tracking_ref`

**Requisitos:**

- Odoo 19.0 Enterprise
- Python `zeep` (SOAP client) — en `requirements.txt`
- Credenciales activas de Correos CR (ver config módulo)

## Despliegue en Odoo.sh

Odoo.sh detecta automáticamente los módulos en la raíz del repo. Basta con hacer push a la rama conectada al entorno correspondiente:

- `main` → Production
- `staging-correos-cr` → Staging (pruebas con credenciales QA)

Tras push: Odoo.sh reconstruye el entorno. Luego en Odoo:
**Apps → Actualizar lista → buscar "Correos CR" → Instalar**.
