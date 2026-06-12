#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mapa de keywords desde Google Search Console (solo lectura).

Consulta searchanalytics de los últimos 90 días (dimensiones query+page,
rowLimit 5000) con cuenta de servicio y genera:

- gsc-keywords.xlsx
    · hoja "Queries": query | clicks | impresiones | CTR | posición media
      (agregado por query, ordenado por impresiones)
    · hoja "Oportunidades": queries con posición media 5–20 e impresiones >50,
      con la página que rankea y marca de términos clave (costa rica / producto)
- resumen.md: top 20 por clicks, top 20 oportunidades, páginas que concentran
  el tráfico.

No escribe nada en Odoo ni en GSC.

Uso:
  python3 scripts/gsc_keywords.py
  python3 scripts/gsc_keywords.py --dias 90 --key .credentials/gsc-service-account.json
"""
import argparse
import datetime
import os
import sys
from collections import defaultdict

from google.oauth2 import service_account
from googleapiclient.discovery import build
from openpyxl import Workbook
from openpyxl.styles import Font

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPIEDAD = "sc-domain:paracarpinteros.com"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

TERMINOS_PRODUCTO = ["fresa", "broca", "forstner", "minifix", "helicoil",
                     "tung", "linaza", "enchapadora", "bisagra", "tornillo"]


def marcas(query):
    q = query.lower()
    encontradas = []
    if "costa rica" in q:
        encontradas.append("costa rica")
    encontradas += [t for t in TERMINOS_PRODUCTO if t in q]
    return ", ".join(encontradas)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", default=os.path.join(RAIZ, ".credentials", "gsc-service-account.json"))
    ap.add_argument("--dias", type=int, default=90)
    ap.add_argument("--row-limit", type=int, default=5000)
    args = ap.parse_args()

    if not os.path.exists(args.key):
        sys.exit(f"No existe la clave {args.key} — descargá el JSON de la cuenta "
                 "de servicio (Cloud Console → IAM → Cuentas de servicio → Claves) "
                 "y guardalo ahí con chmod 600.")

    creds = service_account.Credentials.from_service_account_file(args.key, scopes=SCOPES)
    servicio = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    hoy = datetime.date.today()
    inicio = hoy - datetime.timedelta(days=args.dias)
    cuerpo = {
        "startDate": inicio.isoformat(),
        "endDate": hoy.isoformat(),
        "dimensions": ["query", "page"],
        "rowLimit": args.row_limit,
    }
    try:
        resp = servicio.searchanalytics().query(siteUrl=PROPIEDAD, body=cuerpo).execute()
    except Exception as e:
        if "403" in str(e):
            sys.exit(f"403 de la API: la cuenta de servicio no tiene acceso a la "
                     f"propiedad. En Search Console → Ajustes → Usuarios, añadí el "
                     f"client_email del JSON como usuario (con 'Completo' o "
                     f"'Restringido').\nDetalle: {e}")
        raise
    filas = resp.get("rows", [])
    print(f"{len(filas)} filas query+page ({inicio} → {hoy})", file=sys.stderr)
    if not filas:
        sys.exit("GSC devolvió 0 filas (¿propiedad recién verificada? los datos tardan ~2 días).")

    # ---- Agregado por query (posición ponderada por impresiones)
    por_query = defaultdict(lambda: {"clicks": 0, "imp": 0, "pos_pond": 0.0})
    por_pagina = defaultdict(lambda: {"clicks": 0, "imp": 0})
    detalle = []  # filas crudas query+page
    for f in filas:
        q, pagina = f["keys"]
        d = por_query[q]
        d["clicks"] += f["clicks"]
        d["imp"] += f["impressions"]
        d["pos_pond"] += f["position"] * f["impressions"]
        por_pagina[pagina]["clicks"] += f["clicks"]
        por_pagina[pagina]["imp"] += f["impressions"]
        detalle.append({"query": q, "page": pagina, "clicks": f["clicks"],
                        "imp": f["impressions"], "ctr": f["ctr"], "pos": f["position"]})

    queries = sorted(
        ({"query": q, "clicks": d["clicks"], "imp": d["imp"],
          "ctr": d["clicks"] / d["imp"] if d["imp"] else 0,
          "pos": d["pos_pond"] / d["imp"] if d["imp"] else 0}
         for q, d in por_query.items()),
        key=lambda x: -x["imp"])

    # ---- Oportunidades: posición media 5–20 e impresiones >50 (a nivel query);
    #      página = la que más impresiones aporta a esa query
    mejor_pagina = {}
    for f in detalle:
        actual = mejor_pagina.get(f["query"])
        if actual is None or f["imp"] > actual["imp"]:
            mejor_pagina[f["query"]] = f
    oportunidades = [
        {**q, "page": mejor_pagina[q["query"]]["page"], "marcas": marcas(q["query"])}
        for q in queries
        if 5 <= q["pos"] <= 20 and q["imp"] > 50
    ]

    # ---- XLSX
    wb = Workbook()
    negrita = Font(bold=True)

    h1 = wb.active
    h1.title = "Queries"
    h1.append(["query", "clicks", "impresiones", "CTR", "posición media"])
    for c in h1[1]:
        c.font = negrita
    for q in queries:
        h1.append([q["query"], q["clicks"], q["imp"], round(q["ctr"], 4), round(q["pos"], 1)])
    h1.column_dimensions["A"].width = 55

    h2 = wb.create_sheet("Oportunidades")
    h2.append(["query", "clicks", "impresiones", "CTR", "posición media",
               "página que rankea", "términos clave"])
    for c in h2[1]:
        c.font = negrita
    for o in oportunidades:
        h2.append([o["query"], o["clicks"], o["imp"], round(o["ctr"], 4),
                   round(o["pos"], 1), o["page"], o["marcas"]])
    h2.column_dimensions["A"].width = 55
    h2.column_dimensions["F"].width = 70
    h2.column_dimensions["G"].width = 30

    salida_xlsx = os.path.join(RAIZ, "gsc-keywords.xlsx")
    wb.save(salida_xlsx)

    # ---- resumen.md
    total_clicks = sum(q["clicks"] for q in queries)
    total_imp = sum(q["imp"] for q in queries)
    paginas = sorted(({"page": p, **d} for p, d in por_pagina.items()),
                     key=lambda x: -x["clicks"])

    lineas = [
        "# Resumen Search Console — paracarpinteros.com",
        "",
        f"- Periodo: {inicio} → {hoy} ({args.dias} días)",
        f"- Filas query+page: {len(filas)} (límite {args.row_limit}) · "
        f"queries únicas: {len(queries)} · páginas con tráfico: {len(paginas)}",
        f"- Totales: **{total_clicks} clicks**, {total_imp} impresiones",
        f"- Oportunidades (posición 5–20, >50 impresiones): **{len(oportunidades)}**",
        "",
        "## Top 20 queries por clicks",
        "",
        "| # | Query | Clicks | Impresiones | CTR | Posición |",
        "|---|---|---|---|---|---|",
    ]
    for i, q in enumerate(sorted(queries, key=lambda x: -x["clicks"])[:20], 1):
        lineas.append(f"| {i} | {q['query']} | {q['clicks']} | {q['imp']} | "
                      f"{q['ctr']:.1%} | {q['pos']:.1f} |")

    lineas += [
        "",
        "## Top 20 oportunidades (a un empujón del top: posición 5–20, >50 impresiones)",
        "",
        "| # | Query | Impresiones | Clicks | Posición | Términos clave | Página |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, o in enumerate(oportunidades[:20], 1):
        pagina_corta = o["page"].replace("https://www.paracarpinteros.com", "")
        lineas.append(f"| {i} | {o['query']} | {o['imp']} | {o['clicks']} | "
                      f"{o['pos']:.1f} | {o['marcas'] or '—'} | {pagina_corta} |")

    lineas += [
        "",
        "## Páginas que concentran el tráfico",
        "",
        "| # | Página | Clicks | % del total | Impresiones |",
        "|---|---|---|---|---|",
    ]
    acumulado = 0
    for i, p in enumerate(paginas[:15], 1):
        acumulado += p["clicks"]
        pagina_corta = p["page"].replace("https://www.paracarpinteros.com", "") or "/"
        pct = p["clicks"] / total_clicks if total_clicks else 0
        lineas.append(f"| {i} | {pagina_corta} | {p['clicks']} | {pct:.1%} | {p['imp']} |")
    if total_clicks:
        lineas.append("")
        lineas.append(f"Las 15 primeras páginas concentran el "
                      f"**{acumulado / total_clicks:.0%}** de los clicks.")
    lineas.append("")

    salida_md = os.path.join(RAIZ, "resumen.md")
    with open(salida_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    print(f"✅ {salida_xlsx}\n✅ {salida_md}", file=sys.stderr)


if __name__ == "__main__":
    main()
