#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P4 de estrategia-seo.md: fusiona el árbol de fresas G/P/V (2035–2038) dentro de
"Fresas y Router" (1884) para acabar con la canibalización de "fresas para
router costa rica".

Pasos (en este orden):
  1. Añade a las categorías destino los productos que solo estaban en el árbol B
     (2036→1959, 2037→1960, 2038→1957).
  2. Reapunta los menús del website "Fresas P/G/V" (ids 99/100/101) a las
     categorías destino con nombre de carpintero.
  3. Crea 301 (website.rewrite) de las URLs del árbol B — slug del menú viejo y
     canónica actual — hacia la categoría destino.
  4. Borra las categorías 2036/2037/2038/2035 (hijas primero).

Backup completo (categorías + menús + lista de productos) en scripts/_backups/
antes de tocar nada. Revertible recreando las categorías del JSON.

Uso:
  python3 scripts/aplicar_p4_fusion_fresas.py            # dry-run
  python3 scripts/aplicar_p4_fusion_fresas.py --apply
"""
import datetime
import json
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
WEBSITE_ID = 3
APPLY = "--apply" in sys.argv

MAPA = {2036: 1959, 2037: 1960, 2038: 1957, 2035: 1884}  # B → A
# menú id → (categoría destino, nombre nuevo)
MENUS = {
    99: (1960, 'Fresas 1/4"'),   # era "Fresas P" → cat 2037
    100: (1959, 'Fresas 1/2"'),  # era "Fresas G" → cat 2036
    101: (1957, "Fresas CNC"),   # era "Fresas V" → cat 2038
}
# slugs viejos conocidos (del menú, pre-renombrado de hoy) que también hay que redirigir
SLUGS_VIEJOS = {
    2036: "/shop/category/fresas-fresas-g-vastago-12-2036",
    2037: "/shop/category/fresas-fresas-p-vastago-14-2037",
    2038: "/shop/category/fresas-fresas-v-especiales-cnc-2038",
}


def url_canonica(path, cache={}):
    if path in cache:
        return cache[path]
    req = urllib.request.Request(f"{BASE}{path}", method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0 (p4)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        cache[path] = r.url.replace(BASE, "").split("?")[0]
    return cache[path]


def main():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid:
        sys.exit("Autenticación Odoo fallida.")
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    def call(model, method, *a, **k):
        return models.execute_kw(DB, uid, KEY, model, method, list(a), k)

    cats = {c["id"]: c for c in call(
        "product.public.category", "read", list(MAPA) + list(set(MAPA.values())),
        fields=["name", "parent_id", "product_tmpl_ids",
                "website_meta_title", "website_meta_description"])}
    menus = {m["id"]: m for m in call("website.menu", "read", list(MENUS),
                                      fields=["name", "url", "website_id"])}

    # URLs canónicas ANTES de borrar nada
    canon_b = {b: url_canonica(f"/shop/category/{b}") for b in MAPA if b != 2035}
    canon_b[2035] = url_canonica("/shop/category/2035")
    canon_a = {a: url_canonica(f"/shop/category/{a}") for a in set(MAPA.values())}

    print("== 1. Productos a añadir al árbol destino ==")
    movimientos = {}
    for b, a in MAPA.items():
        faltan = sorted(set(cats[b]["product_tmpl_ids"]) - set(cats[a]["product_tmpl_ids"]))
        movimientos[b] = faltan
        print(f"  {b} '{cats[b]['name']}' → {a} '{cats[a]['name']}': +{len(faltan)} productos")

    print("\n== 2. Menús del website ==")
    for mid, (a, nombre) in MENUS.items():
        destino = f"{canon_a[a]}?order=website_sequence%20asc"
        print(f"  menú {mid} '{menus[mid]['name']}' → '{nombre}' → {destino}")

    print("\n== 3. Redirects 301 ==")
    redirects = []
    for b, a in MAPA.items():
        for origen in {canon_b[b], SLUGS_VIEJOS.get(b)} - {None}:
            if origen != canon_a[a]:
                redirects.append((origen, canon_a[a], b))
    for origen, destino, b in redirects:
        print(f"  {origen} → {destino}")

    print("\n== 4. Borrado de categorías B ==")
    for b in (2036, 2037, 2038, 2035):
        print(f"  unlink {b} '{cats[b]['name']}'")

    if not APPLY:
        print("\nDRY-RUN: nada escrito. Repetir con --apply.")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backups")
    os.makedirs(bak_dir, exist_ok=True)
    bak = os.path.join(bak_dir, f"backup_p4_fusion_fresas_{ts}.json")
    with open(bak, "w") as f:
        json.dump({"categorias": cats, "menus": menus, "canonicas_b": canon_b}, f,
                  ensure_ascii=False, indent=1, default=str)
    print(f"\nBackup: {bak}")

    for b, a in MAPA.items():
        if movimientos[b]:
            call("product.template", "write", movimientos[b],
                 {"public_categ_ids": [(4, a)]})
    print("✅ productos añadidos")

    for mid, (a, nombre) in MENUS.items():
        call("website.menu", "write", [mid],
             {"name": nombre, "url": f"{canon_a[a]}?order=website_sequence%20asc"})
    print("✅ menús reapuntados")

    existentes = {r["url_from"] for r in call("website.rewrite", "search_read", [],
                                              fields=["url_from"])}
    for origen, destino, b in redirects:
        if origen not in existentes:
            call("website.rewrite", "create", [{
                "name": f"SEO P4 fusión fresas {b}",
                "website_id": WEBSITE_ID,
                "url_from": origen,
                "url_to": destino,
                "redirect_type": "301",
            }])
    print("✅ redirects creados")

    call("product.public.category", "unlink", [2036, 2037, 2038])
    call("product.public.category", "unlink", [2035])
    print("✅ categorías B eliminadas")


if __name__ == "__main__":
    main()
