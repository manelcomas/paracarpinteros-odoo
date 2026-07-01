#!/usr/bin/env python3
"""Sube la copia versionada del FE Converter al ir.attachment 37459 de Odoo.

El conversor de factura electrónica vive DENTRO de Odoo como attachment, no en el
VPS ni con git pull (ver fe-signer/fe-converter/README.md). Este helper:
  1. Lee el HTML local  fe-signer/fe-converter/fe_converter.html
  2. Hace un BACKUP del attachment 37459 actual como fe_converter_BACKUP_<fecha>.html
  3. Sobrescribe el 37459 con el HTML local

Por defecto es dry-run (solo compara tamaños). Añade --apply para subir de verdad.
Tras subir: Ctrl+Shift+R en el navegador (el iframe cachea /web/content/37459).

    .venv/bin/python scripts/deploy_fe_converter.py            # dry-run
    .venv/bin/python scripts/deploy_fe_converter.py --apply    # sube + backup
"""
from __future__ import annotations

import base64
import os
import sys
import time
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import load_project_env  # noqa: E402

load_project_env()

ATTACH_ID = 37459
HTML_PATH = Path(__file__).resolve().parent.parent / "fe-signer" / "fe-converter" / "fe_converter.html"

URL = os.environ["ODOO_URL"].rstrip("/")
DB = os.environ["ODOO_DB"]
USER = os.environ["ODOO_USERNAME"]
KEY = os.environ["ODOO_API_KEY"]


def main() -> int:
    apply = "--apply" in sys.argv
    if not HTML_PATH.is_file():
        print(f"No existe {HTML_PATH}", file=sys.stderr)
        return 1
    local = HTML_PATH.read_bytes()

    uid = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True).authenticate(DB, USER, KEY, {})
    if not uid:
        print("authenticate=False: API key Odoo inválida/expirada.", file=sys.stderr)
        return 1
    M = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    cur = M.execute_kw(DB, uid, KEY, "ir.attachment", "read", [[ATTACH_ID]], {"fields": ["name", "datas"]})[0]
    remote = base64.b64decode(cur["datas"]) if cur.get("datas") else b""
    print(f"attachment {ATTACH_ID} ('{cur['name']}'): remoto {len(remote)} bytes · local {len(local)} bytes")
    if local == remote:
        print("Idénticos: nada que subir.")
        return 0
    if not apply:
        print("DRY-RUN. Difieren. Ejecuta con --apply para hacer backup + subir.")
        return 0

    # timestamp determinista sin depender de locale
    fecha = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    bkp_id = M.execute_kw(DB, uid, KEY, "ir.attachment", "create", [{
        "name": f"fe_converter_BACKUP_{fecha}.html",
        "datas": cur["datas"],
        "mimetype": "text/html",
    }])
    print(f"Backup creado: attachment {bkp_id}")
    M.execute_kw(DB, uid, KEY, "ir.attachment", "write", [[ATTACH_ID], {
        "datas": base64.b64encode(local).decode(),
    }])
    print(f"Subido. Ctrl+Shift+R en el navegador para refrescar /web/content/{ATTACH_ID}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
