# -*- coding: utf-8 -*-
"""Mapeo cantón CR → zona Dual Global + tarifas vigentes mayo 2026.

Dual cobra por peso × zona. Como Odoo's delivery.price.rule sólo filtra por
peso (no por destino), la decisión de zona vive aquí, en el wa-bot.

Para mover un cantón entre zonas: editar DUAL_ZONE_BY_CANTON_ID y redesplegar.
La fuente única de las tarifas es DUAL_TARIFFS — Odoo tiene una réplica solo
para que el SO refleje un precio razonable (zona Intermedia como default).

IDs de cantón corresponden a x_canton_cr en Odoo Online. Los rangos de peso
y precios salen de dualglobal.cr (visto mayo 2026).
"""
from typing import Optional, Literal

ZoneT = Literal['gam', 'intermedia', 'remota']

# ──────────────────────────────────────────────────────────────────────────
#  TARIFAS por zona (CRC). Misma estructura para las 3:
#     0-2 kg / 2-5 kg / 5-10 kg / +10 kg (base + por kg extra) + home delivery
# ──────────────────────────────────────────────────────────────────────────
# IDs de delivery.carrier en Odoo Online para cada zona Dual.
# Mantener sincronizado con scripts/cargar_tarifas_courier_2026.py.
DUAL_CARRIER_ID_BY_ZONE: dict = {
    'gam':        11,   # 'Dual Global - GAM' (era 'Dual Global' antes de mayo 2026)
    'intermedia': 14,   # 'Dual Global - Intermedia' (creado mayo 2026)
    'remota':     15,   # 'Dual Global - Remota' (creado mayo 2026)
}

DUAL_TARIFFS = {
    'gam': {
        'name': 'Gran Área Metropolitana',
        'b_0_2':     2000,
        'b_2_5':     2700,
        'b_5_10':    3900,
        'over10_base': 3900,
        'over10_kg':    450,
        'home': 0,        # entrega a domicilio GRATIS
    },
    'intermedia': {
        'name': 'Zona Intermedia',
        'b_0_2':     2300,
        'b_2_5':     3200,
        'b_5_10':    5200,
        'over10_base': 5200,
        'over10_kg':    550,
        'home': 1000,
    },
    'remota': {
        'name': 'Zona Remota',
        'b_0_2':     2500,
        'b_2_5':     3700,
        'b_5_10':    6500,
        'over10_base': 6500,
        'over10_kg':    650,
        'home': 2000,
    },
}

# ──────────────────────────────────────────────────────────────────────────
#  Mapeo canton.id → zona. Aprobado por Manel 2026-05-27.
#  IDs son de x_canton_cr en Odoo Online (Paracarpinteros).
# ──────────────────────────────────────────────────────────────────────────
DUAL_ZONE_BY_CANTON_ID: dict[int, ZoneT] = {
    # ── San José (20 cantones) ──
    1:  'gam',         # San José
    2:  'gam',         # Escazú
    3:  'gam',         # Desamparados
    4:  'intermedia',  # Puriscal
    5:  'intermedia',  # Tarrazú
    6:  'gam',         # Aserrí
    7:  'intermedia',  # Mora (Ciudad Colón)
    8:  'gam',         # Goicoechea
    9:  'gam',         # Santa Ana
    10: 'gam',         # Alajuelita
    11: 'gam',         # Vázquez de Coronado
    12: 'intermedia',  # Acosta
    13: 'gam',         # Tibás
    14: 'gam',         # Moravia
    15: 'gam',         # Montes de Oca
    16: 'intermedia',  # Turrubares
    17: 'intermedia',  # Dota
    18: 'gam',         # Curridabat
    19: 'remota',      # Pérez Zeledón
    20: 'intermedia',  # León Cortés

    # ── Alajuela (15) ──
    21: 'gam',         # Alajuela (centro)
    22: 'intermedia',  # San Ramón
    23: 'intermedia',  # Grecia
    24: 'intermedia',  # San Mateo
    25: 'intermedia',  # Atenas
    26: 'intermedia',  # Naranjo
    27: 'intermedia',  # Palmares
    28: 'intermedia',  # Poás
    29: 'intermedia',  # Orotina
    30: 'remota',      # San Carlos (Ciudad Quesada)
    31: 'intermedia',  # Zarcero
    32: 'intermedia',  # Sarchí
    33: 'remota',      # Upala
    34: 'remota',      # Los Chiles
    35: 'remota',      # Guatuso

    # ── Cartago (8) ──
    36: 'gam',         # Cartago
    37: 'gam',         # Paraíso
    38: 'gam',         # La Unión
    39: 'intermedia',  # Jiménez
    40: 'intermedia',  # Turrialba
    41: 'intermedia',  # Alvarado
    42: 'gam',         # Oreamuno
    43: 'gam',         # El Guarco

    # ── Heredia (10) ──
    44: 'gam',         # Heredia
    45: 'gam',         # Barva
    46: 'gam',         # Santo Domingo
    47: 'gam',         # Santa Bárbara
    48: 'gam',         # San Rafael
    49: 'gam',         # San Isidro
    50: 'gam',         # Belén
    51: 'gam',         # Flores
    52: 'gam',         # San Pablo
    53: 'remota',      # Sarapiquí

    # ── Guanacaste (11) — toda Remota ──
    54: 'remota', 55: 'remota', 56: 'remota', 57: 'remota', 58: 'remota',
    59: 'remota', 60: 'remota', 61: 'remota', 62: 'remota', 63: 'remota',
    64: 'remota',

    # ── Puntarenas (11) ──
    65: 'intermedia',  # Puntarenas (centro)
    66: 'intermedia',  # Esparza
    67: 'remota',      # Buenos Aires
    68: 'intermedia',  # Montes de Oro
    69: 'remota',      # Osa
    70: 'remota',      # Aguirre (Quepos)
    71: 'remota',      # Golfito
    72: 'remota',      # Coto Brus
    73: 'remota',      # Parrita
    74: 'remota',      # Corredores
    75: 'remota',      # Garabito (Jacó)

    # ── Limón (6) — toda Remota ──
    76: 'remota', 77: 'remota', 78: 'remota', 79: 'remota', 80: 'remota',
    81: 'remota',
}


def zone_for_canton(canton_id: Optional[int]) -> ZoneT:
    """Devuelve 'gam' | 'intermedia' | 'remota' para un canton_id de Odoo.
    Si no se puede determinar (sin canton), devuelve 'intermedia' como default conservador."""
    if not canton_id:
        return 'intermedia'
    return DUAL_ZONE_BY_CANTON_ID.get(int(canton_id), 'intermedia')


def quote_dual(weight_g: float, zone: ZoneT, home_delivery: bool = False) -> dict:
    """Cotiza un envío Dual. weight_g en gramos. Devuelve un dict listo para JSON."""
    t = DUAL_TARIFFS[zone]
    kg = max(0.001, weight_g / 1000.0)

    if kg <= 2:
        base = t['b_0_2']; rango = '0-2 kg'
    elif kg <= 5:
        base = t['b_2_5']; rango = '2-5 kg'
    elif kg <= 10:
        base = t['b_5_10']; rango = '5-10 kg'
    else:
        extra_kg = kg - 10
        base = t['over10_base'] + int(round(extra_kg * t['over10_kg']))
        rango = f'+10 kg ({extra_kg:.1f} kg extra × ₡{t["over10_kg"]}/kg)'

    home_extra = t['home'] if home_delivery else 0
    total = base + home_extra

    return {
        'zone': zone,
        'zone_name': t['name'],
        'weight_kg': round(kg, 2),
        'rango': rango,
        'precio_base': base,
        'home_delivery': home_delivery,
        'home_extra': home_extra,
        'precio_total': total,
        'currency': 'CRC',
        'recommended_carrier_id': DUAL_CARRIER_ID_BY_ZONE[zone],
    }


def quote_dual_by_canton(weight_g: float, canton_id: Optional[int],
                          home_delivery: bool = False) -> dict:
    """Wrapper que primero deriva la zona desde el cantón del partner."""
    z = zone_for_canton(canton_id)
    return quote_dual(weight_g, z, home_delivery)
