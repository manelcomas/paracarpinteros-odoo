#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P3 + P6 de estrategia-seo.md.

P3 — cluster aceites (intención de compra, pos 5–11):
  - Meta title/description con "Costa Rica" en las fichas 1086 (linaza 1 L),
    6444 (tung 1 L) y 2711 (linaza galón).
  - Cruce de productos alternativos: linaza ↔ tung (hoy las fichas no se
    enlazan entre sí; el carrusel "alternativos" de Odoo hace el interlinking).

P6 — blog que ya rankea (pos 5–8):
  - Meta title/description de los 6 posts con tráfico: 13 linaza, 22 cedro
    amargo, 25 teca, 32 atomstack x20, 51 cura de madera, 18 laurel.
  - Bloque "Relacionado en la tienda" al final de cada post (enlaces a fichas
    y categorías; marcador pc-relacionados = idempotente; backup en _backups/).

GOTCHA: meta y content son campos traducibles; la web renderiza es_ES.
Se escribe con context lang es_ES Y sin contexto (valor fuente) para coherencia.

Uso:
  python3 scripts/aplicar_p3_p6_seo.py            # dry-run
  python3 scripts/aplicar_p3_p6_seo.py --apply
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
MARCA = "pc-relacionados"

FICHAS = {
    1086: {"website_meta_title": "Aceite de Linaza para Madera Costa Rica | 1 Litro",
           "website_meta_description": ("Comprá aceite de linaza doble cocido para madera "
                                        "(1 L) en Costa Rica. Protege y realza el veteado. "
                                        "Stock real y envío a todo el país.")},
    6444: {"website_meta_title": "Aceite de Tung para Madera Costa Rica | 1 Litro",
           "website_meta_description": ("Aceite de tung puro para madera (1 L): acabado "
                                        "natural resistente al agua, ideal para tablas y "
                                        "muebles. Stock en Costa Rica, envío a todo el país.")},
    2711: {"website_meta_title": "Aceite de Linaza Doble Cocido 1 Galón Costa Rica",
           "website_meta_description": ("Aceite de linaza doble cocido para madera en galón: "
                                        "rinde para proyectos grandes y producción. Stock "
                                        "real en Costa Rica y envío a todo el país.")},
}
# cruces de alternativos que faltan: ficha → ids a añadir
ALTERNATIVOS = {1086: [6444], 6444: [1086, 2711], 2711: [6444]}

POSTS = {
    13: {"website_meta_title": "Aceite de Linaza para Madera: Cómo Aplicarlo Bien",
         "website_meta_description": ("Cómo aplicar aceite de linaza (crudo vs doble cocido) "
                                      "en madera: preparación, tiempos de secado y errores a "
                                      "evitar. Guía de carpintero."),
         "bloque": "aceites"},
    22: {"website_meta_title": "Cedro Amargo en Costa Rica: Propiedades y Usos en Madera",
         "website_meta_description": ("Todo sobre el cedro amargo de Costa Rica: propiedades "
                                      "de la madera, usos en carpintería y construcción, y "
                                      "para qué sirve su semilla."),
         "bloque": "aceites"},
    25: {"website_meta_title": "Madera de Teca en Costa Rica: Características y Usos",
         "website_meta_description": ("La madera de teca en Costa Rica: durabilidad, usos en "
                                      "muebles y exteriores, y cómo trabajarla y protegerla "
                                      "en el taller."),
         "bloque": "aceites"},
    32: {"website_meta_title": "Atomstack X20 Pro: Análisis del Grabador Láser de 20 W",
         "website_meta_description": ("Análisis del Atomstack X20 Pro: potencia óptica real, "
                                      "materiales que corta y graba, y para quién tiene "
                                      "sentido en Costa Rica."),
         "bloque": "laser"},
    51: {"website_meta_title": "Cómo se Cura la Madera: Métodos y Productos | Guía",
         "website_meta_description": ("Guía para curar madera: secado, productos protectores "
                                      "y errores comunes. Con qué se cura la madera según el "
                                      "uso final."),
         "bloque": "aceites"},
    18: {"website_meta_title": "Madera de Laurel en Costa Rica: Usos y Ventajas",
         "website_meta_description": ("La madera de laurel costarricense: usos en muebles y "
                                      "construcción, ventajas, inconvenientes y consejos "
                                      "para trabajarla."),
         "bloque": "aceites"},
}


def url_canonica(path_o_id, cache={}):
    if path_o_id in cache:
        return cache[path_o_id]
    req = urllib.request.Request(f"{BASE}{path_o_id}", method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0 (p3p6)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        cache[path_o_id] = r.url.replace(BASE, "")
    return cache[path_o_id]


def bloque_html(tipo):
    cat_aceites = url_canonica("/shop/category/1891")
    cat_laser = url_canonica("/shop/category/2016")
    enlaces = {
        "aceites": [
            ("/shop/aceite-de-linaza-doble-cocido-para-madera-1-litro-1086",
             "Aceite de linaza doble cocido (1 litro)"),
            ("/shop/aceite-de-tung-para-madera-1-litro-6444",
             "Aceite de tung puro (1 litro)"),
            ("/shop/aceite-de-linaza-doble-cocido-para-madera-1-galon-2711",
             "Aceite de linaza doble cocido (1 galón)"),
            (cat_aceites, "Ver todos los aceites para madera"),
            ("/blog/explora-la-carpinteria-y-ebanisteria-con-paracarpinteroscom-3/"
             "aceite-de-tung-vs-aceite-de-linaza-cual-le-conviene-a-tu-madera-57",
             "Guía: aceite de tung vs aceite de linaza"),
        ],
        "laser": [
            (cat_laser, "Grabadoras láser y repuestos en stock"),
            ("/disenador-laser", "Diseñador láser gratuito de Paracarpinteros"),
        ],
    }[tipo]
    lis = "\n".join(f'<li style="margin:6px 0"><a href="{h}">{t}</a></li>' for h, t in enlaces)
    return (f'\n<section class="{MARCA}" style="margin-top:32px;padding:20px 24px;'
            f'border:2px solid #D4A017;border-radius:12px;background:#fdf8ec">'
            f'<h3 style="margin-top:0">🛒 Relacionado en la tienda</h3>'
            f'<ul style="margin-bottom:0">{lis}</ul></section>')


# Cruce server-rendered en website_description (el carrusel "alternativos" de
# Odoo es un snippet dinámico que en este theme renderiza vacío, no sirve para SEO)
CRUCES_FICHAS = {
    1086: [("/shop/aceite-de-tung-para-madera-1-litro-6444",
            "¿Buscás un acabado más resistente al agua y la intemperie? Mirá el aceite de tung puro (1 L)"),
           ("/shop/aceite-de-linaza-doble-cocido-para-madera-1-galon-2711",
            "Para proyectos grandes: aceite de linaza en galón")],
    6444: [("/shop/aceite-de-linaza-doble-cocido-para-madera-1-litro-1086",
            "¿Preferís el clásico? Aceite de linaza doble cocido (1 L)"),
           ("/shop/aceite-de-linaza-doble-cocido-para-madera-1-galon-2711",
            "Aceite de linaza en galón para producción")],
    2711: [("/shop/aceite-de-tung-para-madera-1-litro-6444",
            "Para exteriores y tablas de cocina: aceite de tung puro (1 L)"),
           ("/shop/aceite-de-linaza-doble-cocido-para-madera-1-litro-1086",
            "Presentación de 1 litro para empezar")],
}


def cruce_html(fid):
    cat = url_canonica("/shop/category/1891")
    lis = "\n".join(f'<li style="margin:6px 0"><a href="{h}">{t}</a></li>'
                    for h, t in CRUCES_FICHAS[fid])
    lis += f'\n<li style="margin:6px 0"><a href="{cat}">Ver todos los aceites y acabados</a></li>'
    return (f'\n<section class="pc-cruce" style="margin-top:24px;padding:16px 20px;'
            f'border-left:4px solid #D4A017;background:#fdf8ec">'
            f'<h3 style="margin-top:0;font-size:1.1rem">También en aceites para madera</h3>'
            f'<ul style="margin-bottom:0">{lis}</ul></section>')


def main():
    for d in list(FICHAS.values()) + list(POSTS.values()):
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

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backups")
    os.makedirs(bak_dir, exist_ok=True)

    print("== P3 fichas aceites ==")
    for fid, vals in FICHAS.items():
        actual = call("product.template", "read", [fid],
                      fields=["name", "website_meta_title"], context={"lang": "es_ES"})[0]
        print(f"→ {fid} {actual['name'][:45]}\n"
              f"    mt: '{actual['website_meta_title']}' → '{vals['website_meta_title']}'")
        if APPLY:
            escribir("product.template", fid, vals)
            print("    ✅")
    for fid, nuevos in ALTERNATIVOS.items():
        print(f"→ {fid} alternativos += {nuevos}")
        if APPLY:
            call("product.template", "write", [fid],
                 {"alternative_product_ids": [(4, n) for n in nuevos]})
            print("    ✅")

    print("\n== P3 cruce en descripciones de fichas ==")
    for fid in CRUCES_FICHAS:
        desc = call("product.template", "read", [fid],
                    fields=["website_description"], context={"lang": "es_ES"})[0]["website_description"] or ""
        if "pc-cruce" in desc:
            print(f"→ {fid}: ya tiene pc-cruce, no se duplica")
            continue
        print(f"→ {fid}: se añade bloque cruce ({len(CRUCES_FICHAS[fid])} enlaces + categoría)")
        if APPLY:
            with open(os.path.join(bak_dir, f"backup_ficha{fid}_wdesc_{ts}.html"), "w") as f:
                f.write(desc)
            call("product.template", "write", [fid],
                 {"website_description": desc + cruce_html(fid)},
                 context={"lang": "es_ES"})
            print("    ✅")

    print("\n== P6 posts blog ==")
    for pid, vals in POSTS.items():
        p = call("blog.post", "read", [pid],
                 fields=["name", "website_meta_title", "content"],
                 context={"lang": "es_ES"})[0]
        print(f"→ post {pid} {p['name'][:50]}\n"
              f"    mt: '{p['website_meta_title'] or '(vacío)'}' → '{vals['website_meta_title']}'")
        metas = {k: v for k, v in vals.items() if k.startswith("website_meta")}
        contenido = p["content"] or ""
        con_bloque = MARCA not in contenido
        print(f"    bloque '{vals['bloque']}': {'se añade' if con_bloque else 'ya existe, no se duplica'}")
        if APPLY:
            escribir("blog.post", pid, metas)
            if con_bloque:
                with open(os.path.join(bak_dir, f"backup_blogpost{pid}_content_{ts}.html"), "w") as f:
                    f.write(contenido)
                call("blog.post", "write", [pid],
                     {"content": contenido + bloque_html(vals["bloque"])},
                     context={"lang": "es_ES"})
            print("    ✅")

    if not APPLY:
        print("\nDRY-RUN: nada escrito. Repetir con --apply.")


if __name__ == "__main__":
    main()
