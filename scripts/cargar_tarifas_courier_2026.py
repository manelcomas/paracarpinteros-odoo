#!/usr/bin/env python3
"""Actualiza carriers Tavo + Dual en Odoo con las tarifas vigentes mayo 2026.

Tavo (#10): renombra para incluir los 7 destinos del Caribe en el nombre
visible. Tarifas se mantienen (₡2.500 hasta 15 kg, ₡5.000 más).

Dual Global: divide en 3 carriers por zona (GAM / Intermedia / Remota), cada
uno con 4 reglas de peso (0-2 / 2-5 / 5-10 / +10 kg). El wa-bot deriva la
zona desde el cantón del partner (módulo whatsapp-bot/zonas_dual.py). Aquí en
Odoo, las 3 carriers existen para que el SO refleje el precio correcto
según la zona elegida en la venta.

- #11 'Dual Global' se renombra a 'Dual Global - GAM' (mantiene el ID para
  no romper SOs históricos) y se le actualizan las reglas a tarifas GAM.
- Se crean #12 'Dual Global - Intermedia' y #13 'Dual Global - Remota'.

Uso:
    python3 scripts/cargar_tarifas_courier_2026.py            # dry-run
    python3 scripts/cargar_tarifas_courier_2026.py --apply    # escribe
"""
import sys
import os
import argparse
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _env import load_project_env
load_project_env()


TAVO_CARRIER_ID = 10
DUAL_GAM_ID = 11  # existente, se renombra

TAVO_NEW_NAME = ('Envío Transtusa Turrialba → Caribe '
                 '(Guápiles, Limón, Pócora, Matina, Siquirres, Cariari, Pto Viejo)')

# Tarifas Dual por zona (CRC). Espejo de whatsapp-bot/zonas_dual.py:DUAL_TARIFFS
DUAL_ZONES = {
    'GAM': {
        'name':        'Dual Global - GAM',
        'b_0_2':       2000,
        'b_2_5':       2700,
        'b_5_10':      3900,
        'over10_base': 3900,
        'over10_kg':    450,
    },
    'Intermedia': {
        'name':        'Dual Global - Intermedia',
        'b_0_2':       2300,
        'b_2_5':       3200,
        'b_5_10':      5200,
        'over10_base': 5200,
        'over10_kg':    550,
    },
    'Remota': {
        'name':        'Dual Global - Remota',
        'b_0_2':       2500,
        'b_2_5':       3700,
        'b_5_10':      6500,
        'over10_base': 6500,
        'over10_kg':    650,
    },
}


def build_rules(z: dict) -> list:
    """4 reglas de peso. Para +10 kg el base se ajusta para que el total sea
    over10_base + (peso-10) * over10_kg cuando Odoo computa
    price = list_base_price + list_price * weight."""
    over10_offset = z['over10_base'] - 10 * z['over10_kg']
    return [
        {'sequence': 10, 'variable': 'weight', 'operator': '<=', 'max_value': 2.0,
         'list_base_price': z['b_0_2'],     'list_price': 0,            'variable_factor': 'weight'},
        {'sequence': 20, 'variable': 'weight', 'operator': '<=', 'max_value': 5.0,
         'list_base_price': z['b_2_5'],     'list_price': 0,            'variable_factor': 'weight'},
        {'sequence': 30, 'variable': 'weight', 'operator': '<=', 'max_value': 10.0,
         'list_base_price': z['b_5_10'],    'list_price': 0,            'variable_factor': 'weight'},
        {'sequence': 40, 'variable': 'weight', 'operator': '>',  'max_value': 10.0,
         'list_base_price': over10_offset,  'list_price': z['over10_kg'], 'variable_factor': 'weight'},
    ]


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

    # ─── 1) Tavo: solo renombrar (tarifa no cambió) ───
    tavo = kw('delivery.carrier', 'read', [TAVO_CARRIER_ID], fields=['name', 'fixed_price'])
    if tavo:
        cur = tavo[0]
        print(f"\n── TAVO #{TAVO_CARRIER_ID} ──")
        print(f"  ANTES: {cur['name']}")
        print(f"  AHORA: {TAVO_NEW_NAME}")
        print(f"  (tarifas sin cambios: ₡{cur['fixed_price']:.0f} hasta 15 kg, ₡5.000 más)")
        if args.apply and cur['name'] != TAVO_NEW_NAME:
            kw('delivery.carrier', 'write', [TAVO_CARRIER_ID], {'name': TAVO_NEW_NAME})
            print("  ✓ renombrado")

    # ─── 2) Dual Global - GAM (renombra #11 + reemplaza reglas) ───
    gam_existing = kw('delivery.carrier', 'read', [DUAL_GAM_ID],
                      fields=['name', 'price_rule_ids', 'delivery_type', 'fixed_price'])
    if not gam_existing:
        print(f"⚠ ERROR: carrier #{DUAL_GAM_ID} no existe")
        return

    print(f"\n── DUAL GAM #{DUAL_GAM_ID} (renombre + reemplazo reglas) ──")
    print(f"  ANTES: {gam_existing[0]['name']}  rules={len(gam_existing[0]['price_rule_ids'])}")
    z = DUAL_ZONES['GAM']
    new_rules = build_rules(z)
    print(f"  AHORA: {z['name']}")
    for r in new_rules:
        print(f"    {fmt_rule(r)}")

    if args.apply:
        # Borrar reglas previas
        if gam_existing[0]['price_rule_ids']:
            kw('delivery.price.rule', 'unlink', gam_existing[0]['price_rule_ids'])
        # Renombrar + actualizar type + fixed_price (de respaldo, igual al rango 0-2)
        kw('delivery.carrier', 'write', [DUAL_GAM_ID], {
            'name': z['name'],
            'delivery_type': 'base_on_rule',
            'fixed_price': z['b_0_2'],
        })
        # Crear reglas nuevas
        for r in new_rules:
            r2 = dict(r); r2['carrier_id'] = DUAL_GAM_ID
            kw('delivery.price.rule', 'create', r2)
        print("  ✓ aplicado")

    # ─── 3) Crear Dual Intermedia + Remota ───
    for zkey in ('Intermedia', 'Remota'):
        z = DUAL_ZONES[zkey]
        # ¿ya existe por nombre?
        existing_ids = kw('delivery.carrier', 'search', [('name', '=', z['name'])])
        print(f"\n── {z['name']} ──")
        new_rules = build_rules(z)
        for r in new_rules:
            print(f"    {fmt_rule(r)}")
        if existing_ids:
            cid = existing_ids[0]
            print(f"  YA EXISTE como #{cid} → reemplazo reglas")
            if args.apply:
                cur_rules = kw('delivery.price.rule', 'search', [('carrier_id', '=', cid)])
                if cur_rules:
                    kw('delivery.price.rule', 'unlink', cur_rules)
                kw('delivery.carrier', 'write', [cid], {
                    'delivery_type': 'base_on_rule',
                    'fixed_price': z['b_0_2'],
                })
                for r in new_rules:
                    r2 = dict(r); r2['carrier_id'] = cid
                    kw('delivery.price.rule', 'create', r2)
                print(f"  ✓ actualizado #{cid}")
        else:
            print(f"  NUEVO carrier a crear")
            if args.apply:
                # Necesita un product_id. Buscar 'Delivery' o crear uno simple.
                # Usar el product del carrier GAM #11 como base — los couriers comparten product type.
                base_carrier = kw('delivery.carrier', 'read', [DUAL_GAM_ID],
                                  fields=['product_id'])[0]
                prod_id = base_carrier['product_id'][0] if base_carrier.get('product_id') else None
                cid = kw('delivery.carrier', 'create', {
                    'name': z['name'],
                    'delivery_type': 'base_on_rule',
                    'fixed_price': z['b_0_2'],
                    'product_id': prod_id,
                    'active': True,
                })
                for r in new_rules:
                    r2 = dict(r); r2['carrier_id'] = cid
                    kw('delivery.price.rule', 'create', r2)
                print(f"  ✓ creado #{cid}")

    if not args.apply:
        print("\n[DRY-RUN] No se escribió nada. Para aplicar: --apply")


if __name__ == '__main__':
    main()
