#!/usr/bin/env python3
"""
Firma y (opcionalmente) envía a Hacienda la Nota de Crédito espejo de la factura 1026 a UCR.

Replica EXACTAMENTE el flujo del conversor fe-converter (sign.php + Cloudflare Worker),
pero a partir del XML espejo construido desde la factura aceptada (no desde un account.move).

Uso:
  # 1) Solo firmar (NO envía) — para verificar que la firma sale bien:
  CERT_P12=/ruta/cert.p12 CERT_PIN=1234 python3 scripts/firmar_enviar_nc.py

  # 2) Firmar Y enviar a Hacienda (irreversible):
  CERT_P12=/ruta/cert.p12 CERT_PIN=1234 python3 scripts/firmar_enviar_nc.py --enviar

  # 3) Tras envío aceptado, subir el consecutivo del conversor para que no choque la próxima NC:
  python3 scripts/firmar_enviar_nc.py --bump-consecutivo

Notas:
  - El .p12 y el PIN los pone Manel (no están en el repo). Se pueden pasar por env
    (CERT_P12 / CERT_PIN) o por --p12 / --pin. Si falta el PIN, lo pide por teclado.
  - NO re-codifica el base64 firmado (preserva el RAW del firmador) — clave para que
    Hacienda no rechace con "El XML fue modificado luego de haber sido firmado".
"""
import os, sys, json, base64, argparse, getpass, urllib.request, urllib.error
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(__file__))
from _env import load_project_env; load_project_env()

XML_PATH    = os.path.join(os.path.dirname(__file__), "..", "fe-signer", "fe-converter", "borrador_NC_1026.xml")
SIGNER_URL  = "https://panel.paracarpinteros.com/sign.php"
SIGNER_KEY  = os.environ.get("SIGNER_API_KEY", "")  # del .env baúl (no hardcodear el secreto)
WORKER_URL  = "https://misty-cake-937c.lacarpicr.workers.dev"
TIPO_DOC    = "03"  # Nota de Crédito
CONSEC_KEY  = "fe.last_consec.PROD.003"
CONSEC_NEW  = "0000001030"


def _post_json(url, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    # User-Agent de navegador: el worker está tras Cloudflare, que bloquea con 403
    # (code 1010) el UA por defecto de urllib (Python-urllib/x.y) → respuesta no-JSON.
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _campos_xml(xml_text):
    root = ET.fromstring(__import__("re").sub(r'xmlns="[^"]+"', "", xml_text, count=1))
    g = lambda p: (root.find(p).text if root.find(p) is not None else None)
    return {
        "clave": g("Clave"),
        "fecha": g("FechaEmision"),
        "emisor_tipo": g("Emisor/Identificacion/Tipo"),
        "emisor_num":  g("Emisor/Identificacion/Numero"),
        "receptor_tipo": g("Receptor/Identificacion/Tipo"),
        "receptor_num":  g("Receptor/Identificacion/Numero"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enviar", action="store_true", help="Enviar a Hacienda (irreversible). Sin esto, SOLO firma.")
    ap.add_argument("--bump-consecutivo", action="store_true", help="Subir fe.last_consec.PROD.003 a 1030 (tras envío OK).")
    ap.add_argument("--p12", default=os.environ.get("CERT_P12", ""))
    ap.add_argument("--pin", default=os.environ.get("CERT_PIN", ""))
    ap.add_argument("--xml", default=XML_PATH, help="Ruta del XML a firmar/enviar (default: NC 1026)")
    ap.add_argument("--tipo", default=TIPO_DOC, help="tipoDoc Hacienda: 01 factura, 03 NC (default 03)")
    ap.add_argument("--consec-key", default=CONSEC_KEY, help="clave ir.config_parameter del consecutivo a bumpear")
    ap.add_argument("--consec-val", default=CONSEC_NEW, help="valor nuevo del consecutivo (10 dígitos)")
    args = ap.parse_args()

    if args.bump_consecutivo:
        import xmlrpc.client
        URL=os.environ["ODOO_URL"]; DB=os.environ["ODOO_DB"]; USER=os.environ["ODOO_USERNAME"]; KEY=os.environ["ODOO_API_KEY"]
        uid=xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common",allow_none=True).authenticate(DB,USER,KEY,{})
        M=xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object",allow_none=True)
        prev=M.execute_kw(DB,uid,KEY,"ir.config_parameter","get_param",[args.consec_key])
        M.execute_kw(DB,uid,KEY,"ir.config_parameter","set_param",[args.consec_key, args.consec_val])
        print(f"Consecutivo {args.consec_key}: {prev} -> {args.consec_val}")
        return

    xml_text = open(os.path.abspath(args.xml), encoding="utf-8").read()
    campos = _campos_xml(xml_text)
    print("XML:", os.path.abspath(args.xml), "| tipoDoc:", args.tipo)
    print("Clave NC:", campos["clave"])
    print("Receptor:", campos["receptor_tipo"], campos["receptor_num"])

    if not SIGNER_KEY:
        sys.exit("Falta SIGNER_API_KEY en el .env baúl (la usa sign.php).")

    p12_path = args.p12 or input(".p12 path: ").strip()
    pin = args.pin or getpass.getpass("PIN del certificado: ")
    if not os.path.isfile(p12_path):
        sys.exit(f"No existe el .p12: {p12_path}")

    # 1) FIRMAR
    p12_b64 = base64.b64encode(open(p12_path, "rb").read()).decode()
    xml_b64 = base64.b64encode(xml_text.encode("utf-8")).decode()
    print(f"\n[1/3] Firmando con sign.php (tipoDoc={args.tipo})...")
    st, body = _post_json(SIGNER_URL, {"xmlBase64": xml_b64, "p12Base64": p12_b64, "pin": pin, "tipoDoc": args.tipo},
                          {"X-API-Key": SIGNER_KEY})
    if st != 200:
        sys.exit(f"Firma HTTP {st}: {body[:600]}")
    res = json.loads(body)
    signed_b64 = res.get("signedXmlBase64")
    if not signed_b64:
        sys.exit(f"Firma sin signedXmlBase64: {body[:600]}")
    print(f"    Firma OK (signer={res.get('signer','?')}, {len(signed_b64)} b64 bytes)")
    firmado_path = os.path.abspath(args.xml).replace(".xml", "_FIRMADO.xml")
    open(firmado_path, "w", encoding="utf-8").write(base64.b64decode(signed_b64).decode("utf-8", "replace"))
    print("    Guardado:", os.path.basename(firmado_path))

    if not args.enviar:
        print("\nSOLO FIRMA (no se envió). Revisá el _FIRMADO.xml. Para enviar: agregá --enviar")
        return

    # 2) TOKEN
    print("\n[2/3] Token Hacienda (worker)...")
    st, body = _post_json(WORKER_URL.rstrip("/") + "/token", {"client_id": "api-prod"})
    tok = json.loads(body).get("access_token")
    if not tok:
        sys.exit(f"Sin token: {body[:600]}")
    print("    Token OK")

    # 3) SUBMIT  (comprobanteXml = base64 RAW del firmador, sin re-codificar)
    print("\n[3/3] Enviando a Hacienda...")
    st, body = _post_json(WORKER_URL.rstrip("/") + "/submit", {
        "token": tok,
        "clave": campos["clave"],
        "fecha": campos["fecha"],
        "emisor":   {"tipoIdentificacion": campos["emisor_tipo"],   "numeroIdentificacion": campos["emisor_num"]},
        "receptor": {"tipoIdentificacion": campos["receptor_tipo"], "numeroIdentificacion": campos["receptor_num"]},
        "comprobanteXml": signed_b64,
    })
    print(f"    HTTP {st}\n{body[:1200]}")
    if st in (200, 202):
        print("\nENVIADA. Verificá el estado (aceptada/rechazada) en unos segundos.")
        print(f"IMPORTANTE: si quedó aceptada, corré:  python3 scripts/firmar_enviar_nc.py --bump-consecutivo")
    else:
        print("\nNO se aceptó el envío. NO bumpees el consecutivo.")


if __name__ == "__main__":
    main()
