#!/usr/bin/env python3
"""
Exporta los comprobantes electrónicos EMITIDOS y RECIBIDOS a una carpeta local
organizada por año/mes y la sincroniza a Google Drive con rclone, para que el
contable tenga acceso read-only a lo legalmente obligatorio.

Dos fuentes distintas (cada una con su almacenamiento):

  EMITIDAS (FE/NC/ND propias, aceptadas por Hacienda)
    Fuente: attachments `FE_<clave>.xml` (+ `_respuesta_hacienda.xml`) que el
    conversor guarda en Odoo. Solo se exportan los que Hacienda marcó "Aceptado".

  RECIBIDAS (facturas de proveedores que llegan por Gmail al buzón)
    Fuente: tabla `xmls_recibidos` de `buzon.db` (vive en el VPS, volumen Docker
    del fe-signer). Se trae una copia por scp y se lee con sqlite3 (stdlib).
    Se exportan TODAS (cualquier estado). Cada una con su XML recibido y, si
    existen, el Mensaje Receptor que mandamos y la respuesta de Hacienda al MR.

NOMBRES DE ARCHIVO LEGIBLES (en vez del número de clave de 50 dígitos):
    <fecha>_<consecutivo>_<NOMBRE>_₡<total>.xml
  donde NOMBRE es el CLIENTE en emitidas (el emisor siempre es Gabriela) y el
  PROVEEDOR en recibidas. La clave de 50 dígitos se conserva dentro del XML y en
  la columna "Clave" del resumen. Ej:
    2026-05-04_156_MUEBLES-FACATO-SOCIEDAD-ANONIMA_₡6.501.xml

RESUMEN tipo Alegra (una fila por factura):
    En cada carpeta de año se genera "Resumen Emitidas AAAA.csv" /
    "Resumen Recibidas AAAA.csv" con cabecera por factura (fecha, tipo,
    consecutivo, cliente/proveedor, cédula, condición, medio de pago, moneda,
    gravado, exento, descuento, IVA, total, estado, clave) y una fila TOTAL por
    moneda al final. CSV en UTF-8 con BOM y separador ';' (abre directo en Excel
    en español).

Salida (ejemplos):
    Emitidas Aceptadas - 2026/05_Mayo/2026-05-04_156_<CLIENTE>_₡6.501.xml
    Emitidas Aceptadas - 2026/Resumen Emitidas 2026.csv
    Recibidas - 2026/05_Mayo/2026-05-12_88_<PROVEEDOR>_₡120.000.xml
    Recibidas - 2026/Resumen Recibidas 2026.csv

Uso:
  # Exportar todo + sincronizar a Drive (lo normal, lo corre el cron):
  python3 scripts/exportar_contable_drive.py

  # Solo exportar local, sin tocar Drive (para probar):
  python3 scripts/exportar_contable_drive.py --no-sync

  # Solo una de las dos fuentes:
  python3 scripts/exportar_contable_drive.py --solo-emitidas
  python3 scripts/exportar_contable_drive.py --solo-recibidas

  # Borrar de Drive los archivos con el nombre VIEJO (FE_<clave>.xml) de corridas
  # anteriores, para que no queden duplicados con los nombres legibles nuevos.
  # Solo toca ficheros cuyo nombre empieza por "FE_"; no borra nada más.
  python3 scripts/exportar_contable_drive.py --limpiar-drive-viejos

Variables (con default, opcionales en el .env baúl):
  CONTABLE_OUT_DIR   carpeta local de salida    (default: ~/contable_export)
  RCLONE_REMOTE      nombre del remote rclone    (default: drive)
  RCLONE_BASE        carpeta base en Drive       (default: "Contabilidad Paracarpinteros")
  VPS_SSH            destino ssh del VPS         (default: root@66.94.99.220)
  BUZON_DB_REMOTE    ruta del buzon.db en el VPS (default: /opt/paracarpinteros-odoo/fe-signer/storage/buzon.db)
"""
import os, sys, base64, re, argparse, subprocess, tempfile, sqlite3, csv, shutil, glob, unicodedata
sys.path.insert(0, os.path.dirname(__file__))
from _env import load_project_env; load_project_env()
import xmlrpc.client

MESES = {"01":"Enero","02":"Febrero","03":"Marzo","04":"Abril","05":"Mayo","06":"Junio",
         "07":"Julio","08":"Agosto","09":"Setiembre","10":"Octubre","11":"Noviembre","12":"Diciembre"}

TIPO_DOC = {
    "FacturaElectronica": "FE", "NotaCreditoElectronica": "NC",
    "NotaDebitoElectronica": "ND", "TiqueteElectronico": "TE",
    "FacturaElectronicaCompra": "FEC", "FacturaElectronicaExportacion": "FEE",
}
COND_VENTA = {
    "01":"Contado","02":"Crédito","03":"Consignación","04":"Apartado",
    "05":"Arrendamiento opción compra","06":"Arrendamiento función financiera",
    "07":"Cobro a favor de tercero","08":"Servicios al Estado",
    "09":"Pago servicios pendiente","99":"Otros",
}
MEDIO_PAGO = {
    "01":"Efectivo","02":"Tarjeta","03":"Cheque","04":"Transferencia",
    "05":"Recaudado por terceros","06":"SINPE Móvil","07":"Plataforma digital","99":"Otros",
}
ESTADO_RX = {  # estado del buzon.db (recibidas) → español
    "accepted":"Aceptado","rejected":"Rechazado","pending":"Pendiente","partial":"Parcial",
}


def _field(tag, x):
    """Texto de un elemento hoja <tag>texto</tag> (el primero). None si no está."""
    m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), x or "", flags=re.S)
    return m.group(1) if m else None


def _sub(tag, x):
    """Contenido interno de <tag ...>...</tag> (puede anidar). '' si no está."""
    m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (tag, tag), x or "", flags=re.S)
    return m.group(1) if m else ""


def _slug(nombre, n=32):
    """Nombre apto para archivo: sin acentos, MAYÚSCULAS, guiones. 'SIN-NOMBRE' si vacío."""
    if not nombre:
        return "SIN-NOMBRE"
    t = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").upper()
    return (t[:n].rstrip("-")) or "SIN-NOMBRE"


def _money_csv(s):
    """Número de Hacienda ('228495.59') → '228495,59' para Excel en español. '' si no numérico."""
    try:
        return ("%.2f" % float(s)).replace(".", ",")
    except (TypeError, ValueError):
        return ""


def _money_file(s):
    """Total → entero con separador de miles con punto: '6500.20' → '6.501'."""
    try:
        return ("{:,}".format(int(round(float(s))))).replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def _parse(xs):
    """Extrae la cabecera contable de un XML de Hacienda (v4.3/4.4). dict con strings."""
    root = re.search(r"<([A-Za-z]+Electronica\w*|TiqueteElectronico)[ >]", xs)
    tipo = TIPO_DOC.get(root.group(1), root.group(1)) if root else "?"
    emisor = _sub("Emisor", xs)
    receptor = _sub("Receptor", xs)
    resumen = _sub("ResumenFactura", xs)
    consec = _field("NumeroConsecutivo", xs) or ""
    consec_short = str(int(consec[-10:])) if consec[-10:].isdigit() else consec
    cond = _field("CondicionVenta", xs) or ""
    medio = _field("TipoMedioPago", resumen) or _field("MedioPago", resumen) or ""
    return {
        "clave": _field("Clave", xs) or "",
        "fecha": (_field("FechaEmision", xs) or "")[:10],
        "tipo": tipo,
        "consec": consec,
        "consec_short": consec_short,
        "emisor_nombre": _field("Nombre", emisor) or "",
        "emisor_id": _field("Numero", emisor) or "",
        "receptor_nombre": _field("Nombre", receptor) or "",
        "receptor_id": _field("Numero", receptor) or "",
        "condicion": COND_VENTA.get(cond, cond),
        "medio": MEDIO_PAGO.get(medio, medio),
        "moneda": _field("CodigoMoneda", resumen) or "CRC",
        "gravado": _field("TotalGravado", resumen) or "0",
        "exento": _field("TotalExento", resumen) or "0",
        "descuento": _field("TotalDescuentos", resumen) or "0",
        "iva": _field("TotalImpuesto", resumen) or "0",
        "total": _field("TotalComprobante", resumen) or "0",
    }


def _wipe(out_dir, prefijo):
    """Borra las carpetas de una categoría ('Emitidas Aceptadas - *', 'Recibidas - *')
    en el staging LOCAL, para reconstruir limpio en cada corrida (sin mezclar nombres
    viejos/nuevos). Solo toca el directorio de salida local, nunca Drive."""
    for d in glob.glob(os.path.join(out_dir, "%s - *" % prefijo)):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


def _carpeta(out_dir, prefijo, fecha):
    """prefijo + año/mes a partir de una fecha 'AAAA-MM...'. None si no parsea."""
    anio, mes = fecha[:4], fecha[5:7]
    if not (anio.isdigit() and mes in MESES):
        return None
    carpeta = os.path.join(out_dir, "%s - %s" % (prefijo, anio), "%s_%s" % (mes, MESES[mes]))
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _basename(meta, contraparte_nombre, usados):
    """<fecha>_<consec>_<NOMBRE>_₡<total>, único dentro de su carpeta (sufijo -2,-3 si choca)."""
    base = "%s_%s_%s_₡%s" % (meta["fecha"], meta["consec_short"],
                             _slug(contraparte_nombre), _money_file(meta["total"]))
    cand, n = base, 1
    while cand in usados:
        n += 1
        cand = "%s-%d" % (base, n)
    usados.add(cand)
    return cand


def _fila(meta, contraparte_nombre, contraparte_id, estado):
    """dict-fila para el CSV de resumen (una por factura)."""
    return {
        "anio": meta["fecha"][:4],
        "Fecha": meta["fecha"],
        "Tipo": meta["tipo"],
        "Consecutivo": meta["consec_short"],
        "Contraparte": contraparte_nombre,
        "Cédula": contraparte_id,
        "Condición": meta["condicion"],
        "Medio de pago": meta["medio"],
        "Moneda": meta["moneda"],
        "Gravado": meta["gravado"],
        "Exento": meta["exento"],
        "Descuento": meta["descuento"],
        "IVA": meta["iva"],
        "Total": meta["total"],
        "Estado": estado,
        "Clave": meta["clave"],
    }


def exportar_emitidas(out_dir, ex):
    """FE/NC/ND propias aceptadas, desde los attachments de Odoo. Devuelve filas para el resumen."""
    _wipe(out_dir, "Emitidas Aceptadas")
    ids = ex("ir.attachment", "search", [[
        ["name", "=like", "FE_%.xml"],
        ["name", "not like", "%respuesta%"],
        ["name", "not like", "%RECIBIDA%"],
        ["mimetype", "=", "application/xml"],
    ]], {"limit": 1000})

    filas, usados = [], {}
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
        meta = _parse(xs)
        carpeta = _carpeta(out_dir, "Emitidas Aceptadas", meta["fecha"])
        if not carpeta:
            continue
        base = _basename(meta, meta["receptor_nombre"], usados.setdefault(carpeta, set()))
        open(os.path.join(carpeta, "%s.xml" % base), "wb").write(raw)
        open(os.path.join(carpeta, "%s_respuesta_hacienda.xml" % base), "wb").write(resp)
        filas.append(_fila(meta, meta["receptor_nombre"], meta["receptor_id"], "Aceptado"))
    print("Emitidas aceptadas exportadas: %d" % len(filas))
    return filas


def exportar_recibidas(out_dir):
    """Facturas de proveedores, desde buzon.db del VPS (todos los estados). Devuelve filas."""
    _wipe(out_dir, "Recibidas")
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
        cols = {r[1] for r in con.execute("PRAGMA table_info(xmls_recibidos)")}
        tiene_estado = "estado" in cols
        sel = "clave, fecha_emision, xml_content, mr_xml, mr_respuesta_hacienda"
        if tiene_estado:
            sel += ", estado"
        filas, usados = [], {}
        for r in con.execute("SELECT %s FROM xmls_recibidos" % sel):
            clave = r["clave"]
            if not clave or not r["xml_content"]:
                continue
            xc = r["xml_content"]
            xs = xc.decode("utf-8", "replace") if isinstance(xc, (bytes, bytearray)) else str(xc)
            meta = _parse(xs)
            if not meta["fecha"]:
                meta["fecha"] = (r["fecha_emision"] or "")[:10]
            carpeta = _carpeta(out_dir, "Recibidas", meta["fecha"])
            if not carpeta:
                continue
            base = _basename(meta, meta["emisor_nombre"], usados.setdefault(carpeta, set()))
            _write_xml(os.path.join(carpeta, "%s.xml" % base), r["xml_content"])
            if r["mr_xml"]:
                _write_xml(os.path.join(carpeta, "%s_MR.xml" % base), r["mr_xml"])
            if r["mr_respuesta_hacienda"]:
                _write_xml(os.path.join(carpeta, "%s_MR_respuesta_hacienda.xml" % base), r["mr_respuesta_hacienda"])
            est_raw = (r["estado"] if tiene_estado and r["estado"] else "")
            estado = ESTADO_RX.get(est_raw, est_raw)
            filas.append(_fila(meta, meta["emisor_nombre"], meta["emisor_id"], estado))
        con.close()
        print("Recibidas exportadas: %d" % len(filas))
        return filas
    finally:
        os.unlink(local_db)


def _write_xml(path, data):
    """Escribe data (str o bytes) como bytes. SQLite puede devolver TEXT o BLOB."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    open(path, "wb").write(data)


def escribir_resumenes(out_dir, prefijo, etiqueta, col_contraparte, filas):
    """Un CSV por año en la carpeta '<prefijo> - AAAA/Resumen <etiqueta> AAAA.csv'.
    col_contraparte es el encabezado de la columna de nombre ('Cliente'/'Proveedor').
    Una fila por factura + fila TOTAL por moneda al final. ';' + UTF-8 BOM (Excel ES)."""
    cabecera = ["Fecha", "Tipo", "Consecutivo", col_contraparte, "Cédula", "Condición",
                "Medio de pago", "Moneda", "Gravado", "Exento", "Descuento", "IVA",
                "Total", "Estado", "Clave"]
    por_anio = {}
    for f in filas:
        por_anio.setdefault(f["anio"], []).append(f)

    for anio, fs in sorted(por_anio.items()):
        fs.sort(key=lambda f: (f["Fecha"], f["Consecutivo"].zfill(12)))
        carpeta = os.path.join(out_dir, "%s - %s" % (prefijo, anio))
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, "Resumen %s %s.csv" % (etiqueta, anio))
        tot = {}  # moneda -> [gravado, exento, descuento, iva, total]
        with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(cabecera)
            for f in fs:
                # Las notas de crédito (NC) reversan: importes en negativo, así el TOTAL
                # de la columna ya queda neteado. ND/FE/TE/etc. en positivo.
                signo = -1.0 if f["Tipo"] == "NC" else 1.0
                montos = []
                acc = tot.setdefault(f["Moneda"], [0.0, 0.0, 0.0, 0.0, 0.0])
                for i, k in enumerate(("Gravado", "Exento", "Descuento", "IVA", "Total")):
                    try:
                        v = float(f[k]) * signo
                    except (TypeError, ValueError):
                        montos.append("")
                        continue
                    montos.append(_money_csv(v))
                    acc[i] += v
                w.writerow([
                    f["Fecha"], f["Tipo"], f["Consecutivo"], f["Contraparte"], f["Cédula"],
                    f["Condición"], f["Medio de pago"], f["Moneda"],
                    montos[0], montos[1], montos[2], montos[3], montos[4], f["Estado"], f["Clave"],
                ])
            for moneda, acc in sorted(tot.items()):
                w.writerow([])
                w.writerow(["TOTAL (%s)" % moneda, "", "", "", "", "", "", moneda,
                            _money_csv(acc[0]), _money_csv(acc[1]), _money_csv(acc[2]),
                            _money_csv(acc[3]), _money_csv(acc[4]), "%d facturas" % len(fs), ""])
        print("Resumen: %s (%d facturas)" % (ruta, len(fs)))


def limpiar_drive_viejos(dest):
    """Borra de Drive SOLO los archivos con el nombre viejo (FE_<clave>.xml) de corridas
    anteriores. Los nombres nuevos empiezan por la fecha (AAAA-...), así que no se tocan."""
    print("Borrando de Drive los archivos con nombre viejo 'FE_*' en %s ..." % dest)
    cp = subprocess.run(["rclone", "delete", dest, "--include", "FE_*.xml"],
                        capture_output=True, text=True)
    sys.stdout.write(cp.stdout); sys.stderr.write(cp.stderr)
    if cp.returncode != 0:
        sys.exit("rclone delete falló (código %d)" % cp.returncode)
    print("Limpieza de nombres viejos OK.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sync", action="store_true", help="Solo exporta local, no sincroniza a Drive.")
    ap.add_argument("--solo-emitidas", action="store_true", help="Exportar solo las emitidas (Odoo).")
    ap.add_argument("--solo-recibidas", action="store_true", help="Exportar solo las recibidas (buzon.db).")
    ap.add_argument("--limpiar-drive-viejos", action="store_true",
                    help="Tras sincronizar, borrar de Drive los archivos con nombre viejo FE_<clave>.xml.")
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
        filas_em = exportar_emitidas(out_dir, lambda *a, **k: M.execute_kw(DB, uid, KEY, *a, **k))
        escribir_resumenes(out_dir, "Emitidas Aceptadas", "Emitidas", "Cliente", filas_em)

    if hacer_recibidas:
        filas_re = exportar_recibidas(out_dir)
        escribir_resumenes(out_dir, "Recibidas", "Recibidas", "Proveedor", filas_re)

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

    if args.limpiar_drive_viejos:
        limpiar_drive_viejos("%s:%s" % (remote, base))


if __name__ == "__main__":
    main()
