#!/usr/bin/env python3
"""Sube los PNG de fichas-tecnicas/rodamientos/ como attachments públicos y los
inserta en la website_description de cada producto, justo ENCIMA de la sección
CTA de WhatsApp (o al final del div raíz si no hay CTA).

Idempotente por marcador ficha-tecnica-<ref>. Backup de descripciones en
scripts/_backups/fichas_rodamientos_desc_backup.json.
"""
import base64
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_project_env

load_project_env()

import xmlrpc.client

THIS = os.path.dirname(os.path.abspath(__file__))
PNG_DIR = os.path.join(THIS, "..", "fichas-tecnicas", "rodamientos")
BACKUP = os.path.join(THIS, "_backups", "fichas_rodamientos_desc_backup.json")

# (ref, template_id, alt)
PRODUCTOS = [
    ("A649", 1429, 'Ficha técnica rodamiento guía para fresas A649 — Ø interior 4,76 mm (3/16"), Ø exterior 12,7 mm (1/2")'),
    ("A653", 2583, 'Ficha técnica rodamiento guía para fresas A653 — Ø interior 12,7 mm (1/2"), Ø exterior 19,05 mm (3/4")'),
    ("A1093", 6333, 'Ficha técnica rodamiento guía para fresas A1093 — Ø interior 12,7 mm (1/2"), Ø exterior 19,05 mm (3/4"), set de 3'),
    ("A654", 1383, 'Ficha técnica rodamiento guía para fresas A654 — Ø interior 4,76 mm (3/16"), Ø exterior 12,7 mm (1/2"), set de 5'),
    ("A655", 1384, 'Ficha técnica rodamiento guía para fresas A655 — Ø interior 4,76 mm (3/16"), Ø exterior 15,9 mm (5/8"), set de 3'),
    ("A656", 1386, 'Ficha técnica rodamiento guía para fresas A656 — Ø interior 4,76 mm (3/16"), Ø exterior 9,52 mm (3/8"), set de 5'),
    ("A658", 1385, 'Ficha técnica rodamiento guía para fresas A658 — Ø interior 4,76 mm (3/16"), Ø exterior 19,05 mm (3/4"), set de 3'),
]


def main():
    url, db = os.environ["ODOO_URL"], os.environ["ODOO_DB"]
    user, key = os.environ["ODOO_USERNAME"], os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/common", allow_none=True).authenticate(db, user, key, {})
    models = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/object", allow_none=True)

    def call(model, method, *args, **kw):
        return models.execute_kw(db, uid, key, model, method, list(args), kw)

    backup = []
    for ref, tmpl_id, alt in PRODUCTOS:
        marca = f"ficha-tecnica-{ref.lower()}"
        rec = call("product.template", "read", [tmpl_id], fields=["website_description"])[0]
        desc = rec["website_description"] or ""
        if marca in desc:
            print(f"{ref}: ya tiene ficha, salto")
            continue

        png = os.path.join(PNG_DIR, f"ficha-{ref}.png")
        with open(png, "rb") as f:
            datas = base64.b64encode(f.read()).decode()
        nombre_att = f"ficha-{ref}.png"
        previos = call("ir.attachment", "search", [["name", "=", nombre_att], ["public", "=", True]])
        if previos:
            att_id = previos[0]
            call("ir.attachment", "write", [att_id], {"datas": datas})
        else:
            att_id = call("ir.attachment", "create", {
                "name": nombre_att, "datas": datas, "mimetype": "image/png",
                "public": True, "res_model": "product.template", "res_id": tmpl_id,
            })

        bloque = (
            f'<section class="pt-4 pb-4 {marca}" style="text-align:center">\n'
            f'  <h3 style="margin-bottom:1rem">Ficha técnica</h3>\n'
            f'  <img src="/web/image/{att_id}-{marca}" alt="{alt}" '
            f'style="max-width:100%;height:auto" loading="lazy"/>\n'
            f"</section>\n"
        )

        # encima de la sección CTA de WhatsApp si existe; si no, antes del cierre del div raíz
        pos_cta = desc.rfind("<section")
        if pos_cta >= 0 and "WhatsApp" in desc[pos_cta:]:
            nueva = desc[:pos_cta] + bloque + "  " + desc[pos_cta:]
        elif desc.rstrip().endswith("</div>"):
            d = desc.rstrip()
            nueva = d[: d.rfind("</div>")] + bloque + "</div>"
        else:
            nueva = desc + bloque

        backup.append({"id": tmpl_id, "ref": ref, "website_description": desc})
        call("product.template", "write", [tmpl_id], {"website_description": nueva})
        print(f"{ref}: attachment {att_id}, descripción {len(desc)} -> {len(nueva)} chars")

    if backup:
        os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
        existente = json.load(open(BACKUP)) if os.path.exists(BACKUP) else []
        json.dump(existente + backup, open(BACKUP, "w"), ensure_ascii=False, indent=1)
        print(f"backup -> {BACKUP}")


if __name__ == "__main__":
    main()
