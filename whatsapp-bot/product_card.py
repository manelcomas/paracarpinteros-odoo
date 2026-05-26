"""Generador de tarjeta visual de producto Paracarpinteros.

Layout 1080 x 1350 (portrait 4:5, óptimo para WhatsApp):
- Banda naranja superior con "Ref: XXX"
- Cuadro negro central: logo "LA JUGUETERIA" arriba izquierda + foto del producto centrada
- Banda naranja inferior: nombre del producto (1-2 líneas) + PRECIO + valor en colones
- Pie blanco con flecha + "paracarpinteros.com"

Uso:
  from product_card import generate_card_bytes
  png_bytes = generate_card_bytes(product_image_bytes, code, name, price_crc)
"""
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1080, 1350
ORANGE = (242, 101, 34)
BLACK = (10, 10, 10)
WHITE = (255, 255, 255)

# Paths configurables vía env o hardcoded
LOGO_CANDIDATES = [
    "/app/static/logo_paracarpinteros.png",
    str(Path(__file__).parent / "static" / "logo_paracarpinteros.png"),
]

# Fuentes (buscar en orden, primera que cargue gana)
_FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:\\Windows\\Fonts\\segoeuib.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]
_FONT_BLACK = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:\\Windows\\Fonts\\seguibl.ttf",
    "C:\\Windows\\Fonts\\impact.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]

_LOGO_CACHE = None


def _font(candidates, size):
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _font_bold(size):
    return _font(_FONT_BOLD, size)


def _font_black(size):
    return _font(_FONT_BLACK, size)


def _load_logo():
    global _LOGO_CACHE
    if _LOGO_CACHE is not None:
        return _LOGO_CACHE
    for path in LOGO_CANDIDATES:
        try:
            if Path(path).exists():
                _LOGO_CACHE = Image.open(path).convert("RGBA")
                return _LOGO_CACHE
        except Exception:
            continue
    return None


def _format_crc(amount):
    """Formato colones CR: ₡ 270.000,00"""
    s = f"{amount:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"₡ {s}"  # ₡


def _wrap(draw, text, font, max_w):
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if (bbox[2] - bbox[0]) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def generate_card_bytes(product_image_bytes: bytes, code: str, name: str, price_crc: float) -> bytes:
    """Genera la tarjeta y devuelve PNG en bytes."""
    canvas = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(canvas)

    # Banner superior naranja con Ref
    TOP_BAR_H = 92
    draw.rectangle((0, 0, WIDTH, TOP_BAR_H), fill=ORANGE)
    ref_text = f"Ref: {code or '-'}"
    font_ref = _font_black(48)
    bbox = draw.textbbox((0, 0), ref_text, font=font_ref)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        (WIDTH - tw - 48 - bbox[0], (TOP_BAR_H - th) // 2 - bbox[1]),
        ref_text, fill=WHITE, font=font_ref,
    )

    # Cuadro negro con foto
    BLACK_TOP = TOP_BAR_H + 18
    BLACK_H = 820
    BLACK_LEFT = 48
    BLACK_RIGHT = WIDTH - 48
    BLACK_BOTTOM = BLACK_TOP + BLACK_H
    draw.rectangle((BLACK_LEFT, BLACK_TOP, BLACK_RIGHT, BLACK_BOTTOM), fill=BLACK)

    # Logo arriba izquierda
    logo = _load_logo()
    if logo:
        try:
            max_logo_w = 320
            ratio = max_logo_w / logo.width
            new_size = (max_logo_w, int(logo.height * ratio))
            logo_resized = logo.resize(new_size, Image.LANCZOS)
            canvas.paste(logo_resized, (BLACK_LEFT + 40, BLACK_TOP + 40), logo_resized)
        except Exception as e:
            print(f"[product_card logo err] {e}")

    # Foto del producto centrada
    try:
        prod = Image.open(BytesIO(product_image_bytes)).convert("RGBA")
        max_prod_w = BLACK_RIGHT - BLACK_LEFT - 120
        max_prod_h = BLACK_H - 200
        prod.thumbnail((max_prod_w, max_prod_h), Image.LANCZOS)
        pw, ph = prod.size
        px = BLACK_LEFT + (BLACK_RIGHT - BLACK_LEFT - pw) // 2
        py = BLACK_TOP + 180 + ((BLACK_H - 200 - ph) // 2)
        canvas.paste(prod, (px, py), prod if prod.mode == "RGBA" else None)
    except Exception as e:
        print(f"[product_card prod img err] {e}")

    # Banner inferior naranja con nombre + precio
    BANNER_TOP = BLACK_BOTTOM
    BANNER_BOTTOM = HEIGHT - 90
    draw.rectangle((0, BANNER_TOP, WIDTH, BANNER_BOTTOM), fill=ORANGE)

    font_name = _font_bold(46)
    name_lines = _wrap(draw, name or "", font_name, WIDTH - 96)[:2]
    if len(name_lines) == 2 and len(name_lines[1]) > 35:
        name_lines[1] = name_lines[1][:33].rstrip() + "…"  # …
    y = BANNER_TOP + 26
    for line in name_lines:
        draw.text((48, y), line, fill=WHITE, font=font_name)
        y += 56

    font_price_label = _font_bold(32)
    y_price_label = y + 16
    draw.text((48, y_price_label), "PRECIO", fill=WHITE, font=font_price_label)

    font_price = _font_black(92)
    price_str = _format_crc(price_crc or 0)
    y_price_value = y_price_label + 44
    draw.text((48, y_price_value), price_str, fill=WHITE, font=font_price)

    # Pie blanco con flecha + dominio
    cta_text = "paracarpinteros.com"
    font_cta = _font_bold(40)
    bbox = draw.textbbox((0, 0), cta_text, font=font_cta)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    cta_y_center = BANNER_BOTTOM + 90 // 2
    arrow_size = 22
    arrow_x = (WIDTH - tw - 50) // 2
    draw.polygon([
        (arrow_x, cta_y_center - arrow_size),
        (arrow_x + arrow_size, cta_y_center),
        (arrow_x, cta_y_center + arrow_size),
    ], fill=ORANGE)
    draw.text(
        (arrow_x + arrow_size + 20 - bbox[0], cta_y_center - th // 2 - bbox[1]),
        cta_text, fill=BLACK, font=font_cta,
    )

    # Exportar a PNG bytes (compresión optimizada)
    out = BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()
