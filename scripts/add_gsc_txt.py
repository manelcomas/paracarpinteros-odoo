#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crea el registro TXT de verificación de Google Search Console en la zona
paracarpinteros.com vía API de Cloudflare.

Acepta el token con o sin el prefijo "google-site-verification=". El registro
se crea en la raíz de la zona (@), que es lo que pide la propiedad de dominio
de GSC. Si ya existe un TXT idéntico, no duplica.

Auth: usa CF_API_TOKEN (Bearer) si está en el .env; si no, cae a
CLOUDFLARE_EMAIL + CLOUDFLARE_GLOBAL_API_KEY (lo que hay hoy en el baúl).

Uso:
  python3 scripts/add_gsc_txt.py TOKEN              # dry-run (muestra qué haría)
  python3 scripts/add_gsc_txt.py TOKEN --apply      # crea el registro
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_project_env
load_project_env()

ZONA = "paracarpinteros.com"
API = "https://api.cloudflare.com/client/v4"


def cabeceras():
    token = os.environ.get("CF_API_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    email = os.environ.get("CLOUDFLARE_EMAIL")
    clave = os.environ.get("CLOUDFLARE_GLOBAL_API_KEY")
    if email and clave:
        return {"X-Auth-Email": email, "X-Auth-Key": clave, "Content-Type": "application/json"}
    sys.exit("Faltan credenciales Cloudflare (CF_API_TOKEN o CLOUDFLARE_EMAIL+CLOUDFLARE_GLOBAL_API_KEY).")


def api(metodo, ruta, cuerpo=None):
    req = urllib.request.Request(
        API + ruta,
        data=json.dumps(cuerpo).encode() if cuerpo else None,
        headers=cabeceras(),
        method=metodo,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    if not resp.get("success"):
        sys.exit(f"Error API Cloudflare: {resp.get('errors')}")
    return resp["result"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("token", help="token de GSC (con o sin 'google-site-verification=')")
    ap.add_argument("--apply", action="store_true", help="crear el registro (sin esto, dry-run)")
    args = ap.parse_args()

    valor = args.token.strip()
    if not valor.startswith("google-site-verification="):
        valor = "google-site-verification=" + valor

    zonas = api("GET", f"/zones?name={ZONA}")
    if not zonas:
        sys.exit(f"Zona {ZONA} no encontrada en la cuenta.")
    zona_id = zonas[0]["id"]
    print(f"Zona {ZONA}: {zona_id}")

    existentes = api("GET", f"/zones/{zona_id}/dns_records?type=TXT&name={ZONA}&per_page=100")
    for r in existentes:
        if r["content"].strip('"') == valor:
            print(f"✅ Ya existe el TXT idéntico (id {r['id']}). Nada que hacer.")
            return
    if existentes:
        print(f"(hay {len(existentes)} TXT previos en la raíz, se conservan)")

    print(f"→ Crearía TXT en @ ({ZONA}): {valor}")
    if not args.apply:
        print("\nDRY-RUN: nada creado. Repetir con --apply para crear el registro.")
        return

    nuevo = api("POST", f"/zones/{zona_id}/dns_records", {
        "type": "TXT",
        "name": ZONA,
        "content": valor,
        "ttl": 3600,
        "comment": "Verificación Google Search Console",
    })
    print(f"✅ TXT creado (id {nuevo['id']}).")


if __name__ == "__main__":
    main()
