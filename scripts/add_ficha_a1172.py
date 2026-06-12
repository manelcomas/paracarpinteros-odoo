#!/usr/bin/env python3
"""Sube la ficha técnica PNG de A1172 como attachment público y la añade
al final de la website_description del product.template 6573.

Idempotente: si la descripción ya contiene el marcador `ficha-tecnica-a1172`,
no duplica nada.
"""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_project_env

load_project_env()

import xmlrpc.client

TEMPLATE_ID = 6573
MARCADOR = "ficha-tecnica-a1172"
PNG_PATH = os.environ.get("FICHA_PNG", "/mnt/c/Users/Manel/Downloads/ficha-A1172.png")


def main():
    url, db = os.environ["ODOO_URL"], os.environ["ODOO_DB"]
    user, key = os.environ["ODOO_USERNAME"], os.environ["ODOO_API_KEY"]
    common = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(db, user, key, {})
    models = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/object", allow_none=True)

    def call(model, method, *args, **kw):
        return models.execute_kw(db, uid, key, model, method, list(args), kw)

    rec = call("product.template", "read", [TEMPLATE_ID], fields=["website_description"])[0]
    desc = rec["website_description"] or ""
    if MARCADOR in desc:
        print(f"La descripción del template {TEMPLATE_ID} ya tiene la ficha ({MARCADOR}); no se toca nada.")
        return

    with open(PNG_PATH, "rb") as f:
        datas = base64.b64encode(f.read()).decode()

    # Reusar attachment si ya existe de una corrida anterior
    existentes = call("ir.attachment", "search", [["name", "=", "ficha-A1172.png"], ["public", "=", True]])
    if existentes:
        att_id = existentes[0]
        call("ir.attachment", "write", [att_id], {"datas": datas})
        print(f"Attachment existente {att_id} actualizado.")
    else:
        att_id = call("ir.attachment", "create", {
            "name": "ficha-A1172.png",
            "datas": datas,
            "mimetype": "image/png",
            "public": True,
            "res_model": "product.template",
            "res_id": TEMPLATE_ID,
        })
        print(f"Attachment creado: id {att_id}")

    bloque = (
        f'\n<section class="pt-4 pb-5 {MARCADOR}" style="text-align:center">\n'
        f'  <h3 style="margin-bottom:1rem">Ficha técnica</h3>\n'
        f'  <img src="/web/image/{att_id}-ficha-a1172" alt="Ficha técnica A1172 — '
        f'soportes plegables de acero inoxidable 30x17.5 cm, carga máxima 132.3 lbs" '
        f'style="max-width:100%;height:auto" loading="lazy"/>\n'
        f"</section>\n"
    )

    # Insertar dentro del div raíz si la descripción termina en </div>; si no, al final
    if desc.rstrip().endswith("</div>"):
        pos = desc.rstrip().rfind("</div>")
        nueva = desc.rstrip()[:pos] + bloque + "</div>"
    else:
        nueva = desc + bloque

    call("product.template", "write", [TEMPLATE_ID], {"website_description": nueva})
    print(f"website_description del template {TEMPLATE_ID} actualizada "
          f"({len(desc)} → {len(nueva)} chars). Imagen: {url}/web/image/{att_id}-ficha-a1172")


if __name__ == "__main__":
    main()
