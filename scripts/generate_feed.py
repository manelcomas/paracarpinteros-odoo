#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera los feeds de productos desde Odoo por XML-RPC (solo LEE de Odoo):
  - feed-google.xml   → Google Merchant Center (RSS 2.0 / Google Shopping)
  - feed-facebook.xml → Facebook/Instagram Catalog (Commerce Manager)

Ambos salen de la MISMA consulta. La diferencia: Facebook quiere `brand` (Google
no lo exige) y prefiere los elementos RSS estándar <title>/<description>/<link>
en vez de los <g:...> de Google. Por eso se generan dos archivos.

Productos: product.template con website_published=True, is_published=True y
sale_ok=True. Los no vendibles (sale_ok=False) dan 404 en /shop aunque estén
publicados, así que Google los desaprueba ("Product page unavailable"): por eso
quedan fuera del feed (caso A898/tmpl 1915, 2026-06-15).
Se omiten además (con aviso) los que no tienen default_code (g:id) o no tienen imagen.

Uso:
  python3 scripts/generate_feed.py                      # ./feed-google.xml + ./feed-facebook.xml
  python3 scripts/generate_feed.py --out /var/www/html/feed-google.xml
  # el feed-facebook.xml se escribe junto al --out (mismo directorio), salvo --fb-out

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
MARCA = "Paracarpinteros"  # Facebook exige brand; el catálogo es mayormente sin marca propia

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


def item_google(r):
    return (
        "<item>"
        f"<g:id>{escape(r['ref'])}</g:id>"
        f"<g:title>{escape(r['titulo'])}</g:title>"
        f"<g:description>{escape(r['desc'])}</g:description>"
        f"<g:link>{escape(r['link'])}</g:link>"
        f"<g:image_link>{escape(r['imagen'])}</g:image_link>"
        f"<g:price>{r['precio']}</g:price>"
        f"<g:availability>{r['dispo']}</g:availability>"
        "<g:condition>new</g:condition>"
        "<g:identifier_exists>no</g:identifier_exists>"
        "</item>"
    )


def item_facebook(r):
    # Facebook: <title>/<description>/<link> estándar + g:brand obligatorio
    return (
        "<item>"
        f"<g:id>{escape(r['ref'])}</g:id>"
        f"<title>{escape(r['titulo'])}</title>"
        f"<description>{escape(r['desc'])}</description>"
        f"<link>{escape(r['link'])}</link>"
        f"<g:image_link>{escape(r['imagen'])}</g:image_link>"
        f"<g:price>{r['precio']}</g:price>"
        f"<g:availability>{r['dispo']}</g:availability>"
        "<g:condition>new</g:condition>"
        f"<g:brand>{escape(MARCA)}</g:brand>"
        "</item>"
    )


def envolver(items_xml, fecha):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">\n'
        "<channel>\n"
        "<title>Paracarpinteros</title>\n"
        f"<link>{BASE}</link>\n"
        f"<description>Herramientas y accesorios de carpintería en Costa Rica. Generado {fecha}</description>\n"
        + "\n".join(items_xml)
        + "\n</channel>\n</rss>\n"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="feed-google.xml", help="ruta del XML de Google")
    ap.add_argument("--fb-out", default=None,
                    help="ruta del XML de Facebook (default: feed-facebook.xml junto a --out)")
    args = ap.parse_args()
    fb_out = args.fb_out or os.path.join(os.path.dirname(args.out) or ".", "feed-facebook.xml")

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

    no_vendibles = call("product.template", "search_count",
                        [["website_published", "=", True], ["is_published", "=", True],
                         ["sale_ok", "=", False]])
    if no_vendibles:
        print(f"⚠️ {no_vendibles} publicados omitidos por sale_ok=False (404 en /shop)",
              file=sys.stderr)

    productos = []
    for i in range(0, len(ids), 500):
        productos += call("product.template", "read", ids[i:i + 500], fields=campos)

    sin_imagen = set(call("product.template", "search", dominio + [["image_1920", "=", False]]))

    rows = []
    omitidos_ref, omitidos_img = [], []
    for p in productos:
        ref = p.get("default_code")
        if not ref:
            omitidos_ref.append(p["name"])
            continue
        if p["id"] in sin_imagen:
            omitidos_img.append(f"{ref} {p['name']}")
            continue
        rows.append({
            "ref": ref,
            "titulo": texto_plano(p["name"])[:TITULO_MAX],
            "desc": texto_plano(p.get("description_sale") or p.get("website_meta_description") or p["name"])[:DESC_MAX],
            "link": BASE + p["website_url"],
            "imagen": f"{BASE}/web/image/product.template/{p['id']}/image_1024",
            "precio": f"{p['list_price']:.2f} CRC",
            "dispo": "in stock" if (p.get("qty_available") or 0) > 0 else "out of stock",
        })

    fecha = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(envolver([item_google(r) for r in rows], fecha))
    with open(fb_out, "w", encoding="utf-8") as f:
        f.write(envolver([item_facebook(r) for r in rows], fecha))
    print(f"✅ {len(rows)} items → {args.out}", file=sys.stderr)
    print(f"✅ {len(rows)} items → {fb_out} (Facebook)", file=sys.stderr)
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
