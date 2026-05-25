#!/usr/bin/env python3
"""
Crea en Odoo el producto: Extractor de Polvo Industrial EMF9030.

Uso:
    python3 scripts/crear_producto_emf9030.py --image /ruta/a/foto.jpg

Credenciales: lee ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY desde el
.env de la raíz del proyecto (creado a partir de .env.example).
"""
import argparse
import base64
import os
import sys
import xmlrpc.client

from _env import load_project_env

load_project_env()

ODOO_URL = os.environ.get("ODOO_URL", "https://paracarpinteros.odoo.com")
ODOO_DB = os.environ.get("ODOO_DB", "paracarpinteros")
ODOO_USER = os.environ.get("ODOO_USERNAME", "manelcomasbre@gmail.com")

PRODUCT_NAME = "Extractor de Polvo Industrial EMF9030 (3 kW)"
INTERNAL_REF = "EXT-EMF9030"
SALE_PRICE_CRC = 525000.0
GROSS_WEIGHT_KG = 70.0

DESCRIPTION_SALE = (
    "Extractor de polvo industrial — Modelo EMF9030\n"
    "\n"
    "• Motor: 3 kW (≈ 4 HP)\n"
    "• Caudal de aire: 3.150 m³/h\n"
    "• Velocidad de aire: 35–40 m/s\n"
    "• Entradas de succión: Ø 4\" × 3 unidades\n"
    "• Bolsas colectoras: Ø 470 mm × 4 unidades\n"
    "• Dimensiones: 1.380 × 550 × 2.000 mm\n"
    "• Peso neto / bruto: 67 kg / 70 kg"
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", help="Ruta a la foto del producto (.jpg/.png). Opcional.")
    ap.add_argument("--api-key", default=os.environ.get("ODOO_API_KEY"),
                    help="API key de Odoo (por defecto, la del .env)")
    args = ap.parse_args()

    if not args.api_key:
        print("ERROR: falta ODOO_API_KEY. Editá el .env raíz o pasá --api-key.", file=sys.stderr)
        return 1

    image_b64 = None
    if args.image:
        if not os.path.isfile(args.image):
            print(f"ERROR: no existe el archivo {args.image}", file=sys.stderr)
            return 1
        with open(args.image, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()

    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(ODOO_DB, ODOO_USER, args.api_key, {})
    if not uid:
        print("ERROR: autenticación falló (revisá la API key).", file=sys.stderr)
        return 1
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)

    def call(model, method, m_args, kwargs=None):
        return models.execute_kw(ODOO_DB, uid, args.api_key, model, method, m_args, kwargs or {})

    existing = call("product.template", "search", [[("default_code", "=", INTERNAL_REF)]])
    if existing:
        print(f"Ya existe un producto con referencia {INTERNAL_REF}: id={existing}")
        print("Si querés actualizarlo en lugar de crearlo, modificá este script.")
        return 2

    vals = {
        "name": PRODUCT_NAME,
        "default_code": INTERNAL_REF,
        "type": "consu",
        "is_storable": True,
        "sale_ok": True,
        "purchase_ok": True,
        "list_price": SALE_PRICE_CRC,
        "weight": GROSS_WEIGHT_KG,
        "description_sale": DESCRIPTION_SALE,
    }
    if image_b64:
        vals["image_1920"] = image_b64
    tmpl_id = call("product.template", "create", [vals])
    print(f"OK — product.template creado id={tmpl_id}")
    print(f"Abrilo en: {ODOO_URL}/odoo/inventory/{tmpl_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
