#!/usr/bin/env python3
"""Rellena x_studio_canton_cr y x_studio_distrito_cr en partners CR.

Para partners que tienen ZIP válido pero los Studio fields vacíos, busca el
distrito en la tabla maestra x_distrito_cr usando x_studio_zip y escribe
cantón + distrito en el partner.

Modo dry-run por defecto (no escribe nada). Usar --apply para ejecutar.

Uso:
    python3 scripts/rellenar_canton_distrito_partners.py            # dry-run
    python3 scripts/rellenar_canton_distrito_partners.py --apply    # escribe
    python3 scripts/rellenar_canton_distrito_partners.py --apply --limit 20
"""
import sys
import os
import argparse
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _env import load_project_env
load_project_env()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='Ejecutar writes en Odoo. Por defecto solo simula.')
    parser.add_argument('--limit', type=int, default=0,
                        help='Limitar a N partners (0 = todos).')
    args = parser.parse_args()

    url = os.environ['ODOO_URL']
    db = os.environ['ODOO_DB']
    user = os.environ['ODOO_USERNAME']
    key = os.environ['ODOO_API_KEY']

    common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
    uid = common.authenticate(db, user, key, {})
    if not uid:
        print('ERROR: no se pudo autenticar')
        sys.exit(1)
    models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

    # 1) Construir índice ZIP → (distrito_id, canton_id) desde la tabla maestra
    print('Cargando tabla maestra x_distrito_cr...')
    master = models.execute_kw(db, uid, key, 'x_distrito_cr', 'search_read',
        [[('x_studio_zip', '!=', False)]],
        {'fields': ['id', 'x_name', 'x_studio_zip', 'x_studio_canton_cr']})

    zip_to_dist = {}  # zip -> (distrito_id, distrito_name, canton_id, canton_name)
    for d in master:
        z = (d.get('x_studio_zip') or '').strip()
        if not z or len(z) != 5:
            continue
        cant = d.get('x_studio_canton_cr')
        if not cant:
            continue
        zip_to_dist[z] = (d['id'], d.get('x_name') or '', cant[0], cant[1])
    print(f'  {len(zip_to_dist)} distritos con ZIP en master.')

    # 2) Buscar partners candidatos: CR + cliente + sin cantón + con zip
    domain = [
        ('country_id.code', '=', 'CR'),
        ('customer_rank', '>', 0),
        ('x_studio_canton_cr', '=', False),
        ('zip', '!=', False),
    ]
    print('\nBuscando partners sin cantón pero con zip...')
    fields = ['id', 'name', 'zip', 'state_id', 'x_studio_canton_cr',
              'x_studio_distrito_cr', 'city']
    kwargs = {'fields': fields, 'order': 'id'}
    if args.limit:
        kwargs['limit'] = args.limit
    partners = models.execute_kw(db, uid, key, 'res.partner', 'search_read',
        [domain], kwargs)
    print(f'  {len(partners)} candidatos.\n')

    # 3) Planificar cambios
    plan = []
    skipped_invalid_zip = 0
    skipped_no_match = 0
    for p in partners:
        z = (p.get('zip') or '').strip().replace(' ', '').replace('-', '')
        if not z.isdigit() or len(z) != 5:
            skipped_invalid_zip += 1
            continue
        match = zip_to_dist.get(z)
        if not match:
            skipped_no_match += 1
            continue
        dist_id, dist_name, cant_id, cant_name = match
        plan.append({
            'partner_id': p['id'],
            'partner_name': p.get('name') or f'(sin nombre #{p["id"]})',
            'zip': z,
            'canton_id': cant_id,
            'canton_name': cant_name,
            'distrito_id': dist_id,
            'distrito_name': dist_name,
        })

    # 4) Reporte
    print(f'PLAN: {len(plan)} partners a actualizar')
    print(f'  saltados (zip inválido): {skipped_invalid_zip}')
    print(f'  saltados (zip sin match en master): {skipped_no_match}\n')

    # Mostrar primeros 15 ejemplos
    for row in plan[:15]:
        print(f'  #{row["partner_id"]:>5} {row["partner_name"][:35]:35} '
              f'ZIP {row["zip"]} → {row["distrito_name"]} ({row["canton_name"]})')
    if len(plan) > 15:
        print(f'  ... y {len(plan) - 15} más')

    if not args.apply:
        print('\n[DRY-RUN] No se escribió nada. Para ejecutar: --apply')
        return

    # 5) Aplicar en batches de 50 (write con misma data, IDs distintos = N llamadas)
    print(f'\n>>> Aplicando cambios en Odoo ({len(plan)} writes)...')
    ok = 0
    errors = []
    for i, row in enumerate(plan, 1):
        try:
            models.execute_kw(db, uid, key, 'res.partner', 'write',
                [[row['partner_id']], {
                    'x_studio_canton_cr': row['canton_id'],
                    'x_studio_distrito_cr': row['distrito_id'],
                }])
            ok += 1
            if i % 25 == 0:
                print(f'  {i}/{len(plan)}...')
        except Exception as e:
            errors.append((row['partner_id'], str(e)[:120]))

    print(f'\n>>> Hecho: {ok} OK, {len(errors)} errores')
    for pid, e in errors[:10]:
        print(f'  partner {pid}: {e}')


if __name__ == '__main__':
    main()
