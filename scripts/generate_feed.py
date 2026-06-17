#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera el feed de Google Merchant Center (RSS 2.0 / Google Shopping) desde Odoo
por XML-RPC. Solo LEE de Odoo; escribe únicamente el XML local indicado en --out.

Productos: product.template con website_published=True, is_published=True y
sale_ok=True. Los no vendibles (sale_ok=False) dan 404 en /shop aunque estén
publicados, así que Google los desaprueba ("Product page unavailable"): por eso
quedan fuera del feed (caso A898/tmpl 1915, 2026-06-15).
Se omiten además (con aviso) los que no tienen default_code (g:id) o no tienen imagen.

Uso:
  python3 scripts/generate_feed.py                      # escribe ./feed-google.xml
  python3 scripts/generate_feed.py --out /var/www/html/feed-google.xml

En el VPS corre vía cron (/etc/cron.d/feed-google) cargando las credenciales
del .env del bridge (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY).
"""
import argparse
import os
import re
import sys
import time
import xmlrpc.client
from xml.sax.saxutils import escape

# Carga el .env baúl del repo si está disponible (en el VPS las vars vienen del cron)
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _env import load_project_env
    load_project_env()
except Exception:
    pass

BASE = "https://www.paracarpinteros.com"
TITULO_MAX = 150
DESC_MAX = 5000  # límite de Google

URL = os.environ.get("ODOO_URL", "https://paracarpinteros.odoo.com")
DB = os.environ.get("ODOO_DB", "paracarpinteros")
USER = os.environ.get("ODOO_USER") or os.environ.get("ODOO_USERNAME")
KEY = os.environ.get("ODOO_API_KEY") or os.environ.get("ODOO_KEY")


def texto_plano(html_o_texto):
    """Quita etiquetas HTML y colapsa espacios."""
    t = re.sub(r"<[^>]+>", " ", html_o_texto or "")
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">")
          .replace("&#39;", "'").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", t).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="feed-google.xml", help="ruta del XML de salida")
    args = ap.parse_args()

    if not KEY or not USER:
        sys.exit("Faltan credenciales Odoo (ODOO_USERNAME/ODOO_USER + ODOO_API_KEY).")

    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid:
        sys.exit("Autenticación Odoo fallida.")
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    def call(model, method, *a, **k):
        return models.execute_kw(DB, uid, KEY, model, method, list(a), k)

    dominio = [["website_published", "=", True], ["is_published", "=", True],
               ["sale_ok", "=", True]]
    campos = ["name", "default_code", "list_price", "qty_available",
              "website_url", "description_sale", "website_meta_description"]
    ids = call("product.template", "search", dominio, order="id")
    print(f"{len(ids)} productos publicados y vendibles", file=sys.stderr)

    # Publicados pero NO vendibles (sale_ok=False): dan 404 en /shop → excluidos.
    no_vendibles = call("product.template", "search_count",
                        [["website_published", "=", True], ["is_published", "=", True],
                         ["sale_ok", "=", False]])
    if no_vendibles:
        print(f"⚠️ {no_vendibles} publicados omitidos por sale_ok=False (404 en /shop)",
              file=sys.stderr)

    productos = []
    for i in range(0, len(ids), 500):
        productos += call("product.template", "read", ids[i:i + 500], fields=campos)

    # Sin imagen propia (image_1920 vacío) → Google los desaprueba por placeholder
    sin_imagen = set(call("product.template", "search", dominio + [["image_1920", "=", False]]))

    items = []
    omitidos_ref, omitidos_img = [], []
    for p in productos:
        ref = p.get("default_code")
        if not ref:
            omitidos_ref.append(p["name"])
            continue
        if p["id"] in sin_imagen:
            omitidos_img.append(f"{ref} {p['name']}")
            continue
        titulo = texto_plano(p["name"])[:TITULO_MAX]
        desc = texto_plano(p.get("description_sale") or p.get("website_meta_description") or p["name"])[:DESC_MAX]
        link = BASE + p["website_url"]
        imagen = f"{BASE}/web/image/product.template/{p['id']}/image_1024"
        precio = f"{p['list_price']:.2f} CRC"
        dispo = "in stock" if (p.get("qty_available") or 0) > 0 else "out of stock"
        items.append(
            "<item>"
            f"<g:id>{escape(ref)}</g:id>"
            f"<g:title>{escape(titulo)}</g:title>"
            f"<g:description>{escape(desc)}</g:description>"
            f"<g:link>{escape(link)}</g:link>"
            f"<g:image_link>{escape(imagen)}</g:image_link>"
            f"<g:price>{precio}</g:price>"
            f"<g:availability>{dispo}</g:availability>"
            "<g:condition>new</g:condition>"
            "<g:identifier_exists>no</g:identifier_exists>"
            "</item>"
        )

    fecha = time.strftime("%Y-%m-%d %H:%M:%S")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">\n'
        "<channel>\n"
        "<title>Paracarpinteros</title>\n"
        f"<link>{BASE}</link>\n"
        f"<description>Herramientas y accesorios de carpintería en Costa Rica. Generado {fecha}</description>\n"
        + "\n".join(items)
        + "\n</channel>\n</rss>\n"
    )

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"✅ {len(items)} items → {args.out}", file=sys.stderr)
    if omitidos_ref:
        print(f"⚠️ {len(omitidos_ref)} omitidos sin default_code:", file=sys.stderr)
        for n in omitidos_ref:
            print(f"   - {n}", file=sys.stderr)
    if omitidos_img:
        print(f"⚠️ {len(omitidos_img)} omitidos sin imagen:", file=sys.stderr)
        for n in omitidos_img:
            print(f"   - {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
