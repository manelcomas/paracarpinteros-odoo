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
    python3 seo_meta.py --non-premium --dedup --write  # idem + regenera con IA los títulos duplicados
"""
import sys, os, re, json, argparse, hashlib, urllib.request
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
    budget = MAXD - len(CTA) - 1  # -1 reserva el punto final
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
        d = word_trunc(d, MAXD - 1).rstrip(' .,;:·-') + '.'
    return d


def _name_sha(name):
    """Hash corto del nombre normalizado, para invalidar cache si el producto se renombra."""
    return hashlib.sha1(clean(name).encode('utf-8')).hexdigest()[:12]


DEDUP_HINT = ('\n\nIMPORTANTE: este producto pertenece a una FAMILIA de productos casi idénticos '
              'que se diferencian por una MEDIDA (diámetro, corte, vástago, largo, grosor) o una '
              'VARIANTE (color, material). El meta_title DEBE incluir esa medida/variante distintiva '
              'para que se distinga de sus hermanos. Abreviá el tipo genérico todo lo necesario '
              '(p.ej. "Fresa CNC Round Nose / Media Caña – Corte" → "Fresa Round Nose") para que '
              'la medida quepa en los 60 caracteres.')


def ai_meta(code, name, price, ai, use_cache=True, dedup=False):
    """Genera {meta_title, meta_description} con Claude (texto). Cachea en seo_cache/.

    El cache se invalida si el nombre del producto cambió (compara _name_sha). En modo
    dedup pide a la IA front-loadear el dato distintivo y marca la entrada con _dedup."""
    cf = os.path.join(SEO_CACHE_DIR, f'{code}.json')
    cur = _name_sha(name)
    if use_cache and os.path.exists(cf):
        try:
            cached = json.load(open(cf, encoding='utf-8'))
        except Exception:
            cached = None
        if cached:
            name_ok = cached.get('_name_sha') == cur or '_name_sha' not in cached
            dedup_ok = (not dedup) or cached.get('_dedup')
            if name_ok and dedup_ok:
                if '_name_sha' not in cached:
                    # legacy sin hash: migrar estampando el nombre actual (sin re-gastar API)
                    cached['_name_sha'] = cur
                    json.dump(cached, open(cf, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
                return cached
            # nombre cambió, o se pide dedup y la entrada no es dedup → regenerar abajo
    if not API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY no está en el .env')
    user = (f'Producto: {name}\nReferencia: {code}\nPrecio: ₡{price:,.0f}\n'
            f'Specs disponibles:\n{_specs_block(ai)}\n\nGenerá el JSON de meta tags.')
    if dedup:
        user += DEDUP_HINT
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
    m = re.search(r'\{', text)
    d = {}
    if m:
        try:  # raw_decode ignora texto extra tras el objeto JSON
            d, _ = json.JSONDecoder().raw_decode(text[m.start():])
        except ValueError:
            d = {}
    res = {'meta_title': _fit_title(d.get('meta_title') or name),
           'meta_description': _fit_desc(d.get('meta_description') or name),
           '_name_sha': cur}
    if dedup:
        res['_dedup'] = True
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


def _dups(items):
    """Set de títulos que aparecen en más de un producto."""
    seen = {}
    for it in items:
        seen[it['title']] = seen.get(it['title'], 0) + 1
    return {t for t, n in seen.items() if n > 1}


def dedup_titles(items):
    """Regenera con IA los títulos colisionados (el determinista los trunca al mismo
    prefijo y pierde el dato distintivo: diámetro, modelo…). Garantiza unicidad final
    anexando el código de referencia a lo que aún colisione."""
    dups = _dups(items)
    if not dups:
        print('[dedup] sin títulos duplicados')
        return items
    print(f'[dedup] {sum(1 for it in items if it["title"] in dups)} productos con título duplicado → regenero con IA')
    for it in items:
        if it['title'] in dups:
            try:
                m = ai_meta(it['code'], it['name'], 0, load_ai(it['code']), dedup=True)
                it['title'], it['desc'], it['ai'] = m['meta_title'], m['meta_description'], True
            except Exception as e:
                print(f'  ⚠ {it["code"]}: dedup IA falló ({e})')
    # fallback determinista: lo que la IA no logró diferenciar, lleva el código
    still = _dups(items)
    for it in items:
        if it['title'] in still:
            core = it['title'][:-len(BRAND)] if it['title'].endswith(BRAND) else it['title']
            core = word_trunc(core, MAXT - len(BRAND) - len(it['code']) - 1)
            it['title'] = f"{core} {it['code']}{BRAND}"
            print(f'  · {it["code"]}: unicidad por código → {it["title"]}')
    return items


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--write', action='store_true', help='Escribir en Odoo (sin esto, dry-run)')
    ap.add_argument('--non-premium', action='store_true', help='Resto del catálogo (<₡100k)')
    ap.add_argument('--sample', type=int, help='Mostrar solo N en dry-run')
    ap.add_argument('--ai', action='store_true', help='Generar meta con IA (texto, cacheado)')
    ap.add_argument('--dedup', action='store_true', help='Regenerar con IA los títulos duplicados (anti-colisión)')
    args = ap.parse_args()

    call = odoo_connect()
    if args.non_premium:
        rows = fetch_products(call, max_price=PRICE_PREMIUM_MIN)
    else:
        rows = fetch_products(call, min_price=PRICE_PREMIUM_MIN)
    if args.sample and not args.write:
        rows = rows[:args.sample]
    items = gen_for(call, rows, use_ai_meta=args.ai)
    if args.dedup:
        items = dedup_titles(items)

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
