#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Añade al custom_code_head del website 3 el bloque PC-FICHA-SEARCH: CSS que
iguala el buscador nativo de la ficha de producto (o_wsale_products_searchbar_form,
opción "Buscador" de la tienda) al estilo del pc-shop-search del header
(borde oscuro 2px, radio 8, botón verde), que ya vive en ese mismo head.

Idempotente por marcador pc-ficha-search. Backup del head en scripts/_backups/.

Uso:
  python3 scripts/inject_ficha_search_style.py            # dry-run
  python3 scripts/inject_ficha_search_style.py --apply    # aplica
"""
import datetime
import os
import sys
import xmlrpc.client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_project_env
load_project_env()

URL = os.environ["ODOO_URL"]
DB = os.environ["ODOO_DB"]
USER = os.environ.get("ODOO_USER") or os.environ.get("ODOO_USERNAME")
KEY = os.environ.get("ODOO_API_KEY")
WEBSITE_ID = 3
APPLY = "--apply" in sys.argv

MARCA = "pc-ficha-search"
SNIPPET = """
<style id="pc-ficha-search">
/* PC-FICHA-SEARCH: iguala el buscador nativo de la ficha de producto al estilo pc-shop-search */
.o_wsale_product_page .o_wsale_products_searchbar_form .input-group{
  background:#fff;border:2px solid var(--pc-dark);border-radius:8px;overflow:hidden;
  box-shadow:0 4px 12px rgba(0,0,0,.08);
}
.o_wsale_product_page .o_wsale_products_searchbar_form .oe_search_box{
  border:0!important;background:#fff!important;padding:10px 16px;font-size:15px;color:var(--pc-dark);
}
.o_wsale_product_page .o_wsale_products_searchbar_form .oe_search_box::placeholder{color:#888;}
.o_wsale_product_page .o_wsale_products_searchbar_form .oe_search_button{
  background:var(--pc-green)!important;border:0!important;padding:0 22px;border-radius:0;
}
.o_wsale_product_page .o_wsale_products_searchbar_form .oe_search_button i{color:#fff;}
.o_wsale_product_page .o_wsale_products_searchbar_form .oe_search_button:hover{background:var(--pc-green-dark)!important;}
/* linea blanca bajo el header en fichas: el margen de #product_detail colapsa y deja ver el fondo blanco del #wrapwrap */
#wrapwrap{background-color:#f4ecdd;}
</style>
"""


def main():
    common = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    models = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/object", allow_none=True)

    def call(model, method, *args, **kw):
        return models.execute_kw(DB, uid, KEY, model, method, list(args), kw)

    web = call("website", "read", [WEBSITE_ID], fields=["name", "custom_code_head"])[0]
    head = web["custom_code_head"] or ""
    if MARCA in head:
        print(f"El head del website {WEBSITE_ID} ya tiene {MARCA}; nada que hacer.")
        return

    print(f"Website {WEBSITE_ID} ({web['name']}): head actual {len(head)} chars; "
          f"se añadirían {len(SNIPPET)} chars.")
    if not APPLY:
        print("Dry-run. Ejecutar con --apply para escribir.")
        return

    bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bpath = os.path.join(bdir, f"website{WEBSITE_ID}_custom_code_head_{stamp}.html")
    with open(bpath, "w") as f:
        f.write(head)
    call("website", "write", [WEBSITE_ID], {"custom_code_head": head + SNIPPET})
    print(f"Aplicado. Backup del head previo: {bpath}")


if __name__ == "__main__":
    main()
