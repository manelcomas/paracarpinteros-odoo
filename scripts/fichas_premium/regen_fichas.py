#!/usr/bin/env python3
"""
Regenera SOLO el ir.attachment de la ficha imprimible (render_print) de los premium,
sin tocar el website_description. Útil cuando cambia la plantilla de la ficha
(machote, logo, tamaños de letra) pero el contenido del producto no.

Usa el cache de IA (backup/ai_cache/) — la primera corrida puebla el cache llamando
a la API; las siguientes son gratis. El att_id no cambia (se actualiza por nombre),
así que el botón "Descargar ficha técnica" del website_description sigue válido.

Uso:
    python3 scripts/fichas_premium/regen_fichas.py            # todos los premium
    python3 scripts/fichas_premium/regen_fichas.py A814        # uno solo
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import (
    odoo_connect, parse_description, render_print,
    fetch_products, PRICE_PREMIUM_MIN,
)
from upload import load_backup_dir, upsert_ficha_attachment, ATT_NAME_PATTERN
from enrich import enrich_one


def regen_one(call, code, backup_map):
    ids = call('product.template', 'search', [[('default_code', '=', code)]])
    if not ids:
        print(f'  ✗ {code}: no existe'); return False
    p = call('product.template', 'read', [ids,
        ['id', 'name', 'default_code', 'list_price']])[0]
    source_html = backup_map.get(code)
    if source_html is None:
        print(f'  ⚠ {code}: no está en backup, salto'); return False
    parsed = parse_description(source_html)

    ai_extra = None
    try:
        r = enrich_one(call, code, verbose=False)  # cache o API
        if r and '_error' not in r:
            ai_extra = {k: v for k, v in r.items() if not k.startswith('_')}
    except Exception as e:
        print(f'  ⚠ {code}: IA falló ({e}), ficha sin enriquecer')

    print_html = render_print(p, parsed, ai_extra=ai_extra)
    att_id = upsert_ficha_attachment(call, p['id'], code, print_html)
    n_s = len(ai_extra.get('specs', [])) if ai_extra else 0
    print(f'  ✓ {code:<14} att={att_id} · {len(print_html)} chars · IA specs={n_s}')
    return True


def main():
    call = odoo_connect()
    backup_map = load_backup_dir('backup')
    code = sys.argv[1] if len(sys.argv) > 1 else None
    if code:
        return 0 if regen_one(call, code, backup_map) else 1
    rows = fetch_products(call, min_price=PRICE_PREMIUM_MIN)
    print(f'Regenerando fichas de {len(rows)} premium (attachment only)…')
    ok = fail = 0
    for r in rows:
        try:
            if regen_one(call, r['default_code'], backup_map):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f'  ✗ {r.get("default_code")}: {e}'); fail += 1
    print(f'\n{ok} OK · {fail} fallos')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
