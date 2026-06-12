#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1 de estrategia-seo.md: crea redirecciones 301 (website.rewrite) para las
categorías del árbol VIEJO que dan 404 pero siguen rankeando en Google.

Inventario: GSC 90 días, dimensión page, 2.977 páginas; 74 paths únicos con
404 verificado por HEAD (2026-06-11). Cada path viejo se mapea a la categoría
viva equivalente por regla de slug, y la URL destino se resuelve a la canónica
(siguiendo el redirect de /shop/category/<id>) para no crear cadenas de 301.

Uso:
  python3 scripts/aplicar_redirects_seo.py            # dry-run (muestra el mapa)
  python3 scripts/aplicar_redirects_seo.py --apply    # crea los redirects en Odoo
"""
import os
import re
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

# path 404 → (impresiones, clicks) — GSC 90d a 2026-06-11
PATHS_404 = {
    "/shop/category/maquinas-185": (8203, 183),
    "/shop/category/herrajes-y-tornilleria-193": (3180, 92),
    "/shop/category/fresas-y-accesorios-para-router-172": (2911, 22),
    "/shop/category/maquinas-208": (2889, 27),
    "/shop/category/fresas-y-accesorios-para-router-212": (1708, 46),
    "/shop/category/para-router-157": (1332, 45),
    "/shop/category/fresas-y-accesorios-para-router-177": (1105, 17),
    "/shop/category/herrajes-y-tornilleria-190": (928, 25),
    "/shop/category/herramientas-de-corte-y-afilado-154": (856, 26),
    "/shop/category/accesorios-161": (772, 17),
    "/shop/category/herramientas-de-corte-y-afilado-153": (708, 5),
    "/shop/category/medicion-y-sujecion-169": (414, 4),
    "/shop/category/herrajes-y-tornilleria-192": (389, 20),
    "/shop/category/herrajes-y-tornilleria-205": (388, 17),
    "/shop/category/fresas-y-accesorios-para-router-201": (381, 4),
    "/shop/category/herramientas-de-corte-y-afilado-151": (301, 3),
    "/shop/category/cerraduras-1583": (292, 5),
    "/shop/category/fresas-y-accesorios-para-router-1152": (288, 0),
    "/shop/category/maquinas-210": (281, 5),
    "/shop/category/escoplo-1584": (273, 8),
    "/shop/category/medicion-y-sujecion-167": (267, 5),
    "/shop/category/fresas-y-accesorios-para-router-221": (260, 9),
    "/shop/category/drywall-1582": (259, 8),
    "/shop/category/accesorios-176": (255, 4),
    "/shop/category/herrajes-y-tornilleria-191": (245, 6),
    "/shop/category/herramientas-de-corte-y-afilado-150": (239, 14),
    "/shop/category/herramientas-para-taladro-148": (227, 5),
    "/shop/category/herramientas-de-corte-y-afilado-1151": (222, 6),
    "/shop/category/herramientas-de-corte-y-afilado-152": (217, 6),
    "/shop/category/corte-y-afilado-149": (174, 3),
    "/shop/category/herramientas-de-corte-y-afilado-223": (167, 3),
    "/shop/category/herramientas-para-taladro-147": (163, 1),
    "/shop/category/madera-1593": (161, 2),
    "/shop/category/para-router-fresas-1-2-159": (120, 2),
    "/shop/category/medicion-y-sujecion-196": (117, 4),
    "/shop/category/herrajes-y-tornilleria-202": (114, 3),
    "/shop/category/fresas-y-accesorios-para-router-159": (113, 1),
    "/shop/category/para-taladro-145": (111, 1),
    "/shop/category/cortavidrios-toyotm-215": (99, 3),
    "/shop/category/maquinas-209": (81, 2),
    "/shop/category/para-router-fresas-1-4-172": (79, 0),
    "/shop/category/accesorios-213": (68, 4),
    "/shop/category/herramientas-de-corte-y-afilado-155": (56, 3),
    "/shop/category/fresas-y-accesorios-para-router-220": (52, 0),
    "/shop/category/triplay-1590": (36, 0),
    "/shop/category/accesorios-178": (23, 0),
    "/shop/category/cortavidrios-toyotm-214": (23, 0),
    "/shop/category/abrasivos-160": (18, 1),
    "/shop/category/complementos-para-muebles-1679": (17, 0),
    "/shop/category/herramientas-para-taladro-1150": (16, 0),
    "/shop/category/fresas-y-accesorios-para-router-171": (14, 0),
    "/shop/category/cnc-1586": (13, 0),
    "/shop/category/herramientas-para-taladro-146": (12, 1),
    "/shop/category/accesorios-accesorios-torno-176": (12, 0),
    "/shop/category/ranurado-1677": (11, 0),
    "/shop/category/accesorios-184": (10, 1),
    "/shop/category/accesorios-156": (9, 0),
    "/shop/category/machihembrado-1678": (8, 1),
    "/shop/category/eva-1589": (8, 0),
    "/shop/category/herramientas-de-corte-y-afilado-224": (8, 0),
    "/shop/category/para-taladro-broca-147": (8, 0),
    "/shop/category/accesorios-plantillas-jigs-156": (7, 0),
    "/shop/category/romana-1676": (7, 0),
    "/shop/category/accesorios-170": (6, 0),
    "/shop/category/herrajes-y-tornilleria-tornillos-191": (6, 0),
    "/shop/category/medicion-y-sujecion-194": (6, 0),
    "/shop/category/medicion-y-sujecion-195": (6, 0),
    "/shop/category/medicion-y-sujecion-guias-y-jigs-1651": (5, 0),
    "/shop/category/medicion-y-sujecion-sujecion-1643": (5, 0),
    "/shop/category/abrasivos-y-acabados-1645": (4, 1),
    "/shop/category/herrajes-y-tornilleria-204": (4, 1),
    "/shop/category/herrajes-y-montaje-1635": (4, 0),
    "/shop/category/herrajes-y-tornilleria-perfil-aluminio-4040-205": (4, 0),
    "/shop/category/abrasivos-y-acabados-lijado-1650": (3, 0),
}

# Regla de slug → id de categoría VIVA (primera coincidencia gana; orden importa:
# de más específico a más genérico)
REGLAS = [
    ("para-router-fresas-1-2", 1959),          # Fresas mango 1/2"
    ("para-router-fresas-1-4", 1960),          # Fresas mango 1/4"
    ("fresas-y-accesorios-para-router", 1884), # Fresas y Router
    ("para-router", 1884),
    ("herrajes-y-tornilleria-perfil-aluminio-4040", 1978),
    ("herrajes-y-tornilleria-tornillos", 1986),
    ("herrajes-y-tornilleria", 1885),
    ("herrajes-y-montaje", 1885),
    ("complementos-para-muebles", 1885),
    ("cnc", 2015),                             # Máquinas > CNC (antes que 'maquinas')
    ("maquinas", 1888),
    ("herramientas-de-corte-y-afilado", 1883),
    ("corte-y-afilado", 1883),
    ("medicion-y-sujecion-guias-y-jigs", 2002),
    ("medicion-y-sujecion-sujecion", 2008),
    ("medicion-y-sujecion", 1887),
    ("herramientas-para-taladro", 1889),
    ("para-taladro-broca", 2028),
    ("para-taladro", 1889),
    ("accesorios-accesorios-torno", 1890),
    ("accesorios-plantillas-jigs", 1927),
    ("accesorios", 1882),
    ("cerraduras", 1968),
    ("escoplo", 1945),
    ("drywall", 1908),
    ("madera", 1886),                          # Materiales
    ("triplay", 1886),
    ("eva", 1886),
    ("cortavidrios-toyotm", 1905),
    ("abrasivos-y-acabados-lijado", 1896),
    ("abrasivos-y-acabados", 1881),
    ("abrasivos", 1881),
    ("ranurado", 1966),                        # Trompo industrial
    ("machihembrado", 1966),
    ("romana", 1966),
]


def destino_de(path):
    slug = re.sub(r"-\d+$", "", path.rsplit("/", 1)[-1])
    for prefijo, cat_id in REGLAS:
        if slug == prefijo or slug.startswith(prefijo):
            return cat_id
    return None


def url_canonica(cat_id, cache={}):
    """Sigue el redirect de /shop/category/<id> para obtener el path canónico."""
    if cat_id in cache:
        return cache[cat_id]
    req = urllib.request.Request(f"{BASE}/shop/category/{cat_id}", method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0 (redirects-p1)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            final = r.url
    except Exception as e:
        sys.exit(f"No pude resolver la canónica de la categoría {cat_id}: {e}")
    path = final.replace(BASE, "")
    cache[cat_id] = path
    return path


def main():
    mapa = []  # (path_viejo, imp, clicks, cat_id, path_nuevo)
    sin_regla = []
    for path, (imp, clicks) in sorted(PATHS_404.items(), key=lambda x: -x[1][0]):
        cat = destino_de(path)
        if cat is None:
            sin_regla.append(path)
            continue
        mapa.append((path, imp, clicks, cat, url_canonica(cat)))

    print(f"{len(mapa)} redirecciones a crear "
          f"({sum(m[1] for m in mapa)} impresiones, {sum(m[2] for m in mapa)} clicks recuperados):\n")
    for path, imp, clicks, cat, nuevo in mapa:
        print(f"  {imp:>6} imp {clicks:>4} cl  {path}\n"
              f"                      → {nuevo}  (cat {cat})")
    if sin_regla:
        print(f"\n⚠️ SIN regla de destino (no se tocan): {sin_regla}")

    if not APPLY:
        print("\nDRY-RUN: nada creado. Repetir con --apply.")
        return

    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid:
        sys.exit("Autenticación Odoo fallida.")
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    def call(model, method, *a, **k):
        return models.execute_kw(DB, uid, KEY, model, method, list(a), k)

    existentes = {r["url_from"] for r in call("website.rewrite", "search_read", [],
                                              fields=["url_from"])}
    creados = 0
    for path, imp, clicks, cat, nuevo in mapa:
        if path in existentes:
            print(f"= ya existe redirect para {path}, se salta")
            continue
        call("website.rewrite", "create", [{
            "name": f"SEO P1 404→cat {cat}",
            "website_id": WEBSITE_ID,
            "url_from": path,
            "url_to": nuevo,
            "redirect_type": "301",
        }])
        creados += 1
    print(f"\n✅ {creados} redirecciones 301 creadas.")


if __name__ == "__main__":
    main()
