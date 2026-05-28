#!/usr/bin/env python3
"""
Sube la ficha re-empaquetada al website_description de Odoo (producción).

Uso:
    python3 scripts/fichas_premium/upload.py EXT-EMF9030       # un producto
    python3 scripts/fichas_premium/upload.py --all              # todos los premium (con confirmación)
    python3 scripts/fichas_premium/upload.py --rollback EXT-EMF9030 backup/website_descriptions_2026-05-28_HHMMSS.json
"""
import sys, os, json, base64, argparse
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import (
    odoo_connect, parse_description, detect_kpis, render, render_print,
    fetch_premium, fetch_products, BACKUP_DIR,
    PRICE_PREMIUM_MIN, PRICE_MEDIUM_MIN, PRICE_MEDIUM_MAX,
)

ATT_NAME_PATTERN = 'ficha-tecnica-{code}.html'


def upsert_ficha_attachment(call, product_id, code, print_html):
    """Crea o actualiza el ir.attachment con el HTML imprimible del producto.
    Devuelve el attachment_id para construir la URL pública.
    """
    name = ATT_NAME_PATTERN.format(code=code)
    domain = [
        ('res_model','=','product.template'),
        ('res_id','=',product_id),
        ('name','=',name),
    ]
    existing = call('ir.attachment','search',[domain])
    datas = base64.b64encode(print_html.encode('utf-8')).decode()
    vals = {
        'name': name,
        'datas': datas,
        'mimetype': 'text/html',
        'res_model': 'product.template',
        'res_id': product_id,
        'type': 'binary',
        'public': True,
    }
    if existing:
        att_id = existing[0]
        # No mandar res_model/res_id en update (Odoo a veces se queja por permisos del campo)
        call('ir.attachment','write',[[att_id], {'datas': datas, 'mimetype': 'text/html', 'public': True}])
    else:
        att_id = call('ir.attachment','create',[vals])
    return att_id


def load_backup_dir(dir_path):
    """Lee todos los JSON de backup en dir y devuelve dict {default_code: original_html}.
    Si hay duplicados, gana el del archivo más antiguo (el más cercano al original)."""
    files = sorted([f for f in os.listdir(dir_path) if f.endswith('.json')])
    mapping = {}
    for fn in files:
        with open(os.path.join(dir_path, fn)) as f:
            items = json.load(f)
        for it in items:
            code = it.get('default_code')
            if code and code not in mapping:  # primer (más antiguo) gana
                mapping[code] = it.get('website_description', '')
    return mapping


def upload_one(call, code, dry_run=False, from_backup_map=None, use_ai=False):
    ids = call('product.template', 'search', [[('default_code', '=', code)]])
    if not ids:
        print(f'  ✗ NO existe producto con código {code}')
        return False
    p = call('product.template', 'read', [ids,
        ['id', 'name', 'default_code', 'list_price', 'website_description', 'website_url']])[0]

    # Fuente del HTML para parsear: backup original si está, sino el actual de Odoo.
    # Crítico: si re-uploeamos sobre productos ya migrados, el parser ve nuestro propio
    # HTML renderizado y degrada el contenido. SIEMPRE usar backup en re-uploads.
    source_html = None
    if from_backup_map is not None:
        source_html = from_backup_map.get(code)
        if source_html is None:
            print(f'  ⚠ {code} no está en backup, salto')
            return False
    else:
        source_html = p.get('website_description') or ''

    parsed = parse_description(source_html)

    # IA enrichment (opcional)
    ai_extra = None
    if use_ai:
        from enrich import enrich_one as ai_enrich
        try:
            ai_extra = ai_enrich(call, code, verbose=False)
            if ai_extra and '_error' not in ai_extra:
                # Limpiar metadatos privados
                ai_extra = {k: v for k, v in ai_extra.items() if not k.startswith('_')}
            else:
                ai_extra = None
        except Exception as e:
            print(f'  ⚠ {code}: IA falló: {e}')
            ai_extra = None

    kpis = detect_kpis(parsed['specs'] + (ai_extra.get('specs', []) if ai_extra else []), p['name'])

    if dry_run:
        new_html_preview = render(p, parsed, kpis, ai_extra=ai_extra)
        ai_note = ' + IA' if ai_extra else ''
        print(f'  [dry-run] {code}: generaría {len(new_html_preview)} chars{ai_note}')
        return True

    # 1) Subir attachment con HTML imprimible (incluye IA si disponible)
    print_html = render_print(p, parsed, ai_extra=ai_extra)
    att_id = upsert_ficha_attachment(call, p['id'], code, print_html)
    ficha_url = f'/web/content/{att_id}?download=false&filename={ATT_NAME_PATTERN.format(code=code)}'

    # 2) Renderizar website_description con botón apuntando al attachment
    new_html = render(p, parsed, kpis, ficha_url=ficha_url, ai_extra=ai_extra)

    # 3) Escribir website_description
    call('product.template', 'write', [[p['id']], {'website_description': new_html}])

    url = p.get('website_url') or f'/odoo/inventory/{p["id"]}'
    ai_summary = ''
    if ai_extra:
        n_s = len(ai_extra.get('specs', []))
        n_a = len(ai_extra.get('applications', []))
        n_n = len(ai_extra.get('operation_notes', []))
        ai_summary = f' · IA: +{n_s}specs +{n_a}apps +{n_n}notas'
    print(f'  ✓ {code:<14} (id={p["id"]}, att={att_id}) → {len(new_html)} chars · ficha {len(print_html)} chars{ai_summary}')
    return True


def rollback(call, code, backup_path):
    with open(backup_path) as f:
        items = json.load(f)
    item = next((x for x in items if x.get('default_code') == code), None)
    if not item:
        print(f'  ✗ {code} no está en backup {backup_path}')
        return False
    call('product.template', 'write', [[item['id']], {'website_description': item['website_description']}])
    print(f'  ✓ rollback {code} (id={item["id"]}) restaurado desde {os.path.basename(backup_path)}')
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('code', nargs='?', help='Default code de un producto')
    g.add_argument('--all', action='store_true', help='Subir TODOS los premium (>=₡100k). Pide confirmación.')
    g.add_argument('--tier-medium', action='store_true', help='Subir el tier medio (₡20k-₡100k). Pide confirmación.')
    g.add_argument('--range', nargs=2, type=int, metavar=('MIN','MAX'), help='Rango personalizado de precio.')
    g.add_argument('--rollback', nargs=2, metavar=('CODE','BACKUP_JSON'), help='Restaurar desde un backup')
    g.add_argument('--rollback-all', metavar='BACKUP_JSON', help='Restaurar TODOS los productos del backup')
    ap.add_argument('--dry-run', action='store_true', help='No escribir, solo simular')
    ap.add_argument('--yes', action='store_true', help='Sin confirmación (peligroso)')
    ap.add_argument('--from-backup-dir', metavar='DIR', help='Leer HTML original desde JSONs en DIR (en lugar del Odoo actual). Crítico para re-uploads.')
    ap.add_argument('--use-ai', action='store_true', help='Enriquecer specs/aplicaciones/notas con Claude multimodal viendo la foto del producto')
    args = ap.parse_args()

    call = odoo_connect()

    # Cargar mapa de backup si se pidió (--from-backup-dir)
    from_backup_map = None
    if args.from_backup_dir:
        d = args.from_backup_dir
        if not os.path.isabs(d):
            d = os.path.join(BACKUP_DIR, '..', d) if not d.startswith('backup') else os.path.join(os.path.dirname(BACKUP_DIR), d)
        if not os.path.isdir(d):
            d = BACKUP_DIR  # fallback al dir estándar
        from_backup_map = load_backup_dir(d)
        print(f'  cargado backup: {len(from_backup_map)} productos desde {d}')

    if args.rollback:
        code, backup = args.rollback
        if not os.path.isabs(backup):
            backup = os.path.join(BACKUP_DIR, os.path.basename(backup))
        return 0 if rollback(call, code, backup) else 1

    if args.rollback_all:
        backup = args.rollback_all
        if not os.path.isabs(backup):
            backup = os.path.join(BACKUP_DIR, os.path.basename(backup))
        with open(backup) as f:
            items = json.load(f)
        print(f'Rollback masivo de {len(items)} productos desde {os.path.basename(backup)}.')
        if not args.yes:
            ans = input('Escribí "si" para confirmar: ').strip().lower()
            if ans != 'si':
                print('Cancelado'); return 1
        ok = fail = 0
        for it in items:
            if rollback(call, it.get('default_code'), backup):
                ok += 1
            else:
                fail += 1
        print(f'\n{ok} OK · {fail} fallos')
        return 0 if fail == 0 else 1

    # Determinar rango por flag
    mn = mx = None
    label = None
    if args.all:
        mn, mx, label = PRICE_PREMIUM_MIN, None, f'premium (>=₡{PRICE_PREMIUM_MIN:,})'
    elif args.tier_medium:
        mn, mx, label = PRICE_MEDIUM_MIN, PRICE_MEDIUM_MAX, f'medio (₡{PRICE_MEDIUM_MIN:,}-₡{PRICE_MEDIUM_MAX:,})'
    elif args.range:
        mn, mx = args.range
        label = f'rango personalizado ₡{mn:,}-₡{mx:,}'

    if label:
        print(f'Subir productos en el tier {label} — esto modifica producción.')
        rows = fetch_products(call, min_price=mn, max_price=mx)
        if not rows:
            print('No hay productos en ese rango.'); return 1
        # Backup antes de bulk (omitir si estamos re-uploeando desde backup — sería backup del estado degradado)
        if from_backup_map:
            print('  saltando backup automático (usando --from-backup-dir)')
        else:
            ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
            backup_file = os.path.join(BACKUP_DIR, f'website_descriptions_pre-{ts}.json')
            with open(backup_file, 'w', encoding='utf-8') as f:
                # re-leer con website_description para tener el backup completo
                ids_chk = [r['id'] for r in rows]
                BATCH = 50
                backup_rows = []
                for i in range(0, len(ids_chk), BATCH):
                    backup_rows += call('product.template','read',[ids_chk[i:i+BATCH],
                        ['id','default_code','name','list_price','website_description']])
                json.dump([{
                    'id': r['id'],'default_code': r.get('default_code'),
                    'name': r['name'],'list_price': r['list_price'],
                    'website_description': r.get('website_description') or '',
                } for r in backup_rows], f, ensure_ascii=False, indent=2)
            print(f'  backup → {backup_file} ({len(rows)} productos)')

        if not args.yes:
            ans = input(f'\nSe van a actualizar {len(rows)} productos. Escribí "si" para confirmar: ').strip().lower()
            if ans != 'si':
                print('Cancelado'); return 1
        ok = fail = 0
        for r in rows:
            try:
                if upload_one(call, r['default_code'], dry_run=args.dry_run, from_backup_map=from_backup_map, use_ai=args.use_ai):
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                print(f'  ✗ {r.get("default_code")} EXCEPCIÓN: {e}')
                fail += 1
        print(f'\n{ok} OK · {fail} fallos')
        return 0 if fail == 0 else 1

    if args.code:
        return 0 if upload_one(call, args.code, dry_run=args.dry_run, from_backup_map=from_backup_map, use_ai=args.use_ai) else 1

    ap.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
