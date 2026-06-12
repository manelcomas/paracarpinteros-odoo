#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P3 punto 4 de estrategia-seo.md: réplica del patrón aceites en el cluster
"perfiles de aluminio" (526+416+159 impresiones en pos 6–8, intención de compra).

- Meta title/description con "Costa Rica" en las 8 fichas de tramos (2020/3060/
  4040 × 100/250 cm) y en las 3 categorías de perfil (1976/1977/1978, sin metas).
- Bloque pc-cruce server-rendered en cada ficha de tramo: el otro largo de la
  misma serie, los accesorios compatibles (tuerca de riel, uniones) y las
  categorías. Mismo patrón e idempotencia que aplicar_p3_p6_seo.py.

Uso:
  python3 scripts/aplicar_p3b_perfiles.py            # dry-run
  python3 scripts/aplicar_p3b_perfiles.py --apply
"""
import datetime
import os
import sys
import urllib.request
import xmlrpc.client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_project_env
load_project_env()

URL = os.environ.get("ODOO_URL", "https://paracarpinteros.odoo.com")
DB = os.environ.get("ODOO_DB", "paracarpinteros")
USER = os.environ.get("ODOO_USER") or os.environ.get("ODOO_USERNAME")
KEY = os.environ.get("ODOO_API_KEY") or os.environ.get("ODOO_KEY")
BASE = "https://www.paracarpinteros.com"
APPLY = "--apply" in sys.argv


def md_perfil(serie, largo):
    return (f"Perfil de aluminio {serie} tipo T (ranura T), tramo de {largo}. Para CNC, "
            f"mesas, jigs y estructuras. Stock y envío a toda Costa Rica.")


CATEGORIAS = {
    1976: {"website_meta_title": "Perfiles de Aluminio Costa Rica | Tipo T (Ranura T)",
           "website_meta_description": ("Perfiles de aluminio tipo T para CNC, mesas y "
                                        "estructuras: series 2020, 3060 y 4040 en tramos de "
                                        "100 y 250 cm. Envío a toda Costa Rica.")},
    1977: {"website_meta_title": "Perfil de Aluminio 2020 Costa Rica | Tipo T",
           "website_meta_description": ("Perfil de aluminio 2020 tipo T en tramos de 100 y "
                                        "250 cm, con tuercas de riel, uniones y articulaciones "
                                        "compatibles. Envío a toda Costa Rica.")},
    1978: {"website_meta_title": "Perfil de Aluminio 4040 Costa Rica | Tipo T",
           "website_meta_description": ("Perfil de aluminio 4040 tipo T en tramos de 100 y "
                                        "250 cm, con tuercas de riel, uniones y articulaciones "
                                        "compatibles. Envío a toda Costa Rica.")},
}

FICHAS = {
    1868: {"website_meta_title": "Perfil de Aluminio 2020 de 100 cm Costa Rica | Tipo T",
           "website_meta_description": md_perfil("2020", "100 cm")},
    6446: {"website_meta_title": "Perfil de Aluminio 2020 de 250 cm Costa Rica | Tipo T",
           "website_meta_description": md_perfil("2020", "250 cm")},
    2675: {"website_meta_title": "Perfil de Aluminio 3060 de 100 cm Costa Rica | Tipo T",
           "website_meta_description": md_perfil("3060", "100 cm")},
    2662: {"website_meta_title": "Perfil de Aluminio 3060 de 250 cm Costa Rica | Tipo T",
           "website_meta_description": md_perfil("3060", "250 cm")},
    1557: {"website_meta_title": "Perfil de Aluminio 4040 de 100 cm Costa Rica | Tipo T",
           "website_meta_description": md_perfil("4040", "100 cm")},
    6865: {"website_meta_title": "Perfil de Aluminio 4040 de 100 cm Plata Costa Rica",
           "website_meta_description": md_perfil("4040", "100 cm (color plata)")},
    6589: {"website_meta_title": "Perfil de Aluminio 4040 de 250 cm Negro Costa Rica",
           "website_meta_description": md_perfil("4040", "250 cm (color negro)")},
    6903: {"website_meta_title": "Perfil de Aluminio 4040 de 250 cm Plata Costa Rica",
           "website_meta_description": md_perfil("4040", "250 cm (color plata)")},
}

# ficha → [(producto/categoría enlazado, texto)] — los hrefs se resuelven en runtime
CRUCES = {
    1868: [(6446, "El mismo perfil 2020 en tramo de 250 cm"),
           (1872, "Tuercas de riel para perfil 2020"),
           (1869, "Uniones y conectores para perfil 2020"),
           ("cat:1977", "Ver todo el perfil de aluminio 2020")],
    6446: [(1868, "El mismo perfil 2020 en tramo de 100 cm"),
           (1872, "Tuercas de riel para perfil 2020"),
           (1869, "Uniones y conectores para perfil 2020"),
           ("cat:1977", "Ver todo el perfil de aluminio 2020")],
    2675: [(2662, "El mismo perfil 3060 en tramo de 250 cm"),
           ("cat:1976", "Ver todos los perfiles de aluminio")],
    2662: [(2675, "El mismo perfil 3060 en tramo de 100 cm"),
           ("cat:1976", "Ver todos los perfiles de aluminio")],
    1557: [(6589, "El mismo perfil 4040 en tramo de 250 cm"),
           (1569, "Tuercas de riel para perfil 4040"),
           (1558, "Uniones y conectores para perfil 4040"),
           ("cat:1978", "Ver todo el perfil de aluminio 4040")],
    6865: [(6903, "El mismo perfil 4040 plata en tramo de 250 cm"),
           (1569, "Tuercas de riel para perfil 4040"),
           ("cat:1978", "Ver todo el perfil de aluminio 4040")],
    6589: [(1557, "El mismo perfil 4040 en tramo de 100 cm"),
           (1569, "Tuercas de riel para perfil 4040"),
           (1558, "Uniones y conectores para perfil 4040"),
           ("cat:1978", "Ver todo el perfil de aluminio 4040")],
    6903: [(6865, "El mismo perfil 4040 plata en tramo de 100 cm"),
           (1569, "Tuercas de riel para perfil 4040"),
           ("cat:1978", "Ver todo el perfil de aluminio 4040")],
}


def url_canonica(path, cache={}):
    if path in cache:
        return cache[path]
    req = urllib.request.Request(f"{BASE}{path}", method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0 (p3b)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        cache[path] = r.url.replace(BASE, "").split("?")[0]
    return cache[path]


def main():
    for d in list(CATEGORIAS.values()) + list(FICHAS.values()):
        assert len(d["website_meta_title"]) <= 60, d["website_meta_title"]
        assert len(d["website_meta_description"]) <= 155, d["website_meta_description"]

    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid:
        sys.exit("Autenticación Odoo fallida.")
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    def call(model, method, *a, **k):
        return models.execute_kw(DB, uid, KEY, model, method, list(a), k)

    def escribir(model, rid, vals):
        call(model, "write", [rid], vals, context={"lang": "es_ES"})
        call(model, "write", [rid], vals)

    # URLs de productos referenciados en cruces
    ids_ref = sorted({x for v in CRUCES.values() for x, _ in v if isinstance(x, int)})
    urls_prod = {p["id"]: p["website_url"] for p in
                 call("product.template", "read", ids_ref, fields=["website_url"])}

    def href(ref):
        return url_canonica(f"/shop/category/{ref[4:]}") if isinstance(ref, str) else urls_prod[ref]

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backups")
    os.makedirs(bak_dir, exist_ok=True)

    print("== Categorías perfil ==")
    for cid, vals in CATEGORIAS.items():
        c = call("product.public.category", "read", [cid], fields=["name", "website_meta_title"])[0]
        print(f"→ {cid} {c['name']}: mt '{c['website_meta_title'] or '(vacío)'}' → '{vals['website_meta_title']}'")
        if APPLY:
            escribir("product.public.category", cid, vals)
            print("    ✅")

    print("\n== Fichas de tramos ==")
    for fid, vals in FICHAS.items():
        p = call("product.template", "read", [fid],
                 fields=["name", "website_meta_title", "website_description"],
                 context={"lang": "es_ES"})[0]
        desc = p["website_description"] or ""
        print(f"→ {fid} {p['name'][:45]}\n    mt → '{vals['website_meta_title']}'")
        cruce_pendiente = "pc-cruce" not in desc
        print(f"    cruce: {'se añade (' + str(len(CRUCES[fid])) + ' enlaces)' if cruce_pendiente else 'ya existe'}")
        if APPLY:
            escribir("product.template", fid, vals)
            if cruce_pendiente:
                with open(os.path.join(bak_dir, f"backup_ficha{fid}_wdesc_{ts}.html"), "w") as f:
                    f.write(desc)
                lis = "\n".join(f'<li style="margin:6px 0"><a href="{href(r)}">{t}</a></li>'
                                for r, t in CRUCES[fid])
                bloque = (f'\n<section class="pc-cruce" style="margin-top:24px;padding:16px 20px;'
                          f'border-left:4px solid #D4A017;background:#fdf8ec">'
                          f'<h3 style="margin-top:0;font-size:1.1rem">También en perfiles de aluminio</h3>'
                          f'<ul style="margin-bottom:0">{lis}</ul></section>')
                call("product.template", "write", [fid],
                     {"website_description": desc + bloque}, context={"lang": "es_ES"})
            print("    ✅")

    if not APPLY:
        print("\nDRY-RUN: nada escrito. Repetir con --apply.")


if __name__ == "__main__":
    main()
