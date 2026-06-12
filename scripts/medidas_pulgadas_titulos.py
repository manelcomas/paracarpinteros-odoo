#!/usr/bin/env python3
"""Añade el equivalente en pulgadas (entre paréntesis, al final del título) a los
productos publicados cuyas medidas métricas del título son convertibles SIN mentir:

- Fracción exacta: medidas que SON estándar imperial (3.175mm=1/8", 6.35mm=1/4",
  12.7mm=1/2"...). Tolerancia ±0.05mm. Se aplica a diámetros de brocas/fresas/
  collets/pinzas/rodamientos y a cualquier medida que coincida.
- Nominal ≈: solo dimensiones >= 25.4mm que caen a <2.5% de una pulgada entera
  (bisagra 75mm -> ≈3"). Siempre con el símbolo ≈.

NO toca: productos que ya mencionan pulgadas/fracciones, sets/rangos de varias
medidas, ni medidas que no caen en ninguna de las dos categorías.

Uso:
    python3 scripts/medidas_pulgadas_titulos.py            # dry-run -> propuestas JSON
    python3 scripts/medidas_pulgadas_titulos.py --aplicar  # aplica el JSON revisado

El dry-run escribe scripts/_backups/pulgadas_propuestas.json (editable a mano para
podar filas antes de --aplicar). Al aplicar se guarda backup de los valores viejos.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_project_env

load_project_env()

import xmlrpc.client

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backups")
PROPUESTAS = os.path.join(BACKUP_DIR, "pulgadas_propuestas.json")
BACKUP_APLICADO = os.path.join(BACKUP_DIR, "pulgadas_titulos_backup.json")

# mm exactos de fracciones de pulgada estándar (tolerancia ±0.05mm)
FRACCIONES = [
    (3.175, '1/8"'), (4.7625, '3/16"'), (6.35, '1/4"'), (7.9375, '5/16"'),
    (9.525, '3/8"'), (11.1125, '7/16"'), (12.7, '1/2"'), (15.875, '5/8"'),
    (19.05, '3/4"'), (22.225, '7/8"'), (25.4, '1"'), (31.75, '1-1/4"'),
    (38.1, '1-1/2"'), (44.45, '1-3/4"'), (50.8, '2"'), (63.5, '2-1/2"'),
    (76.2, '3"'), (101.6, '4"'), (127.0, '5"'), (152.4, '6"'),
]

RE_YA_PULGADAS = re.compile(r'pulgada|″|"|\d\s*/\s*\d|\b\d+(?:\.\d+)?\s*in\b|\d\s*\'', re.I)
RE_RANGO = re.compile(r'\d+(?:[.,]\d+)?\s*(?:-|–|a)\s*\d+(?:[.,]\d+)?\s*(?:mm|cm)\b', re.I)
RE_MULTIMEDIDA = re.compile(r'\d+(?:[.,]\d+)?(?:\s*-\s*\d+(?:[.,]\d+)?){2,}\s*(?:mm|cm)', re.I)
RE_DIM = re.compile(
    r'(\d+(?:[.,]\d+)?)\s*[x×*]\s*(\d+(?:[.,]\d+)?)'
    r'(?:\s*[x×*]\s*(\d+(?:[.,]\d+)?))?\s*(cm|mm)\b', re.I)
RE_SIMPLE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(cm|mm)\b', re.I)


def fraccion_exacta(mm):
    for ref, frac in FRACCIONES:
        if abs(mm - ref) <= 0.05:
            return frac
    return None


def nominal(mm):
    """Pulgada entera si la medida >= 25.4mm cae a <2.5%; si no, None."""
    if mm < 25.4:
        return None
    pulgadas = mm / 25.4
    entero = round(pulgadas)
    if entero >= 1 and abs(pulgadas - entero) / entero < 0.025:
        return f'≈{entero}"'
    return None


def proponer(nombre):
    """Devuelve el sufijo '(...)' o None si no hay conversión honesta."""
    if RE_YA_PULGADAS.search(nombre):
        return None
    if RE_MULTIMEDIDA.search(nombre) or RE_RANGO.search(nombre):
        return None  # sets / rangos: ambiguo, fuera

    m_dim = RE_DIM.search(nombre)
    if m_dim:
        nums = [float(x.replace(",", ".")) for x in m_dim.groups()[:3] if x]
        unidad = m_dim.group(4).lower()
        mms = [v * 10 if unidad == "cm" else v for v in nums]
        # todas fracción exacta -> combinada en fracciones
        fracs = [fraccion_exacta(v) for v in mms]
        if all(fracs):
            return "(" + " x ".join(fracs) + ")"
        # si no: solo dimensiones grandes con nominal entero, y TODAS deben tenerlo
        # (excepto que se permite omitir las menores a 1")
        grandes = [v for v in mms if v >= 25.4]
        if not grandes:
            return None
        noms = [fraccion_exacta(v) or nominal(v) for v in grandes]
        if all(noms) and len(grandes) >= 1:
            # solo merece la pena si la mayor es >= 2" (búsquedas tipo "bisagra 3 pulgadas")
            if max(grandes) >= 50.8:
                return "(" + " x ".join(noms) + ")"
        return None

    m_simple = RE_SIMPLE.search(nombre)
    if m_simple:
        # solo si hay UNA medida en el título (sin ambigüedad)
        if len(RE_SIMPLE.findall(nombre)) != 1:
            return None
        v = float(m_simple.group(1).replace(",", "."))
        mm = v * 10 if m_simple.group(2).lower() == "cm" else v
        frac = fraccion_exacta(mm)
        if frac:
            return f"({frac})"
        nom = nominal(mm)
        if nom and mm >= 50.8:
            return f"({nom})"
    return None


def conectar():
    url, db = os.environ["ODOO_URL"], os.environ["ODOO_DB"]
    user, key = os.environ["ODOO_USERNAME"], os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/common", allow_none=True).authenticate(db, user, key, {})
    models = xmlrpc.client.ServerProxy(url + "/xmlrpc/2/object", allow_none=True)

    def call(model, method, *args, **kw):
        return models.execute_kw(db, uid, key, model, method, list(args), kw)

    return call


def dry_run():
    call = conectar()
    recs = call("product.template", "search_read", [["is_published", "=", True]],
                fields=["name", "default_code", "website_meta_title"], limit=3000)
    propuestas = []
    for r in recs:
        suf = proponer(r["name"])
        if not suf:
            continue
        meta = r["website_meta_title"] or ""
        meta_nueva = ""
        if meta and not RE_YA_PULGADAS.search(meta) and len(meta) + len(suf) + 1 <= 70:
            meta_nueva = f"{meta} {suf}"
        propuestas.append({
            "id": r["id"], "ref": r["default_code"] or "",
            "nombre": r["name"], "sufijo": suf,
            "nombre_nuevo": f'{r["name"].rstrip()} {suf}',
            "meta_title": meta, "meta_title_nuevo": meta_nueva,
        })
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(PROPUESTAS, "w") as f:
        json.dump(propuestas, f, ensure_ascii=False, indent=1)
    print(f"{len(propuestas)} propuestas -> {PROPUESTAS}")
    for p in propuestas:
        print(f'  {p["ref"]:>6} | {p["sufijo"]:<22} | {p["nombre"][:95]}')


def aplicar():
    call = conectar()
    with open(PROPUESTAS) as f:
        propuestas = json.load(f)
    backup = []
    for p in propuestas:
        viejo = call("product.template", "read", [p["id"]],
                     fields=["name", "website_meta_title"])[0]
        if viejo["name"] != p["nombre"]:
            print(f'SKIP {p["ref"]}: el nombre cambió en Odoo desde el dry-run')
            continue
        vals = {"name": p["nombre_nuevo"]}
        if p["meta_title_nuevo"]:
            vals["website_meta_title"] = p["meta_title_nuevo"]
        call("product.template", "write", [p["id"]], vals)
        backup.append({"id": p["id"], "ref": p["ref"], "name": viejo["name"],
                       "website_meta_title": viejo["website_meta_title"]})
        print(f'OK {p["ref"]} -> {p["nombre_nuevo"][:100]}')
    with open(BACKUP_APLICADO, "w") as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
    print(f"\n{len(backup)} aplicados. Backup de originales -> {BACKUP_APLICADO}")


if __name__ == "__main__":
    aplicar() if "--aplicar" in sys.argv else dry_run()
