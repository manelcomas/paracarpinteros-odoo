#!/usr/bin/env python3
"""Auditoría del schema.org Product en fichas de producto de www.paracarpinteros.com.

Lee el sitemap, toma N fichas de producto al azar, parsea JSON-LD y microdata,
y verifica que el schema Product tenga: name, image, sku, offers.price,
offers.priceCurrency=CRC y offers.availability.

Genera schema-audit.md en la raíz del repo. Script de SOLO LECTURA:
no escribe nada en Odoo ni en el VPS.

Uso:
    python3 scripts/audit_schema.py                 # 15 fichas al azar
    python3 scripts/audit_schema.py --n 30 --seed 42
"""

import argparse
import json
import random
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://www.paracarpinteros.com"
SITEMAP = BASE + "/sitemap.xml"
UA = "Mozilla/5.0 (compatible; ParacarpinterosSchemaAudit/1.0)"

CAMPOS = [
    "name",
    "image",
    "sku",
    "offers.price",
    "offers.priceCurrency=CRC",
    "offers.availability",
]


def descargar(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def urls_de_producto(sitemap_xml):
    urls = re.findall(r"<loc>([^<]+)</loc>", sitemap_xml)
    return [
        u for u in urls
        if "/shop/" in u and "/shop/category/" not in u and not u.rstrip("/").endswith("/shop")
    ]


def extraer_jsonld(html):
    """Devuelve la lista de objetos JSON-LD (aplanando arrays y @graph)."""
    objetos = []
    for m in re.finditer(
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html, re.S | re.I,
    ):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        pila = data if isinstance(data, list) else [data]
        for item in pila:
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    objetos.extend(x for x in item["@graph"] if isinstance(x, dict))
                else:
                    objetos.append(item)
    return objetos


class MicrodataParser(HTMLParser):
    """Detector mínimo de microdata Product: itemscope itemtype=...Product + itemprops."""

    def __init__(self):
        super().__init__()
        self.en_producto = False
        self.props = set()

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        itemtype = d.get("itemtype", "")
        if "itemscope" in d and "schema.org/Product" in itemtype:
            self.en_producto = True
        if self.en_producto and "itemprop" in d:
            self.props.add(d["itemprop"])


def buscar_product_jsonld(objetos):
    for obj in objetos:
        tipo = obj.get("@type")
        tipos = tipo if isinstance(tipo, list) else [tipo]
        if "Product" in tipos:
            return obj
    return None


def auditar_producto(prod):
    """Devuelve lista de campos faltantes/incorrectos del objeto Product JSON-LD."""
    faltan = []
    if not prod.get("name"):
        faltan.append("name")
    if not prod.get("image"):
        faltan.append("image")
    if not prod.get("sku"):
        faltan.append("sku")
    offers = prod.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if offers.get("price") in (None, ""):
        faltan.append("offers.price")
    if offers.get("priceCurrency") != "CRC":
        faltan.append("offers.priceCurrency=CRC")
    if not offers.get("availability"):
        faltan.append("offers.availability")
    return faltan


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=15, help="fichas a muestrear (default 15)")
    ap.add_argument("--seed", type=int, default=None, help="semilla para muestreo reproducible")
    ap.add_argument("--out", default="schema-audit.md", help="ruta del reporte")
    args = ap.parse_args()

    print(f"Descargando sitemap {SITEMAP} ...", file=sys.stderr)
    urls = urls_de_producto(descargar(SITEMAP))
    print(f"{len(urls)} fichas de producto en el sitemap", file=sys.stderr)
    if not urls:
        sys.exit("No se encontraron URLs de producto en el sitemap")

    rng = random.Random(args.seed)
    muestra = rng.sample(urls, min(args.n, len(urls)))

    filas = []  # (url, faltantes:list, nota:str)
    for i, url in enumerate(muestra, 1):
        print(f"[{i}/{len(muestra)}] {url}", file=sys.stderr)
        try:
            html = descargar(url)
        except Exception as e:
            filas.append((url, None, f"ERROR descarga: {e}"))
            continue

        jsonld = extraer_jsonld(html)
        prod = buscar_product_jsonld(jsonld)

        md = MicrodataParser()
        try:
            md.feed(html)
        except Exception:
            pass

        if prod is None:
            nota = "sin JSON-LD Product"
            if md.en_producto:
                nota += f" (hay microdata Product con props: {', '.join(sorted(md.props)) or 'ninguna'})"
            filas.append((url, CAMPOS[:], nota))
            continue

        faltan = auditar_producto(prod)
        nota = ""
        if md.en_producto:
            nota = f"microdata Product también presente (props: {', '.join(sorted(md.props))})"
        filas.append((url, faltan, nota))
        time.sleep(0.5)  # no martillar el server

    # Reporte
    raiz = Path(__file__).resolve().parent.parent
    out = (raiz / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    con_falta = [f for f in filas if f[1]]
    lineas = [
        "# Auditoría schema.org Product — www.paracarpinteros.com",
        "",
        f"- Fecha: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- Fichas muestreadas: {len(muestra)} de {len(urls)} en el sitemap"
        + (f" (seed {args.seed})" if args.seed is not None else ""),
        f"- Campos verificados: {', '.join(CAMPOS)}",
        f"- Fichas con algún campo faltante: {len(con_falta)}/{len(filas)}",
        "",
        "| URL | Campos faltantes | Nota |",
        "|---|---|---|",
    ]
    for url, faltan, nota in filas:
        celda = ", ".join(faltan) if faltan else "✅ ninguno"
        if faltan is None:
            celda = "(no auditado)"
        lineas.append(f"| {url} | {celda} | {nota} |")
    lineas.append("")
    out.write_text("\n".join(lineas), encoding="utf-8")
    print(f"\nReporte escrito en {out}", file=sys.stderr)

    # Resumen por campo
    conteo = {c: 0 for c in CAMPOS}
    for _, faltan, _ in filas:
        for c in faltan or []:
            if c in conteo:
                conteo[c] += 1
    print("\nFaltas por campo:", file=sys.stderr)
    for c, n in conteo.items():
        print(f"  {c}: {n}/{len(filas)}", file=sys.stderr)


if __name__ == "__main__":
    main()
