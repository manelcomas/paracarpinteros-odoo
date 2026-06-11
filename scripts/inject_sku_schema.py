#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Añade el snippet PC-SKU-JSONLD al custom_code_footer del website 3 (ParaCarpinteros).

El snippet corre en las fichas de producto (/shop/*): lee la referencia interna
que Odoo ya imprime en el DOM ("Ref: A465") y la inyecta como `sku` en el JSON-LD
Product que Odoo 19 genera server-side (que no incluye sku de serie).

Idempotente: si el marcador pc-sku-jsonld ya está en el footer, no hace nada.
Hace backup local del footer en scripts/_backups/ antes de escribir.

Uso:
  python3 scripts/inject_sku_schema.py            # dry-run (muestra qué haría)
  python3 scripts/inject_sku_schema.py --apply    # aplica el cambio
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
WEBSITE_ID = 3
APPLY = "--apply" in sys.argv

MARCA = "pc-sku-jsonld"
SNIPPET = """
<script id="pc-sku-jsonld">
/* PC-SKU-JSONLD: anade sku al JSON-LD Product leyendo el "Ref:" nativo de la ficha */
(function(){
  function addSku(){
    if(!/^\\/shop\\/[^/]+/.test(location.pathname) || location.pathname.indexOf('/shop/category')===0) return;
    var ref=null;
    document.querySelectorAll('p.text-muted').forEach(function(p){
      var m=p.textContent.match(/^\\s*Ref:\\s*(\\S+)\\s*$/);
      if(m) ref=m[1];
    });
    if(!ref) return;
    document.querySelectorAll('script[type="application/ld+json"]').forEach(function(s){
      try{
        var data=JSON.parse(s.textContent);
        var arr=Array.isArray(data)?data:[data];
        var cambiado=false;
        arr.forEach(function(o){
          if(o && o['@type']==='Product' && !o.sku){ o.sku=ref; cambiado=true; }
        });
        if(cambiado) s.textContent=JSON.stringify(data);
      }catch(e){}
    });
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', addSku);
  else addSku();
})();
</script>
"""

if not KEY:
    sys.exit("Falta ODOO_API_KEY en el entorno (.env raíz).")

common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
uid = common.authenticate(DB, USER, KEY, {})
if not uid:
    sys.exit("Autenticación fallida (revisa ODOO_USER / ODOO_API_KEY).")
models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def call(model, method, *args, **kw):
    return models.execute_kw(DB, uid, KEY, model, method, list(args), kw)


w = call("website", "read", [WEBSITE_ID], fields=["name", "custom_code_footer"])[0]
footer = w.get("custom_code_footer") or ""
print(f"website {WEBSITE_ID} ({w['name']}): footer actual {len(footer)} bytes")

if MARCA in footer:
    print(f"✅ El marcador {MARCA} ya está en el footer. Nada que hacer.")
    sys.exit(0)

ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
bak_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backups")
os.makedirs(bak_dir, exist_ok=True)
bak = os.path.join(bak_dir, f"backup_website{WEBSITE_ID}_custom_code_footer_{ts}.html")

nuevo = footer.rstrip() + "\n" + SNIPPET
print(f"→ Se anexará el bloque {MARCA} ({len(SNIPPET)} bytes) al final del footer.")
print(f"→ Footer pasaría de {len(footer)} a {len(nuevo)} bytes.")

if APPLY:
    with open(bak, "w") as f:
        f.write(footer)
    print(f"→ Backup del footer actual: {bak}")
    call("website", "write", [WEBSITE_ID], {"custom_code_footer": nuevo})
    print("✅ Aplicado.")
else:
    print("\nDRY-RUN: nada escrito. Repetir con --apply para aplicar.")
