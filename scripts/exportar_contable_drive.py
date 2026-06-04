#!/usr/bin/env python3
"""
Exporta los comprobantes electrónicos EMITIDOS y RECIBIDOS a una carpeta local
organizada por año/mes y la sincroniza a Google Drive con rclone, para que el
contable tenga acceso read-only a lo legalmente obligatorio.

Dos fuentes distintas (cada una con su almacenamiento):

  EMITIDAS (FE/NC/ND propias, aceptadas por Hacienda)
    Fuente: attachments `FE_<clave>.xml` (+ `_respuesta_hacienda.xml`) que el
    conversor guarda en Odoo. Solo se exportan los que Hacienda marcó "Aceptado".
    Salida:  Emitidas Aceptadas - AAAA/MM_Mes/FE_<clave>.xml (+ _respuesta_hacienda.xml)

  RECIBIDAS (facturas de proveedores que llegan por Gmail al buzón)
    Fuente: tabla `xmls_recibidos` de `buzon.db` (vive en el VPS, volumen Docker
    del fe-signer). Se trae una copia por scp y se lee con sqlite3 (stdlib).
    Se exportan TODAS (cualquier estado). Cada una con su XML recibido y, si
    existen, el Mensaje Receptor que mandamos y la respuesta de Hacienda al MR.
    Salida:  Recibidas - AAAA/MM_Mes/FE_<clave>.xml (+ _MR.xml, _MR_respuesta_hacienda.xml)

Uso:
  # Exportar todo + sincronizar a Drive (lo normal, lo corre el cron):
  python3 scripts/exportar_contable_drive.py

  # Solo exportar local, sin tocar Drive (para probar):
  python3 scripts/exportar_contable_drive.py --no-sync

  # Solo una de las dos fuentes:
  python3 scripts/exportar_contable_drive.py --solo-emitidas
  python3 scripts/exportar_contable_drive.py --solo-recibidas

Variables (con default, opcionales en el .env baúl):
  CONTABLE_OUT_DIR   carpeta local de salida    (default: ~/contable_export)
  RCLONE_REMOTE      nombre del remote rclone    (default: drive)
  RCLONE_BASE        carpeta base en Drive       (default: "Contabilidad Paracarpinteros")
  VPS_SSH            destino ssh del VPS         (default: root@66.94.99.220)
  BUZON_DB_REMOTE    ruta del buzon.db en el VPS (default: /opt/paracarpinteros-odoo/fe-signer/storage/buzon.db)
"""
import os, sys, base64, re, argparse, subprocess, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(__file__))
from _env import load_project_env; load_project_env()
import xmlrpc.client

MESES = {"01":"Enero","02":"Febrero","03":"Marzo","04":"Abril","05":"Mayo","06":"Junio",
         "07":"Julio","08":"Agosto","09":"Setiembre","10":"Octubre","11":"Noviembre","12":"Diciembre"}


def _field(tag, x):
    m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), x, flags=re.S)
    return m.group(1) if m else None


def _write_xml(path, data):
    """Escribe data (str o bytes) como bytes. SQLite puede devolver TEXT o BLOB."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    open(path, "wb").write(data)


def _carpeta(out_dir, prefijo, fecha):
    """prefijo + año/mes a partir de una fecha 'AAAA-MM...'. None si no parsea."""
    anio, mes = fecha[:4], fecha[5:7]
    if not (anio.isdigit() and mes in MESES):
        return None
    sub = "%s - %s" % (prefijo, anio)
    carpeta = os.path.join(out_dir, sub, "%s_%s" % (mes, MESES[mes]))
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def exportar_emitidas(out_dir, ex):
    """FE/NC/ND propias aceptadas, desde los attachments de Odoo."""
    ids = ex("ir.attachment", "search", [[
        ["name", "=like", "FE_%.xml"],
        ["name", "not like", "%respuesta%"],
        ["name", "not like", "%RECIBIDA%"],
        ["mimetype", "=", "application/xml"],
    ]], {"limit": 1000})

    escritos = 0
    for r in ex("ir.attachment", "read", [ids], {"fields": ["name", "datas"]}):
        raw = base64.b64decode(r["datas"])
        xs = raw.decode("utf-8", "replace")
        clave = _field("Clave", xs)
        if not clave:
            continue
        rid = ex("ir.attachment", "search", [[["name", "=", "FE_%s_respuesta_hacienda.xml" % clave]]], {"limit": 1})
        if not rid:
            continue
        resp = base64.b64decode(ex("ir.attachment", "read", [rid], {"fields": ["datas"]})[0]["datas"])
        if _field("EstadoMensaje", resp.decode("utf-8", "replace")) != "Aceptado":
            continue
        carpeta = _carpeta(out_dir, "Emitidas Aceptadas", _field("FechaEmision", xs) or "")
        if not carpeta:
            continue
        open(os.path.join(carpeta, "FE_%s.xml" % clave), "wb").write(raw)
        open(os.path.join(carpeta, "FE_%s_respuesta_hacienda.xml" % clave), "wb").write(resp)
        escritos += 1
    print("Emitidas aceptadas exportadas: %d" % escritos)


def exportar_recibidas(out_dir):
    """Facturas de proveedores, desde buzon.db del VPS (todos los estados)."""
    vps = os.environ.get("VPS_SSH", "root@66.94.99.220")
    remote_db = os.environ.get("BUZON_DB_REMOTE", "/opt/paracarpinteros-odoo/fe-signer/storage/buzon.db")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        local_db = tf.name
    try:
        cp = subprocess.run(["scp", "-o", "BatchMode=yes", "%s:%s" % (vps, remote_db), local_db],
                            capture_output=True, text=True)
        if cp.returncode != 0:
            sys.exit("scp del buzon.db falló: %s" % (cp.stderr.strip() or cp.stdout.strip()))

        con = sqlite3.connect(local_db); con.row_factory = sqlite3.Row
        escritos = 0
        for r in con.execute("SELECT clave, fecha_emision, xml_content, mr_xml, mr_respuesta_hacienda FROM xmls_recibidos"):
            clave = r["clave"]
            if not clave or not r["xml_content"]:
                continue
            carpeta = _carpeta(out_dir, "Recibidas", r["fecha_emision"] or "")
            if not carpeta:
                continue
            _write_xml(os.path.join(carpeta, "FE_%s.xml" % clave), r["xml_content"])
            if r["mr_xml"]:
                _write_xml(os.path.join(carpeta, "FE_%s_MR.xml" % clave), r["mr_xml"])
            if r["mr_respuesta_hacienda"]:
                _write_xml(os.path.join(carpeta, "FE_%s_MR_respuesta_hacienda.xml" % clave), r["mr_respuesta_hacienda"])
            escritos += 1
        con.close()
        print("Recibidas exportadas: %d" % escritos)
    finally:
        os.unlink(local_db)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sync", action="store_true", help="Solo exporta local, no sincroniza a Drive.")
    ap.add_argument("--solo-emitidas", action="store_true", help="Exportar solo las emitidas (Odoo).")
    ap.add_argument("--solo-recibidas", action="store_true", help="Exportar solo las recibidas (buzon.db).")
    args = ap.parse_args()

    out_dir = os.path.expanduser(os.environ.get("CONTABLE_OUT_DIR", "~/contable_export"))
    remote  = os.environ.get("RCLONE_REMOTE", "drive")
    base    = os.environ.get("RCLONE_BASE", "Contabilidad Paracarpinteros")
    os.makedirs(out_dir, exist_ok=True)

    hacer_emitidas = not args.solo_recibidas
    hacer_recibidas = not args.solo_emitidas

    if hacer_emitidas:
        URL=os.environ["ODOO_URL"]; DB=os.environ["ODOO_DB"]
        USER=os.environ["ODOO_USERNAME"]; KEY=os.environ["ODOO_API_KEY"]
        uid = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True).authenticate(DB, USER, KEY, {})
        M = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)
        exportar_emitidas(out_dir, lambda *a, **k: M.execute_kw(DB, uid, KEY, *a, **k))

    if hacer_recibidas:
        exportar_recibidas(out_dir)

    print("Salida local: %s" % out_dir)

    if args.no_sync:
        print("--no-sync: no se sincroniza a Drive.")
        return

    dest = "%s:%s/" % (remote, base)
    print("Sincronizando a Drive: %s" % dest)
    # 'copy' (no 'sync') para no borrar en Drive lo que ya no esté local.
    cp = subprocess.run(["rclone", "copy", out_dir + "/", dest, "--progress"], capture_output=True, text=True)
    sys.stdout.write(cp.stdout); sys.stderr.write(cp.stderr)
    if cp.returncode != 0:
        sys.exit("rclone falló (código %d)" % cp.returncode)
    print("Sincronización OK.")


if __name__ == "__main__":
    main()
