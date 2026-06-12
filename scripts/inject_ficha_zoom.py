#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Añade el snippet PC-FICHA-ZOOM al custom_code_footer del website 3 (ParaCarpinteros).

Hace clicables (zoom) las imágenes de las fichas técnicas insertadas en las
website_description (secciones con clase ficha-tecnica-*):
  - Desktop: lightbox a pantalla completa; click sobre la imagen alterna
    ajuste/100% (con scroll centrado en el punto clicado), Escape o click
    fuera cierra.
  - Móvil/táctil (pointer coarse): abre el PNG a resolución completa en
    pestaña nueva → pinch-zoom nativo del navegador.

Idempotente: si el marcador pc-ficha-zoom ya está en el footer, no hace nada.
Backup local del footer en scripts/_backups/ antes de escribir.

Uso:
  python3 scripts/inject_ficha_zoom.py            # dry-run
  python3 scripts/inject_ficha_zoom.py --apply    # aplica
"""
import datetime
import os
import sys
import xmlrpc.client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import load_project_env
load_project_env()

URL = os.environ["ODOO_URL"]
DB = os.environ["ODOO_DB"]
USER = os.environ.get("ODOO_USER") or os.environ.get("ODOO_USERNAME")
KEY = os.environ.get("ODOO_API_KEY")
WEBSITE_ID = 3
APPLY = "--apply" in sys.argv

MARCA = "pc-ficha-zoom"
SNIPPET = """
<script id="pc-ficha-zoom">
/* PC-FICHA-ZOOM: lightbox con zoom para las imagenes de fichas tecnicas (secciones ficha-tecnica-*) */
(function(){
  function abrir(src){
    var ov=document.createElement('div');
    ov.style.cssText='position:fixed;inset:0;z-index:10000;background:rgba(8,8,8,.95);overflow:auto;cursor:zoom-out;display:flex;align-items:center;justify-content:center';
    var img=document.createElement('img');
    img.src=src; img.alt='';
    img.style.cssText='max-width:95vw;max-height:95vh;display:block;margin:auto;cursor:zoom-in';
    var grande=false;
    img.addEventListener('click',function(e){
      e.stopPropagation();
      var rx=(e.clientX-img.getBoundingClientRect().left)/img.clientWidth;
      var ry=(e.clientY-img.getBoundingClientRect().top)/img.clientHeight;
      grande=!grande;
      if(grande){
        img.style.maxWidth='none'; img.style.maxHeight='none'; img.style.cursor='zoom-out';
        ov.style.display='block';
        ov.scrollLeft=rx*img.scrollWidth-ov.clientWidth/2;
        ov.scrollTop=ry*img.scrollHeight-ov.clientHeight/2;
      }else{
        img.style.maxWidth='95vw'; img.style.maxHeight='95vh'; img.style.cursor='zoom-in';
        ov.style.display='flex';
      }
    });
    function cerrar(){ ov.remove(); document.removeEventListener('keydown',esc); }
    function esc(e){ if(e.key==='Escape') cerrar(); }
    ov.addEventListener('click',cerrar);
    document.addEventListener('keydown',esc);
    ov.appendChild(img);
    document.body.appendChild(ov);
  }
  function init(){
    document.querySelectorAll('section[class*="ficha-tecnica-"] img').forEach(function(im){
      if(im.dataset.pcZoom) return;
      im.dataset.pcZoom='1';
      im.style.cursor='zoom-in';
      im.addEventListener('click',function(ev){
        ev.preventDefault();
        if(window.matchMedia('(pointer: coarse)').matches){ window.open(im.currentSrc||im.src,'_blank'); return; }
        abrir(im.currentSrc||im.src);
      });
    });
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init);
  else init();
})();
</script>
"""


def main():
    common = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    models = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/object", allow_none=True)

    def call(model, method, *args, **kw):
        return models.execute_kw(DB, uid, KEY, model, method, list(args), kw)

    web = call("website", "read", [WEBSITE_ID], fields=["name", "custom_code_footer"])[0]
    footer = web["custom_code_footer"] or ""
    if MARCA in footer:
        print(f"El footer del website {WEBSITE_ID} ya tiene {MARCA}; nada que hacer.")
        return

    print(f"Website {WEBSITE_ID} ({web['name']}): footer actual {len(footer)} chars; "
          f"se añadirían {len(SNIPPET)} chars.")
    if not APPLY:
        print("Dry-run. Ejecutar con --apply para escribir.")
        return

    bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bpath = os.path.join(bdir, f"website{WEBSITE_ID}_custom_code_footer_{stamp}.html")
    with open(bpath, "w") as f:
        f.write(footer)
    call("website", "write", [WEBSITE_ID], {"custom_code_footer": footer + SNIPPET})
    print(f"Aplicado. Backup del footer previo: {bpath}")


if __name__ == "__main__":
    main()
