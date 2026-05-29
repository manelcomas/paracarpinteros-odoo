#!/usr/bin/env python3
"""
Genera y (opcionalmente) sube website_meta_title / website_meta_description
optimizados por producto a Odoo.

- title: <= 60 car., palabra completa, con sufijo de marca.
- description: <= 155 car., usa specs/aplicaciones del cache IA si existen, + CTA CR.

Uso:
    python3 seo_meta.py                 # dry-run premium, muestra tabla (NO escribe)
    python3 seo_meta.py --sample 10     # dry-run, solo N de muestra
    python3 seo_meta.py --write         # escribe los premium en producción
    python3 seo_meta.py --non-premium --write   # resto del catálogo (determinista)
"""
import sys, os, re, json, argparse, urllib.request
from urllib.error import HTTPError
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import odoo_connect, fetch_products, PRICE_PREMIUM_MIN
from enrich import API_KEY, API_URL, MODEL

BRAND = ' | Paracarpinteros'
MAXT = 60
MAXD = 155
CTA = ' Envío en Costa Rica · consultá por WhatsApp.'
THIS = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(THIS, 'backup', 'ai_cache')
SEO_CACHE_DIR = os.path.join(THIS, 'backup', 'seo_cache')

SEO_SYSTEM = """Sos experto en SEO de e-commerce para Costa Rica. Generás meta tags para páginas de
producto de Paracarpinteros (herramientas y maquinaria de carpintería profesional).

REGLAS DURAS:
- meta_title: MÁXIMO 60 caracteres en total, y debe TERMINAR exactamente con " | Paracarpinteros".
  Front-load el tipo de producto + el dato que lo distingue de productos parecidos (modelo, potencia,
  medida/diámetro, voltaje). Sin cortar palabras. Sin comillas dobles dentro.
- meta_description: MÁXIMO 150 caracteres. Una frase con un beneficio o uso concreto del producto.
  Debe mencionar "Costa Rica" e invitar a consultar por WhatsApp. Natural, no relleno de keywords.
- NO inventes especificaciones. Usá SOLO lo que aparezca en los datos dados. Si no hay specs, usá el nombre.
- CRÍTICO: los identificadores (modelo, número de modelo, potencia HP/kW, voltaje, medidas) del meta_title
  y la description DEBEN coincidir con el NOMBRE del producto. Si las specs contradicen al nombre
  (ej: el nombre dice "DP-32 15 HP" pero las specs dicen "GT-32 13 HP"), GANA EL NOMBRE. Las specs solo
  sirven para enriquecer con datos que NO contradigan el nombre. Nunca cambies el modelo ni la potencia del nombre.
- Español de Costa Rica, tono profesional y cercano.

OUTPUT: JSON estricto, nada antes ni después: {"meta_title": "...", "meta_description": "..."}"""


def load_ai(code):
    p = os.path.join(CACHE_DIR, f'{code}.json')
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding='utf-8'))
        except Exception:
            return None
    return None


def clean(s):
    return re.sub(r'\s+', ' ', (s or '').strip())


_STOP = {'de','del','con','y','para','la','el','en','a','o','los','las','un','una','por','al','su'}


def word_trunc(s, limit):
    """Trunca a <=limit en frontera de palabra; quita stopwords colgando al final."""
    s = clean(s)
    if len(s) <= limit:
        out = s
    else:
        cut = s[:limit]
        out = cut.rsplit(' ', 1)[0] if ' ' in cut else cut
    out = out.rstrip(' .,;:·-')
    # quitar preposiciones/conjunciones sueltas al final (p.ej. "… área de")
    parts = out.split(' ')
    while len(parts) > 1 and parts[-1].lower() in _STOP:
        parts.pop()
    return ' '.join(parts).rstrip(' .,;:·-')


def build_title(name, ai):
    core = word_trunc(name, MAXT - len(BRAND))
    return core + BRAND


def build_desc(name, ai, price):
    budget = MAXD - len(CTA)
    # Frase núcleo: nombre + (1ª aplicación del cache IA si hay)
    lead = clean(name)
    if ai and ai.get('applications'):
        app = clean(ai['applications'][0])
        app = app[0].lower() + app[1:] if app else app
        candidate = f'{lead}. Ideal para {app}'
        if len(candidate) <= budget:
            lead = candidate
    lead = word_trunc(lead, budget).rstrip(' .,;:·-')
    return lead + '.' + CTA


def _specs_block(ai):
    if not ai:
        return '(sin specs)'
    lines = [f'- {k}: {v}' for k, v in ai.get('specs', [])[:12]]
    apps = ai.get('applications', [])[:4]
    if apps:
        lines.append('Aplicaciones: ' + '; '.join(apps))
    return '\n'.join(lines) or '(sin specs)'


def _fit_title(t):
    t = clean(t).strip('"')
    if not t.endswith(BRAND):
        # quitar cualquier marca a medias y re-anexar
        t = re.sub(r'\s*\|\s*Paracarpinteros\s*$', '', t)
        core = word_trunc(t, MAXT - len(BRAND))
        t = core + BRAND
    elif len(t) > MAXT:
        core = word_trunc(t[:-len(BRAND)], MAXT - len(BRAND))
        t = core + BRAND
    return t


def _fit_desc(d):
    d = clean(d).strip('"')
    if len(d) > MAXD:
        d = word_trunc(d, MAXD).rstrip(' .,;:·-') + '.'
    return d


def ai_meta(code, name, price, ai, use_cache=True):
    """Genera {meta_title, meta_description} con Claude (texto). Cachea en seo_cache/."""
    cf = os.path.join(SEO_CACHE_DIR, f'{code}.json')
    if use_cache and os.path.exists(cf):
        try:
            return json.load(open(cf, encoding='utf-8'))
        except Exception:
            pass
    if not API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY no está en el .env')
    user = (f'Producto: {name}\nReferencia: {code}\nPrecio: ₡{price:,.0f}\n'
            f'Specs disponibles:\n{_specs_block(ai)}\n\nGenerá el JSON de meta tags.')
    payload = {
        'model': MODEL, 'max_tokens': 300,
        'system': [{'type': 'text', 'text': SEO_SYSTEM, 'cache_control': {'type': 'ephemeral'}}],
        'messages': [{'role': 'user', 'content': user}],
    }
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), headers={
        'x-api-key': API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    text = data['content'][0]['text']
    m = re.search(r'\{[\s\S]*\}', text)
    d = json.loads(m.group(0)) if m else {}
    res = {'meta_title': _fit_title(d.get('meta_title') or name),
           'meta_description': _fit_desc(d.get('meta_description') or name)}
    os.makedirs(SEO_CACHE_DIR, exist_ok=True)
    json.dump(res, open(cf, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return res


def gen_for(call, rows, use_ai_meta=False):
    out = []
    for r in rows:
        code = r.get('default_code') or f'ID{r["id"]}'
        ai = load_ai(code)
        if use_ai_meta:
            try:
                m = ai_meta(code, r['name'], r.get('list_price') or 0, ai)
                title, desc, flag = m['meta_title'], m['meta_description'], True
            except Exception as e:
                print(f'  ⚠ {code}: IA meta falló ({e}), uso determinista')
                title, desc, flag = build_title(r['name'], ai), build_desc(r['name'], ai, r.get('list_price')), False
        else:
            title = build_title(r['name'], ai)
            desc = build_desc(r['name'], ai, r.get('list_price'))
            flag = bool(ai)
        out.append({'id': r['id'], 'code': code, 'name': r['name'],
                    'title': title, 'desc': desc, 'ai': flag})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--write', action='store_true', help='Escribir en Odoo (sin esto, dry-run)')
    ap.add_argument('--non-premium', action='store_true', help='Resto del catálogo (<₡100k)')
    ap.add_argument('--sample', type=int, help='Mostrar solo N en dry-run')
    ap.add_argument('--ai', action='store_true', help='Generar meta con IA (texto, cacheado)')
    args = ap.parse_args()

    call = odoo_connect()
    if args.non_premium:
        rows = fetch_products(call, max_price=PRICE_PREMIUM_MIN)
    else:
        rows = fetch_products(call, min_price=PRICE_PREMIUM_MIN)
    if args.sample and not args.write:
        rows = rows[:args.sample]
    items = gen_for(call, rows, use_ai_meta=args.ai)

    if not args.write:
        show = items[:args.sample] if args.sample else items
        for it in show:
            ai_flag = '🤖' if it['ai'] else '  '
            print(f"{ai_flag} {it['code']:<12}")
            print(f"   T({len(it['title'])}): {it['title']}")
            print(f"   D({len(it['desc'])}): {it['desc']}")
        # chequeos de longitud
        over_t = sum(1 for it in items if len(it['title']) > MAXT)
        over_d = sum(1 for it in items if len(it['desc']) > MAXD)
        print(f"\n[dry-run] {len(items)} productos · titles >{MAXT}: {over_t} · descs >{MAXD}: {over_d} · NO se escribió nada")
        return 0

    ok = 0
    for it in items:
        call('product.template', 'write', [[it['id']], {
            'website_meta_title': it['title'],
            'website_meta_description': it['desc'],
        }])
        ok += 1
    print(f"\n{ok} productos actualizados (meta_title + meta_description)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
