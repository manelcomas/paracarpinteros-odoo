#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aplica la propuesta de categorias-seo.md sobre product.public.category vía XML-RPC.

Lee las tablas del markdown (filas `| <id> | actual | nombre | meta title | meta
description |`) y escribe name, website_meta_title y website_meta_description de
ESOS ids únicamente. No toca jerarquía (parent_id), productos ni nada más.

Uso:
  python3 scripts/aplicar_categorias_seo.py            # dry-run (muestra diff)
  python3 scripts/aplicar_categorias_seo.py --apply    # escribe en Odoo
"""
import os
import re
import sys
import xmlrpc.client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_project_env
load_project_env()

URL = os.environ.get("ODOO_URL", "https://paracarpinteros.odoo.com")
DB = os.environ.get("ODOO_DB", "paracarpinteros")
USER = os.environ.get("ODOO_USER") or os.environ.get("ODOO_USERNAME")
KEY = os.environ.get("ODOO_API_KEY") or os.environ.get("ODOO_KEY")
APPLY = "--apply" in sys.argv

MD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "categorias-seo.md")


def parsear_propuesta(ruta):
    filas = []
    for linea in open(ruta, encoding="utf-8"):
        if not re.match(r"\| \d{4} \|", linea):
            continue
        partes = [c.strip().replace("\\|", "|") for c in linea.split(" | ")]
        cid = int(partes[0].strip("| "))
        actual, nombre, mt, md = partes[1], partes[2], partes[3], partes[4].rstrip(" |")
        filas.append({"id": cid, "actual": actual, "name": nombre,
                      "website_meta_title": mt, "website_meta_description": md})
    return filas


def main():
    propuesta = parsear_propuesta(MD)
    if not propuesta:
        sys.exit("No se encontraron filas en categorias-seo.md")
    print(f"{len(propuesta)} categorías en la propuesta")

    if not KEY or not USER:
        sys.exit("Faltan credenciales Odoo en el .env raíz.")
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid:
        sys.exit("Autenticación Odoo fallida.")
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    def call(model, method, *a, **k):
        return models.execute_kw(DB, uid, KEY, model, method, list(a), k)

    actuales = {c["id"]: c for c in call(
        "product.public.category", "read", [f["id"] for f in propuesta],
        fields=["name", "website_meta_title", "website_meta_description"])}

    cambios = 0
    for f in propuesta:
        viejo = actuales.get(f["id"])
        if not viejo:
            print(f"⚠️ id {f['id']} no existe en Odoo, se omite")
            continue
        if viejo["name"] != f["actual"]:
            print(f"⚠️ id {f['id']}: nombre actual '{viejo['name']}' ≠ '{f['actual']}' "
                  f"de la propuesta — se omite por seguridad (¿cambió desde la lectura?)")
            continue
        vals = {}
        for campo in ("name", "website_meta_title", "website_meta_description"):
            if (viejo.get(campo) or "") != f[campo]:
                vals[campo] = f[campo]
        if not vals:
            print(f"= id {f['id']} ({viejo['name']}): ya está, sin cambios")
            continue
        cambios += 1
        print(f"→ id {f['id']}:")
        for campo, nuevo in vals.items():
            print(f"    {campo}: '{(viejo.get(campo) or '')[:70]}' → '{nuevo[:70]}'")
        if APPLY:
            call("product.public.category", "write", [f["id"]], vals)
            print("    ✅ aplicado")

    if not APPLY:
        print(f"\nDRY-RUN: {cambios} categorías cambiarían. Repetir con --apply.")
    else:
        print(f"\n✅ {cambios} categorías actualizadas.")


if __name__ == "__main__":
    main()
