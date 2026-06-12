#!/usr/bin/env python3
"""Genera las fichas técnicas (HTML 1280x960, estilo A1172: fondo oscuro, cotas
ámbar) de la familia "rodamientos guía para fresas de router".

Datos 100% derivados del título del producto (Ø interior x Ø exterior, ambos
fracción de pulgada exacta) — nada inventado: ni material ni ancho (no constan).

    python3 fichas-tecnicas/_gen_rodamientos.py   # escribe rodamientos/ficha-*.html

Luego se rasterizan a PNG con un navegador (1280x960) y se suben a Odoo con
scripts/aplicar_fichas_rodamientos.py.
"""
import os

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(THIS_DIR, "rodamientos")

# (ref, template_id, d_int_mm, frac_int, d_ext_mm, frac_ext, presentacion)
PRODUCTOS = [
    ("A649", 1429, 4.76, '3/16"', 12.7, '1/2"', "Individual"),
    ("A653", 2583, 12.7, '1/2"', 19.05, '3/4"', None),
    ("A1093", 6333, 12.7, '1/2"', 19.05, '3/4"', "Set de 3 pzs"),
    ("A654", 1383, 4.76, '3/16"', 12.7, '1/2"', "Set de 5 pzs"),
    ("A655", 1384, 4.76, '3/16"', 15.9, '5/8"', "Set de 3 pzs"),
    ("A656", 1386, 4.76, '3/16"', 9.52, '3/8"', "Set de 5 pzs"),
    ("A658", 1385, 4.76, '3/16"', 19.05, '3/4"', "Set de 3 pzs"),
]

AMBAR = "#F5A800"
GRIS = "#9B9A97"
BLANCO = "#F2F0EC"


def fmt_mm(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def svg_frontal(d_int, d_ext):
    """Vista frontal: anillos concéntricos a proporción real + cotas Ø."""
    R = 150.0                       # radio exterior en px
    r_bore = R * (d_int / d_ext)    # agujero, proporción real
    r_race = r_bore + (R - r_bore) * 0.42   # separación pista interior/exterior (esquemático)
    cx, cy = 230, 230
    return f"""<svg width="425" height="480" viewBox="0 0 460 520" xmlns="http://www.w3.org/2000/svg" font-family="inherit">
  <defs><marker id="fl" markerWidth="10" markerHeight="10" refX="5" refY="5" orient="auto">
    <path d="M1,1 L9,5 L1,9" fill="none" stroke="{AMBAR}" stroke-width="1.4"/></marker></defs>
  <text x="{cx}" y="28" text-anchor="middle" fill="{GRIS}" font-size="15" letter-spacing="2">VISTA FRONTAL</text>
  <g fill="none" stroke="{BLANCO}" stroke-width="2">
    <circle cx="{cx}" cy="{cy}" r="{R}"/>
    <circle cx="{cx}" cy="{cy}" r="{r_race:.1f}" stroke-width="1.2" stroke="{GRIS}" stroke-dasharray="5 4"/>
    <circle cx="{cx}" cy="{cy}" r="{r_bore:.1f}"/>
  </g>
  <line x1="{cx - R}" y1="{cy}" x2="{cx - r_bore:.1f}" y2="{cy}" stroke="{GRIS}" stroke-width="0.8" stroke-dasharray="3 4"/>
  <line x1="{cx + r_bore:.1f}" y1="{cy}" x2="{cx + R}" y2="{cy}" stroke="{GRIS}" stroke-width="0.8" stroke-dasharray="3 4"/>
  <g stroke="{AMBAR}" stroke-width="1.3">
    <line x1="{cx - r_bore + 4:.1f}" y1="{cy}" x2="{cx + r_bore - 4:.1f}" y2="{cy}"
          marker-start="url(#fl)" marker-end="url(#fl)"/>
    <line x1="{cx - R + 4}" y1="{cy + R + 36}" x2="{cx + R - 4}" y2="{cy + R + 36}"
          marker-start="url(#fl)" marker-end="url(#fl)"/>
    <line x1="{cx - R}" y1="{cy + 8}" x2="{cx - R}" y2="{cy + R + 44}" stroke-dasharray="3 4" stroke-width="0.8"/>
    <line x1="{cx + R}" y1="{cy + 8}" x2="{cx + R}" y2="{cy + R + 44}" stroke-dasharray="3 4" stroke-width="0.8"/>
  </g>
  <text x="{cx}" y="{cy - 14}" text-anchor="middle" fill="{AMBAR}" font-size="17" font-weight="600"
        stroke="#161616" stroke-width="8" paint-order="stroke">Ø {fmt_mm(d_int)} MM · {{d_int_frac_holder}}</text>
  <text x="{cx}" y="{cy + R + 66}" text-anchor="middle" fill="{AMBAR}" font-size="17" font-weight="600">Ø {fmt_mm(d_ext)} MM · {{d_ext_frac_holder}}</text>
</svg>"""


def svg_lateral():
    """Vista lateral esquemática (sin cota de ancho: no consta en el título)."""
    return f"""<svg width="222" height="480" viewBox="0 0 240 520" xmlns="http://www.w3.org/2000/svg" font-family="inherit">
  <text x="120" y="28" text-anchor="middle" fill="{GRIS}" font-size="15" letter-spacing="2">VISTA LATERAL</text>
  <g fill="none" stroke="{BLANCO}" stroke-width="2">
    <rect x="85" y="80" width="70" height="300" rx="8"/>
    <line x1="85" y1="155" x2="155" y2="155" stroke="{GRIS}" stroke-width="1.2"/>
    <line x1="85" y1="305" x2="155" y2="305" stroke="{GRIS}" stroke-width="1.2"/>
  </g>
  <line x1="120" y1="62" x2="120" y2="398" stroke="{GRIS}" stroke-width="0.8" stroke-dasharray="8 5"/>
  <text x="120" y="430" text-anchor="middle" fill="{GRIS}" font-size="13">eje de la fresa</text>
</svg>"""


def ficha_html(ref, d_int, frac_int, d_ext, frac_ext, presentacion):
    frontal = svg_frontal(d_int, d_ext) \
        .replace("{d_int_frac_holder}", frac_int.replace('"', "&#8243;")) \
        .replace("{d_ext_frac_holder}", frac_ext.replace('"', "&#8243;"))
    celdas = [
        (f"Ø {fmt_mm(d_int)} MM / {frac_int}", "diámetro interior (eje)"),
        (f"Ø {fmt_mm(d_ext)} MM / {frac_ext}", "diámetro exterior"),
    ]
    if presentacion:
        celdas.append((presentacion.upper(), "presentación"))
    celdas.append(("RODAMIENTO GUÍA", "para fresas de router"))
    celdas_html = "\n".join(
        f'<div class="celda"><div class="v">{v}</div><div class="k">{k}</div></div>'
        for v, k in celdas)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ overflow:hidden; }}
  body {{ width:1280px; height:960px; background:#161616; color:{BLANCO};
         font-family:'Inter',sans-serif; padding:48px 64px 30px; display:flex; flex-direction:column; }}
  h1, .marca, .v {{ font-family:'Oswald',sans-serif; text-transform:uppercase; }}
  header {{ display:flex; justify-content:space-between; align-items:flex-start;
            border-bottom:1px solid #2c2c2c; padding-bottom:26px; }}
  h1 {{ font-size:44px; font-weight:600; letter-spacing:1px; }}
  .sub {{ color:{AMBAR}; font-family:'Oswald',sans-serif; font-size:21px; letter-spacing:2px;
          margin-top:10px; text-transform:uppercase; }}
  .marca {{ color:{AMBAR}; font-size:26px; font-weight:600; letter-spacing:2px; }}
  main {{ flex:1; display:flex; align-items:center; justify-content:center; gap:120px; }}
  main svg {{ font-family:'Oswald',sans-serif; }}
  footer {{ border-top:1px solid #2c2c2c; padding-top:22px; }}
  .celdas {{ display:flex; justify-content:space-between; gap:32px; }}
  .celda .v {{ font-size:27px; font-weight:600; letter-spacing:1px; }}
  .celda .k {{ color:{GRIS}; font-size:15px; margin-top:6px; }}
  .pie {{ text-align:center; color:#5d5c59; font-size:13px; margin-top:16px; }}
</style></head>
<body>
  <header>
    <div>
      <h1>Rodamiento guía para fresas</h1>
      <div class="sub">REF {ref} · Ø INT {frac_int.replace('"', "&#8243;")} · Ø EXT {frac_ext.replace('"', "&#8243;")}</div>
    </div>
    <div class="marca">Paracarpinteros</div>
  </header>
  <main>{frontal}{svg_lateral()}</main>
  <footer>
    <div class="celdas">{celdas_html}</div>
    <div class="pie">paracarpinteros.com · medidas del fabricante · proporciones del dibujo a escala real Ø int/Ø ext</div>
  </footer>
</body></html>"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for ref, tmpl_id, d_int, frac_int, d_ext, frac_ext, pres in PRODUCTOS:
        ruta = os.path.join(OUT_DIR, f"ficha-{ref}.html")
        with open(ruta, "w") as f:
            f.write(ficha_html(ref, d_int, frac_int, d_ext, frac_ext, pres))
        print(f"{ref} (template {tmpl_id}) -> {ruta}")


if __name__ == "__main__":
    main()
