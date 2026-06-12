#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2 de estrategia-seo.md: titles/metas/H1 de las dos páginas de conversión
mm↔pulgadas (88.614 impresiones en posición 7–11, CTR <0,5%).

- Página 23 /conversion-de-medidas-...: meta title/description orientados a
  "convertidor mm a pulgadas" + H1 con keyword (hoy: "CONVERSOR CARPINTERO PRO").
- Página 22 /tabla-conversiones-pulgadas-a-mm: especializada en fracciones y
  decimales (las queries que ya rankea: "7/32 a mm", "0.375 pulgadas a mm").
  Hoy no tiene meta title ni description.

Así cada página ataca su familia de queries y dejan de competir entre sí.
Backup del arch de la vista 3819 en scripts/_backups/ antes de tocar el H1.

Uso:
  python3 scripts/aplicar_p2_conversores.py            # dry-run
  python3 scripts/aplicar_p2_conversores.py --apply
"""
import datetime
import os
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

PAGINAS = {
    23: {  # /conversion-de-medidas-tabla-de-pulgadas-milimetros-y-fracciones
        "website_meta_title": "Convertidor de mm a Pulgadas (y Pulgadas a mm) con Tabla",
        "website_meta_description": ("Convertidor gratuito de milímetros a pulgadas y "
                                     "pulgadas a mm, con tabla de fracciones de carpintero. "
                                     "Incluye varas ticas y pies tabla."),
    },
    22: {  # /tabla-conversiones-pulgadas-a-mm
        "website_meta_title": "Tabla de Pulgadas a mm: Fracciones y Decimales Exactos",
        "website_meta_description": ("Tabla completa de equivalencias: fracciones de pulgada "
                                     "(1/16, 7/32, 7/8…) a milímetros exactos y decimales. "
                                     "Consultala gratis o imprimila para el taller."),
    },
}

VISTA_H1 = 3819
H1_VIEJO = "<h1>🪚 CONVERSOR CARPINTERO PRO</h1>"
H1_NUEVO = "<h1>🪚 Convertidor de mm a Pulgadas y Medidas de Carpintería</h1>"


def main():
    for pid, vals in PAGINAS.items():
        mt, md = vals["website_meta_title"], vals["website_meta_description"]
        assert len(mt) <= 60, f"meta title página {pid}: {len(mt)} chars (>60)"
        assert len(md) <= 155, f"meta description página {pid}: {len(md)} chars (>155)"

    if not KEY or not USER:
        sys.exit("Faltan credenciales Odoo.")
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid:
        sys.exit("Autenticación Odoo fallida.")
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    def call(model, method, *a, **k):
        return models.execute_kw(DB, uid, KEY, model, method, list(a), k)

    # ---- metas de las páginas
    for p in call("website.page", "read", list(PAGINAS),
                  fields=["url", "website_meta_title", "website_meta_description"]):
        vals = PAGINAS[p["id"]]
        print(f"→ página {p['id']} {p['url']}")
        for campo, nuevo in vals.items():
            print(f"    {campo} ({len(nuevo)} ch):\n"
                  f"      '{p.get(campo) or '(vacío)'}' →\n      '{nuevo}'")
        if APPLY:
            # GOTCHA: los meta son campos traducibles. La web renderiza en es_ES;
            # un write sin contexto escribe el valor fuente (en_US) y si la página
            # tiene un '' explícito en es_ES, lo tapa. Escribir SIEMPRE con lang.
            call("website.page", "write", [p["id"]], vals, context={"lang": "es_ES"})
            call("website.page", "write", [p["id"]], vals)
            print("    ✅ aplicado (es_ES + fuente)")

    # ---- H1 de la vista 3819
    arch = call("ir.ui.view", "read", [VISTA_H1], fields=["arch_db"])[0]["arch_db"]
    n = arch.count(H1_VIEJO)
    print(f"\n→ vista {VISTA_H1}: H1 '{H1_VIEJO}' aparece {n} veces")
    if n == 0:
        print("    ⚠️ H1 viejo no encontrado — no se toca la vista (¿ya cambiado?)")
    else:
        print(f"    → '{H1_NUEVO}'")
        if APPLY:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            bak_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backups")
            os.makedirs(bak_dir, exist_ok=True)
            bak = os.path.join(bak_dir, f"backup_view{VISTA_H1}_arch_{ts}.html")
            with open(bak, "w") as f:
                f.write(arch)
            call("ir.ui.view", "write", [VISTA_H1],
                 {"arch_db": arch.replace(H1_VIEJO, H1_NUEVO)})
            print(f"    ✅ aplicado (backup: {bak})")

    if not APPLY:
        print("\nDRY-RUN: nada escrito. Repetir con --apply.")


if __name__ == "__main__":
    main()
