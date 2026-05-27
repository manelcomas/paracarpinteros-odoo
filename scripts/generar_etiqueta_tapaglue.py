#!/usr/bin/env python3
"""Genera etiquetas/tapa-glue.html — etiqueta TAPA-GLUE para impresora térmica.

Diseñada para impresoras térmicas de etiquetas 100×150 mm (10×15 cm,
formato shipping label estándar de Zebra, Xprinter, Rongta, etc.).

Salida: HTML monocromo (negro puro sobre blanco). Sin gradientes ni colores
suaves, porque las térmicas no los renderizan bien.

Para imprimir: abrir el HTML, Ctrl+P, tamaño 100×150mm, márgenes 0.
"""
from pathlib import Path

# ------------------- Datos del producto -------------------
PRODUCTO = {
    "marca": "TAPA-GLUE",
    "titulo": "PEGAMENTO GRANULADO HOTMELT",
    "subtitulo": "PARA TAPACANTOS",
    "base": "BASE EVA",
    "specs": [
        ("Contenido", "1 KG"),
        ("Temp. de Trabajo", "180°C - 210°C"),
        ("Viscosidad", "Media-Alta"),
        ("Color", "Translúcido"),
    ],
    # El código original (8809977900512) tiene checksum inválido.
    # Lo corregimos a 8809977900519 para que el lector lo acepte.
    # Para retail real, comprar EAN en GS1 Costa Rica.
    "ean13": "8809977900519",
}

# ------------------- Generador EAN-13 -------------------
L_CODE = {'0':'0001101','1':'0011001','2':'0010011','3':'0111101','4':'0100011',
          '5':'0110001','6':'0101111','7':'0111011','8':'0110111','9':'0001011'}
G_CODE = {'0':'0100111','1':'0110011','2':'0011011','3':'0100001','4':'0011101',
          '5':'0111001','6':'0000101','7':'0010001','8':'0001001','9':'0010111'}
R_CODE = {'0':'1110010','1':'1100110','2':'1101100','3':'1000010','4':'1011100',
          '5':'1001110','6':'1010000','7':'1000100','8':'1001000','9':'1110100'}
PARITY = {'0':'LLLLLL','1':'LLGLGG','2':'LLGGLG','3':'LLGGGL','4':'LGLLGG',
          '5':'LGGLLG','6':'LGGGLL','7':'LGLGLG','8':'LGLGGL','9':'LGGLGL'}


def ean13_check(code12: str) -> str:
    assert len(code12) == 12 and code12.isdigit()
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(code12))
    return code12 + str((10 - s % 10) % 10)


def ean13_bits(code: str) -> str:
    assert len(code) == 13 and code.isdigit()
    bits = "101"
    pattern = PARITY[code[0]]
    for i, c in enumerate(code[1:7]):
        bits += (L_CODE if pattern[i] == 'L' else G_CODE)[c]
    bits += "01010"
    for c in code[7:]:
        bits += R_CODE[c]
    bits += "101"
    assert len(bits) == 95
    return bits


def ean13_svg(code: str) -> str:
    """SVG escalable, alto contraste, optimizado para térmica."""
    bits = ean13_bits(code)
    guards = set(list(range(3)) + list(range(45, 50)) + list(range(92, 95)))
    bar_h = 70
    guard_extra = 8
    rects = []
    for i, b in enumerate(bits):
        if b == '1':
            h = bar_h + (guard_extra if i in guards else 0)
            rects.append(f'<rect x="{i}" y="0" width="1.05" height="{h}" fill="#000"/>')

    text_y = bar_h + guard_extra + 10
    left_centers = [7, 14, 21, 28, 35, 42]
    right_centers = [52, 59, 66, 73, 80, 87]
    texts = [f'<text x="-6" y="{text_y}" font-size="10" font-family="\'Courier New\', monospace" font-weight="bold">{code[0]}</text>']
    for x, c in zip(left_centers, code[1:7]):
        texts.append(f'<text x="{x}" y="{text_y}" font-size="10" font-family="\'Courier New\', monospace" font-weight="bold" text-anchor="middle">{c}</text>')
    for x, c in zip(right_centers, code[7:]):
        texts.append(f'<text x="{x}" y="{text_y}" font-size="10" font-family="\'Courier New\', monospace" font-weight="bold" text-anchor="middle">{c}</text>')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-8 -2 108 {text_y + 4}" class="barcode-svg" shape-rendering="crispEdges">
  <rect x="-8" y="-2" width="108" height="{text_y + 6}" fill="#fff"/>
  {chr(10).join(rects)}
  {chr(10).join(texts)}
</svg>'''


# ------------------- HTML monocromo para térmica -------------------
def build_html(p: dict) -> str:
    barcode = ean13_svg(p["ean13"])
    spec_rows = "\n".join(
        f'    <tr><th>{k}</th><td>{v}</td></tr>' for k, v in p["specs"]
    )

    return f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Etiqueta {p["marca"]} 10x15</title>
<style>
  /* ============ IMPRESIÓN TÉRMICA 100×150 mm ============ */
  @page {{
    size: 100mm 150mm;
    margin: 0;
  }}

  * {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  html, body {{ margin: 0; padding: 0; background: #ccc; }}

  .etiqueta {{
    width: 100mm;
    height: 150mm;
    background: #ffffff;
    color: #000;
    padding: 5mm;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    display: flex;
    flex-direction: column;
    page-break-after: always;
  }}

  /* HEADER — negro sólido, sin gradiente */
  .header {{
    background: #000;
    color: #fff;
    padding: 4mm 5mm;
    display: flex;
    align-items: center;
    gap: 4mm;
    border-radius: 2mm;
  }}
  .header .logo {{
    width: 16mm; height: 16mm;
    background: #fff;
    color: #000;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-weight: 900;
    font-size: 11mm;
    font-family: 'Arial Black', Impact, sans-serif;
    flex-shrink: 0;
    line-height: 1;
  }}
  .header h1 {{
    margin: 0;
    font-size: 9mm;
    font-weight: 900;
    letter-spacing: 0.3mm;
  }}

  /* Línea negra sólida en lugar del naranja */
  .franja {{
    height: 1.5mm;
    background: #000;
    margin: 3mm 0;
  }}

  .titulo {{
    font-size: 8.5mm;
    font-weight: 900;
    line-height: 1.05;
    color: #000;
    margin: 0 0 3mm 0;
  }}

  .subtitulo {{
    background: #000;
    color: #fff;
    font-weight: 800;
    font-size: 5mm;
    padding: 2mm 4mm;
    display: inline-block;
    letter-spacing: 0.3mm;
  }}

  .base {{
    margin-top: 2.5mm;
    font-size: 4.5mm;
    font-weight: 800;
    color: #000;
    letter-spacing: 0.5mm;
  }}

  table.specs {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 4mm;
    font-size: 3.8mm;
    border: 0.4mm solid #000;
  }}
  table.specs th,
  table.specs td {{
    border: 0.3mm solid #000;
    padding: 2mm 3mm;
    text-align: left;
    vertical-align: middle;
  }}
  table.specs th {{
    width: 42%;
    font-weight: 800;
    color: #000;
    background: #fff;
  }}
  table.specs td {{
    font-weight: 700;
    color: #000;
  }}

  .pie {{
    margin-top: auto;
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 4mm;
    padding-top: 3mm;
  }}
  .recycle {{
    font-size: 10mm;
    color: #000;
    line-height: 1;
    font-weight: 900;
  }}
  .barcode {{
    width: 55mm;
  }}
  .barcode .barcode-svg {{
    width: 100%;
    height: auto;
    display: block;
  }}

  @media screen {{
    body {{
      padding: 20px;
      display: flex;
      justify-content: center;
      align-items: flex-start;
      min-height: 100vh;
    }}
    .etiqueta {{
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
      outline: 1px dashed #999;
    }}
    .hint {{
      position: fixed;
      top: 10px; left: 10px;
      background: #000; color: #fff;
      padding: 10px 14px; border-radius: 6px;
      font-family: sans-serif; font-size: 12px;
      max-width: 320px; line-height: 1.5;
    }}
  }}
  @media print {{
    .hint {{ display: none; }}
    body {{ background: white; padding: 0; }}
    .etiqueta {{ outline: none; box-shadow: none; }}
  }}
</style>
</head>
<body>
  <div class="hint">
    <b>Térmica 10×15 cm</b><br>
    Ctrl/Cmd + P → impresora térmica<br>
    Tamaño: 100×150 mm (o 4×6")<br>
    Márgenes: <b>Ninguno / 0</b><br>
    Escala: <b>100%</b> (no "ajustar")<br>
    "Gráficos de fondo": <b>ON</b>
  </div>

  <div class="etiqueta">
    <div class="header">
      <div class="logo">T</div>
      <h1>{p["marca"]}</h1>
    </div>
    <div class="franja"></div>

    <div class="titulo">{p["titulo"]}</div>
    <span class="subtitulo">{p["subtitulo"]}</span>
    <div class="base">{p["base"]}</div>

    <table class="specs">
{spec_rows}
    </table>

    <div class="pie">
      <div class="recycle" title="Reciclable">♻</div>
      <div class="barcode">
        {barcode}
      </div>
    </div>
  </div>
</body>
</html>
'''


def main():
    repo = Path(__file__).resolve().parent.parent
    out = repo / "etiquetas" / "tapa-glue.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(PRODUCTO), encoding="utf-8")
    print(f"OK → {out}")
    print(f"   Formato: térmica 100×150 mm (monocromo)")
    print(f"   EAN-13: {PRODUCTO['ean13']}  (checksum válido)")


if __name__ == "__main__":
    main()
