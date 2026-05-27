#!/usr/bin/env python3
"""Recupera teléfonos de partners pisados por el bug del modal Pymex.

El modal panel-envios.html tenía un value="83777894" hardcoded en el input
m-telefono que no se reseteaba al abrir el modal. Cada vez que un operador
generaba una guía Pymex sin tocar manualmente el teléfono, ese valor (o el
último teléfono tipeado) se enviaba a Correos Y se escribía sobre el phone
del partner en Odoo vía syncPartnerIfChanged.

Este script:
1. Busca partners cuyo phone actual sea exactamente "83777894".
2. Para cada uno, consulta mail.tracking.value para encontrar el ÚLTIMO cambio
   donde new_value_char fue "83777894", y rescata el old_value_char (el phone
   original).
3. Muestra el plan. Con --apply restaura los teléfonos originales.

Uso:
    python3 scripts/recuperar_telefonos_overwriteados.py            # dry-run
    python3 scripts/recuperar_telefonos_overwriteados.py --apply    # escribe
"""
import sys
import os
import argparse
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _env import load_project_env
load_project_env()

BAD_PHONE = '83777894'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Escribe los cambios')
    parser.add_argument('--bad-phone', default=BAD_PHONE,
                        help=f'Phone string a buscar/reemplazar (default {BAD_PHONE})')
    args = parser.parse_args()

    url = os.environ['ODOO_URL']
    db = os.environ['ODOO_DB']
    user = os.environ['ODOO_USERNAME']
    key = os.environ['ODOO_API_KEY']
    common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
    uid = common.authenticate(db, user, key, {})
    models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

    # 1) Partners con phone actual = BAD_PHONE
    parts = models.execute_kw(db, uid, key, 'res.partner', 'search_read',
        [[('phone', '=', args.bad_phone)]],
        {'fields': ['id', 'name', 'phone', 'write_date'], 'order': 'write_date desc'})
    print(f'Partners con phone="{args.bad_phone}": {len(parts)}\n')

    plan = []
    skip = []
    for p in parts:
        # 2) Buscar el último mail.tracking.value para este partner donde
        #    new_value_char = BAD_PHONE y field es "Phone (Contact)"
        msgs = models.execute_kw(db, uid, key, 'mail.message', 'search_read',
            [[('model', '=', 'res.partner'),
              ('res_id', '=', p['id']),
              ('tracking_value_ids', '!=', False)]],
            {'fields': ['tracking_value_ids', 'date'],
             'order': 'date desc'})

        tv_ids = []
        for m in msgs:
            tv_ids.extend(m.get('tracking_value_ids') or [])
        if not tv_ids:
            skip.append((p['id'], p['name'], 'sin tracking'))
            continue

        tvs = models.execute_kw(db, uid, key, 'mail.tracking.value', 'read',
            [tv_ids], {'fields': ['field_id', 'old_value_char', 'new_value_char', 'create_date']})

        # Buscar el último tv que sea phone field y donde new_value = BAD_PHONE
        phone_tvs = [tv for tv in tvs
                     if tv.get('field_id') and 'phone' in (tv['field_id'][1] or '').lower()
                     and (tv.get('new_value_char') or '').strip() == args.bad_phone]
        if not phone_tvs:
            skip.append((p['id'], p['name'], 'no encontré change a 83777894'))
            continue

        # El más reciente (Odoo devuelve por create_date desc al leerlos por orden de creación)
        phone_tvs.sort(key=lambda t: t.get('create_date') or '', reverse=True)
        last = phone_tvs[0]
        old = (last.get('old_value_char') or '').strip()
        if not old or old == args.bad_phone:
            skip.append((p['id'], p['name'], f'old_value vacío o también 83777894 (no hay original)'))
            continue
        plan.append({
            'partner_id': p['id'],
            'name': p['name'],
            'restored_phone': old,
            'changed_at': last.get('create_date'),
        })

    print('PLAN — restaurar teléfonos:')
    for row in plan:
        print(f"  #{row['partner_id']:>5}  {row['name'][:36]:36}  "
              f"83777894  →  {row['restored_phone']}   ({row['changed_at']})")
    print(f'\nA restaurar: {len(plan)}')
    print(f'No-recuperables: {len(skip)}')
    for s in skip:
        print(f'  #{s[0]:>5} {s[1][:36]:36} — {s[2]}')

    if not args.apply:
        print('\n[DRY-RUN] No se escribió nada. Para ejecutar: --apply')
        return

    print(f'\n>>> Aplicando restauraciones...')
    ok = 0
    errs = []
    for row in plan:
        try:
            models.execute_kw(db, uid, key, 'res.partner', 'write',
                [[row['partner_id']], {'phone': row['restored_phone']}])
            ok += 1
        except Exception as e:
            errs.append((row['partner_id'], str(e)[:120]))
    print(f'>>> Hecho: {ok} restaurados, {len(errs)} errores')
    for pid, e in errs:
        print(f'  partner {pid}: {e}')


if __name__ == '__main__':
    main()
