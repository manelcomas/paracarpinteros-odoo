#!/usr/bin/env python3
"""
Enriquece especificaciones de un producto usando Claude Sonnet 4.6 multimodal
mirando su foto + nombre + descripción corta.

Output: dict con keys: specs (list of [k,v]), applications, operation_notes, confidence.

Uso standalone:
    python3 scripts/fichas_premium/enrich.py A394
"""
import sys, os, json, re, base64, urllib.request
from urllib.error import HTTPError

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))
from _env import load_project_env
load_project_env()

API_KEY = os.environ.get('ANTHROPIC_API_KEY')
API_URL = 'https://api.anthropic.com/v1/messages'
MODEL = 'claude-sonnet-4-6'

SYSTEM_PROMPT = """Sos experto en herramientas y maquinaria de carpintería profesional.
Tu trabajo es extraer especificaciones técnicas VERIFICABLES de un producto a partir de
su foto y nombre. El producto se vende en Paracarpinteros, una tienda de Costa Rica.

REGLAS DURAS:
- NO inventes datos. Si no estás seguro, OMITÍ ese dato.
- Es mejor 3 specs correctas que 10 dudosas.
- Si la foto está borrosa, mal iluminada, o muestra packaging genérico, declará "low" confidence.
- Prefiere unidades originales del producto (mm, cm, pulgadas, kg, V, kW, HP, RPM).
- Para fresas/brocas/herramientas de corte, prioriza: tipo, diámetro, vástago, material, RPM máx.
- Para máquinas (router, sierra, CNC), prioriza: motor (kW/HP), voltaje, dimensiones, peso, área trabajo.

OUTPUT: JSON estricto. Nada antes ni después.
Schema:
{
  "specs": [["clave","valor"], ...],
  "applications": ["uso típico 1", ...],
  "operation_notes": ["nota crítica de operación 1", ...],
  "confidence": "high" | "medium" | "low",
  "reasoning_brief": "1 frase explicando qué viste"
}
"""

USER_TEMPLATE = """Producto: {name}
Referencia: {code}
Descripción cargada actual: {desc_short}

Extraé del JSON arriba."""


def fetch_product(call, code):
    ids = call('product.template', 'search', [[('default_code', '=', code)]])
    if not ids:
        return None
    return call('product.template', 'read', [ids,
        ['id', 'name', 'default_code', 'description_sale', 'image_1920']])[0]


def detect_media_type(image_b64):
    """Detecta JPEG/PNG/WEBP/GIF mirando los magic bytes del base64 decoded."""
    try:
        head = base64.b64decode(image_b64[:64], validate=False)
    except Exception:
        return 'image/jpeg'
    if head[:2] == b'\xff\xd8':
        return 'image/jpeg'
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    return 'image/jpeg'


def call_claude_vision(name, code, desc_short, image_b64, media_type=None):
    """Llama a Claude API multimodal. Devuelve dict parseado o None si falla.

    Si media_type es None, lo detecta automáticamente de los magic bytes del image_b64.
    """
    if not API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY no está en el .env")
    if media_type is None:
        media_type = detect_media_type(image_b64)

    payload = {
        "model": MODEL,
        "max_tokens": 1200,
        "system": [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": USER_TEMPLATE.format(name=name, code=code, desc_short=(desc_short or '')[:500])}
            ]
        }],
    }
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), headers={
        'x-api-key': API_KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
    except HTTPError as e:
        body = e.read().decode()
        # Fallback adicional: probar otros formatos si el detector falló
        if 'image' in body.lower():
            alt = {'image/jpeg':'image/png','image/png':'image/webp','image/webp':'image/jpeg','image/gif':'image/png'}.get(media_type)
            if alt:
                return call_claude_vision(name, code, desc_short, image_b64, alt)
        raise RuntimeError(f"API HTTP {e.code}: {body[:300]}")

    text = data['content'][0]['text']
    usage = data.get('usage', {})
    # Parsear JSON (puede venir con wrappers ```json)
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return {'_raw': text, '_usage': usage}
    try:
        parsed = json.loads(m.group(0))
        parsed['_usage'] = usage
        return parsed
    except json.JSONDecodeError as e:
        return {'_raw': text, '_error': str(e), '_usage': usage}


CACHE_DIR = os.path.join(THIS_DIR, 'backup', 'ai_cache')


def enrich_one(call, code, verbose=True, use_cache=True):
    # Cache persistente: el enrichment IA de un producto no cambia entre corridas
    # (misma foto + mismo prompt), así que lo guardamos para no re-pagar la API al
    # regenerar fichas por cambios de plantilla. Borrar backup/ai_cache/{code}.json fuerza refetch.
    cache_file = os.path.join(CACHE_DIR, f'{code}.json')
    if use_cache and os.path.exists(cache_file):
        try:
            with open(cache_file, encoding='utf-8') as f:
                cached = json.load(f)
            if verbose: print(f'  ⚡ {code}: desde cache ({len(cached.get("specs",[]))} specs)')
            return cached
        except Exception:
            pass  # cache corrupto → refetch

    p = fetch_product(call, code)
    if not p:
        if verbose: print(f'  ✗ {code}: producto no encontrado')
        return None
    if not p.get('image_1920'):
        if verbose: print(f'  ⊘ {code}: sin foto, no se puede enriquecer')
        return None

    result = call_claude_vision(
        name=p['name'],
        code=p.get('default_code') or '',
        desc_short=p.get('description_sale') or '',
        image_b64=p['image_1920'],
    )

    # Guardar en cache si el resultado es válido (limpio de metadatos privados _usage/_raw)
    if result and '_error' not in result and result.get('specs'):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            clean = {k: v for k, v in result.items() if not k.startswith('_')}
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(clean, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    if verbose:
        if '_error' in result:
            print(f'  ⚠ {code}: respuesta no parseable como JSON')
            print(result.get('_raw', '')[:200])
        else:
            u = result.get('_usage', {})
            in_t = u.get('input_tokens', 0)
            out_t = u.get('output_tokens', 0)
            cache_r = u.get('cache_read_input_tokens', 0)
            conf = result.get('confidence', '?')
            n_specs = len(result.get('specs', []))
            n_apps = len(result.get('applications', []))
            n_notes = len(result.get('operation_notes', []))
            print(f'  ✓ {code} · conf={conf} · {n_specs} specs · {n_apps} apps · {n_notes} notas · tok in={in_t} out={out_t} cache={cache_r}')
    return result


def main():
    import argparse
    sys.path.insert(0, THIS_DIR)
    from run import odoo_connect

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('code', help='Default code del producto a enriquecer')
    args = ap.parse_args()

    call = odoo_connect()
    r = enrich_one(call, args.code)
    if r and '_error' not in r:
        # Limpio _usage para imprimir
        out = {k: v for k, v in r.items() if not k.startswith('_')}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if r else 1


if __name__ == '__main__':
    sys.exit(main())
