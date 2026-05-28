#!/usr/bin/env python3
"""Sincroniza tarifas Tavo + Dual desde whatsapp-bot/zonas_dual.py hacia Odoo.

FUENTE ÚNICA DE VERDAD: whatsapp-bot/zonas_dual.py
  * Editás las tarifas o el mapeo de cantones ahí
  * Corrés este script para reflejar el cambio en delivery.carrier de Odoo
  * Rsync wa-bot al VPS para que el bot también las use

Sin este script las tarifas en Odoo (precio que ve el SO/factura) y las del
wa-bot (precio que cotiza al cliente) se desincronizan.

Uso:
    python3 scripts/cargar_tarifas_courier_2026.py            # dry-run
    python3 scripts/cargar_tarifas_courier_2026.py --apply    # escribe
"""
import sys
import os
import argparse
import xmlrpc.client
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'scripts'))
sys.path.insert(0, str(REPO_ROOT / 'whatsapp-bot'))

from _env import load_project_env  # type: ignore
load_project_env()

# Importar la fuente única
from zonas_dual import (  # type: ignore
    DUAL_TARIFFS,
    DUAL_CARRIER_ID_BY_ZONE,
    DUAL_CARRIER_NAME_BY_ZONE,
    TAVO_CARRIER_ID,
    TAVO_NAME,
    build_odoo_price_rules,
)


def fmt_rule(r: dict) -> str:
    return (f"seq={r['sequence']:>3} IF weight {r['operator']} {r['max_value']:.1f}  "
            f"→ base=₡{r['list_base_price']:>5.0f}  + ₡{r['list_price']:>4.0f}×{r['variable_factor']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    url = os.environ['ODOO_URL']; db = os.environ['ODOO_DB']
    user = os.environ['ODOO_USERNAME']; key = os.environ['ODOO_API_KEY']
    common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
    uid = common.authenticate(db, user, key, {})
    models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

    def kw(model, method, *args, **kwargs):
        return models.execute_kw(db, uid, key, model, method, list(args), kwargs)

    # ─── 1) Tavo (#10): renombrar para reflejar destinos ───
    tavo = kw('delivery.carrier', 'read', [TAVO_CARRIER_ID], fields=['name', 'fixed_price'])
    if tavo:
        cur = tavo[0]
        print(f"\n── TAVO #{TAVO_CARRIER_ID} ──")
        print(f"  ACTUAL: {cur['name']}")
        print(f"  ESPERADO: {TAVO_NAME}")
        if cur['name'] == TAVO_NAME:
            print(f"  ✓ ya está sincronizado")
        else:
            print(f"  (tarifas no cambian: ₡{cur['fixed_price']:.0f} hasta 15 kg)")
            if args.apply:
                kw('delivery.carrier', 'write', [TAVO_CARRIER_ID], {'name': TAVO_NAME})
                print(f"  ✓ renombrado")

    # ─── 2) Dual: 3 carriers (uno por zona) ───
    for zone in ('gam', 'intermedia', 'remota'):
        name = DUAL_CARRIER_NAME_BY_ZONE[zone]
        target_id = DUAL_CARRIER_ID_BY_ZONE.get(zone)
        new_rules = build_odoo_price_rules(zone)
        t = DUAL_TARIFFS[zone]

        # Encontrar el carrier por ID (preferido) o por nombre
        existing = []
        if target_id:
            existing = kw('delivery.carrier', 'read', [target_id],
                          fields=['id', 'name', 'price_rule_ids'])
        if not existing:
            ids_byname = kw('delivery.carrier', 'search', [('name', '=', name)])
            if ids_byname:
                existing = kw('delivery.carrier', 'read', [ids_byname[0]],
                              fields=['id', 'name', 'price_rule_ids'])

        print(f"\n── DUAL {zone.upper()} ({name}) ──")
        for r in new_rules:
            print(f"    {fmt_rule(r)}")

        if existing:
            cur = existing[0]
            print(f"  carrier #{cur['id']} (rules={len(cur['price_rule_ids'])})")
            if args.apply:
                # Limpiar reglas viejas
                if cur['price_rule_ids']:
                    kw('delivery.price.rule', 'unlink', cur['price_rule_ids'])
                # Actualizar metadata
                kw('delivery.carrier', 'write', [cur['id']], {
                    'name': name,
                    'delivery_type': 'base_on_rule',
                    'fixed_price': t['b_0_2'],
                    'active': True,
                })
                # Crear reglas nuevas
                for r in new_rules:
                    r2 = dict(r); r2['carrier_id'] = cur['id']
                    kw('delivery.price.rule', 'create', r2)
                print(f"  ✓ sincronizado #{cur['id']}")
        else:
            print(f"  no existe → crear")
            if args.apply:
                # Usar el product del carrier GAM #11 como base
                base_carrier = kw('delivery.carrier', 'read', [11], fields=['product_id'])
                prod_id = (base_carrier[0]['product_id'][0]
                           if base_carrier and base_carrier[0].get('product_id') else None)
                new_id = kw('delivery.carrier', 'create', {
                    'name': name,
                    'delivery_type': 'base_on_rule',
                    'fixed_price': t['b_0_2'],
                    'product_id': prod_id,
                    'active': True,
                })
                for r in new_rules:
                    r2 = dict(r); r2['carrier_id'] = new_id
                    kw('delivery.price.rule', 'create', r2)
                print(f"  ✓ creado #{new_id} — RECORDÁ actualizar DUAL_CARRIER_ID_BY_ZONE en zonas_dual.py con el ID {new_id}")

    if not args.apply:
        print("\n[DRY-RUN] No se escribió nada. Para aplicar: --apply")


if __name__ == '__main__':
    main()
