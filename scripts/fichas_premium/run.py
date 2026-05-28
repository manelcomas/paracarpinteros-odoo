#!/usr/bin/env python3
"""
Genera fichas HTML enriquecidas para los productos premium (>₡100k) de
Paracarpinteros Odoo, re-empaquetando el website_description existente con la
plantilla minimalist editorial.

NO sube a Odoo — solo escribe a scripts/fichas_premium/output/<default_code>.html
Para subir a producción usar scripts/fichas_premium/upload.py (script aparte).

Uso:
    python3 scripts/fichas_premium/run.py            # genera todos los premium
    python3 scripts/fichas_premium/run.py A814       # solo un producto
    python3 scripts/fichas_premium/run.py --limit 5  # primeros 5
"""
import sys, os, re, json, html, argparse
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))
from _env import load_project_env
load_project_env()

import xmlrpc.client

ODOO_URL = os.environ['ODOO_URL']
ODOO_DB = os.environ['ODOO_DB']
ODOO_USER = os.environ['ODOO_USERNAME']
ODOO_KEY = os.environ['ODOO_API_KEY']

PRICE_PREMIUM_MIN = 100_000
PRICE_MEDIUM_MIN = 20_000
PRICE_MEDIUM_MAX = 100_000
WA_NUMBER = '50664063012'  # número de Paracarpinteros para wa.me
WEB_URL = 'https://www.paracarpinteros.com'
SHOP_NAME = 'Paracarpinteros'
SHOP_TAGLINE = 'Equipo profesional para carpintería · Costa Rica'

OUT_DIR = os.path.join(THIS_DIR, 'output')
BACKUP_DIR = os.path.join(THIS_DIR, 'backup')
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)


# ──────────────────────────── PARSER ────────────────────────────

class FichaExtractor(HTMLParser):
    """Extrae estructura de un website_description de Odoo a un dict."""

    def __init__(self):
        super().__init__()
        self.headers = []          # [(level, text)]
        self.paragraphs = []       # [text]
        self.bullets = []          # [text] - items de listas no-spec
        self.specs = []            # [(key, value)] - "K: V" patterns
        self.videos = []           # [youtube_id] - youtube embeds
        self.images = []           # [src] - imágenes embebidas distintas a image_1920
        self._tag_stack = []
        self._current_text = []
        self._capturing_strong = False
        self._strong_text = []
        self._current_li_strong = None  # último <strong> dentro de <li>
        self._in_li = False
        self._li_text_buf = []

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        self._tag_stack.append(tag)

        # 1) Detección multimedia (independiente de la captura de texto)
        if tag == 'iframe':
            src = attrs_d.get('src', '')
            yt = self._extract_youtube_id(src)
            if yt:
                self.videos.append(yt)
        if tag == 'div':
            cls = attrs_d.get('class', '')
            if 'media_iframe_video' in cls:
                expr = attrs_d.get('data-oe-expression', '') or attrs_d.get('data-src', '')
                yt = self._extract_youtube_id(expr)
                if yt:
                    self.videos.append(yt)
        if tag == 'img':
            src = attrs_d.get('src', '')
            if src and not src.startswith('data:') and '/web/image/' not in src:
                self.images.append(src)

        # 2) Captura de estructura de texto
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5'):
            self._current_text = []
        elif tag == 'p' or tag == 'div':
            if not any(t in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5') for t in self._tag_stack[:-1]):
                self._current_text = []
        elif tag == 'li':
            self._in_li = True
            self._li_text_buf = []
            self._current_li_strong = None
        elif tag == 'strong' or tag == 'b':
            self._capturing_strong = True
            self._strong_text = []

    def handle_endtag(self, tag):
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5'):
            lvl = int(tag[1])
            txt = self._normalize(''.join(self._current_text))
            if txt and len(txt) < 200:
                self.headers.append((lvl, txt))
            self._current_text = []
        elif tag in ('p', 'div'):
            txt = self._normalize(''.join(self._current_text))
            # filtrar div interno o vacío
            if txt and len(txt) > 20 and not any(t in self._tag_stack for t in ('li', 'h1', 'h2', 'h3', 'h4', 'h5')):
                # detectar specs en línea: "Key: Value"
                spec = self._detect_inline_spec(txt)
                if spec:
                    self.specs.append(spec)
                else:
                    self.paragraphs.append(txt)
            self._current_text = []
        elif tag == 'li':
            self._in_li = False
            full = self._normalize(''.join(self._li_text_buf))
            if not full:
                return
            # Si tenía <strong>K:</strong> V → es spec
            if self._current_li_strong:
                k = self._current_li_strong.rstrip(':').strip()
                v = full
                # quitar k del inicio de v si está
                if v.lower().startswith(k.lower()):
                    v = v[len(k):].lstrip(':').strip()
                if k and v and len(k) < 50 and len(v) < 300:
                    self.specs.append((k, v))
                else:
                    self.bullets.append(full)
            else:
                # quizás es "Key: Value" inline
                spec = self._detect_inline_spec(full)
                if spec:
                    self.specs.append(spec)
                else:
                    self.bullets.append(full)
        elif tag in ('strong', 'b'):
            self._capturing_strong = False
            stxt = ''.join(self._strong_text).strip()
            if self._in_li and stxt and self._current_li_strong is None:
                self._current_li_strong = stxt

    def handle_data(self, data):
        if not data:
            return
        if self._capturing_strong:
            self._strong_text.append(data)
        if self._in_li:
            self._li_text_buf.append(data)
        else:
            # Si estamos dentro de un h/p/div, capturar
            for t in reversed(self._tag_stack):
                if t in ('h1','h2','h3','h4','h5','p','div'):
                    self._current_text.append(data)
                    break

    @staticmethod
    def _normalize(s):
        s = re.sub(r'\s+', ' ', s).strip()
        # quitar markdown literal **X** que Odoo no renderiza
        s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
        return s

    @staticmethod
    def _detect_inline_spec(txt):
        """Detecta 'Key: Value' donde Key es 2-40 chars sin punto y Value <250."""
        m = re.match(r'^([A-ZÁÉÍÓÚÜÑa-záéíóúüñ][^:\n.]{1,40}):\s+(.{1,250})$', txt)
        if m:
            k = m.group(1).strip()
            v = m.group(2).strip()
            if v and len(v) > 1:
                return (k, v)
        return None

    @staticmethod
    def _extract_youtube_id(url_or_embed):
        if not url_or_embed:
            return None
        # acepta youtube.com/embed/ID, youtube.com/watch?v=ID, youtu.be/ID
        m = re.search(r'(?:youtube\.com/embed/|youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})', url_or_embed)
        return m.group(1) if m else None


def _fallback_plain_text(html_str):
    """Si el parser DOM no extrajo nada, sacar texto plano y buscar bullets/specs por regex.
    Útil para HTMLs del builder viejo de Odoo: <div>• Motor: 3 kW</div><div>• Caudal: …</div>."""
    # Quitar tags
    txt = re.sub(r'<br\s*/?>', '\n', html_str, flags=re.I)
    txt = re.sub(r'</(?:div|p|li|h[1-6])>', '\n', txt, flags=re.I)
    txt = re.sub(r'<[^>]+>', '', txt)
    txt = html.unescape(txt)
    # Normalizar bullets
    txt = txt.replace('●','•').replace('▪','•').replace('-','•')  # opcional, pero seguro para listas
    lines = [l.strip().lstrip('•').strip() for l in txt.split('\n') if l.strip()]
    specs, bullets, paragraphs = [], [], []
    for ln in lines:
        if len(ln) < 3:
            continue
        if len(ln) > 200:
            paragraphs.append(ln)
            continue
        m = re.match(r'^([A-ZÁÉÍÓÚÜÑa-záéíóúüñ][^:\n]{1,40}):\s+(.+)$', ln)
        if m:
            specs.append((m.group(1).strip(), m.group(2).strip()))
        else:
            bullets.append(ln)
    return {'specs': specs, 'bullets': bullets, 'paragraphs': paragraphs}


def parse_description(html_str):
    """Devuelve dict: { headers, paragraphs, bullets, specs, videos, images }."""
    if not html_str:
        return {'headers':[],'paragraphs':[],'bullets':[],'specs':[],'videos':[],'images':[]}
    ext = FichaExtractor()
    try:
        ext.feed(html_str)
    except Exception as e:
        print(f"  parser error: {e}", file=sys.stderr)
    # Dedup specs preservando orden
    seen, dedup_specs = set(), []
    for k, v in ext.specs:
        key_norm = (k.lower(), v.lower())
        if key_norm in seen: continue
        seen.add(key_norm)
        dedup_specs.append((k, v))

    result = {
        'headers':    ext.headers,
        'paragraphs': ext.paragraphs,
        'bullets':    ext.bullets,
        'specs':      dedup_specs,
        'videos':     list(dict.fromkeys(ext.videos)),
        'images':     list(dict.fromkeys(ext.images)),
    }

    # Post: reclasificar paragraphs cortos con bullets/specs.
    # Útil cuando el HTML viene como <div>• Motor: 3kW</div><div>• Caudal: 3150</div>
    promoted_specs, promoted_bullets, kept_paragraphs = [], [], []
    for p in result['paragraphs']:
        # quitar bullet leading
        stripped = re.sub(r'^[•●▪\-\*]\s*', '', p).strip()
        if len(stripped) < 200:
            spec = FichaExtractor._detect_inline_spec(stripped)
            if spec:
                promoted_specs.append(spec)
                continue
            if stripped != p:  # tenía bullet
                promoted_bullets.append(stripped)
                continue
        # NUEVO: párrafo más largo, intentar dividir por '. ' o ';' y detectar specs en chunks
        # Útil cuando narrativa contiene "Material: Acero. Diámetro: 5mm. Peso: 200g."
        if len(stripped) < 600:
            chunks = re.split(r'(?<=[.;])\s+', stripped)
            chunk_specs = []
            for chunk in chunks:
                chunk = chunk.rstrip('.;').strip()
                if 3 < len(chunk) < 200:
                    spec = FichaExtractor._detect_inline_spec(chunk)
                    if spec:
                        chunk_specs.append(spec)
            if chunk_specs:
                promoted_specs.extend(chunk_specs)
                # El párrafo lo mantengo igual — sirve como narrativa
        kept_paragraphs.append(p)
    # merge sin perder orden ni duplicar
    seen_s = set((k.lower(), v.lower()) for k, v in result['specs'])
    for k, v in promoted_specs:
        nk = (k.lower(), v.lower())
        if nk not in seen_s:
            result['specs'].append((k, v))
            seen_s.add(nk)
    seen_b = set(b.lower() for b in result['bullets'])
    for b in promoted_bullets:
        if b.lower() not in seen_b:
            result['bullets'].append(b)
            seen_b.add(b.lower())
    result['paragraphs'] = kept_paragraphs

    # Fallback: si tras todo eso no extrajo nada, parsear texto plano
    if not result['specs'] and not result['bullets'] and not result['paragraphs']:
        fb = _fallback_plain_text(html_str)
        result['specs'] = fb['specs']
        result['bullets'] = fb['bullets']
        result['paragraphs'] = fb['paragraphs']

    return result


# ──────────────────────────── DETECTORES DE KPIs ────────────────────────────

# Patrones para extraer los 3 KPIs visualmente destacados (potencia, capacidad, dimensión)
def detect_kpis(specs, name):
    """Detecta hasta 3 KPIs visualmente fuertes para mostrar en el hero."""
    kpis = []
    used_keys = set()

    def add(val, lbl):
        if val and len(kpis) < 3:
            kpis.append({'val': val, 'lbl': lbl})

    # Buscar patrones típicos en specs
    for k, v in specs:
        kl = k.lower()
        if any(t in kl for t in ['motor','potencia','potência','power']) and 'motor' not in used_keys:
            add(v[:18], 'Potencia')
            used_keys.add('motor')
        elif any(t in kl for t in ['caudal','flujo','air capacity','airflow']) and 'caudal' not in used_keys:
            add(v[:18], 'Caudal')
            used_keys.add('caudal')
        elif any(t in kl for t in ['voltaje','tensión','tension','voltage']) and 'volt' not in used_keys:
            add(v[:18], 'Tensión')
            used_keys.add('volt')
        elif any(t in kl for t in ['velocidad','rpm','spindle','revoluciones']) and 'rpm' not in used_keys:
            add(v[:18], 'Velocidad')
            used_keys.add('rpm')
        elif any(t in kl for t in ['peso','weight']) and 'peso' not in used_keys:
            add(v[:18], 'Peso')
            used_keys.add('peso')
        elif any(t in kl for t in ['área','area','superficie','grabado','recorrido','work area']) and 'area' not in used_keys:
            add(v[:18], 'Área trabajo')
            used_keys.add('area')

    # Fallback: extraer del nombre patrones tipo "300W" "3kW" "1390"
    if len(kpis) < 3:
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*(kW|HP|W|kw|hp)\b', name, re.I)
        if m and 'motor' not in used_keys:
            add(f"{m.group(1)} {m.group(2)}", 'Potencia')
            used_keys.add('motor')
    return kpis


# ──────────────────────────── PLANTILLA ────────────────────────────

STYLE = """\
<style>
.pcf{--bg:#FBFBFA;--surface:#fff;--surface-2:#F7F6F3;--text:#111;--text-2:#6F6F6E;--text-3:#9B9A97;
  --border:#EAEAEA;--border-2:#D1D1CF;--green-bg:#EDF3EC;--green-fg:#346538;--blue-bg:#E1F3FE;
  --blue-fg:#1F6C9F;--yellow-bg:#FBF3DB;--yellow-fg:#956400;--purple-bg:#F4ECFF;--purple-fg:#5B2A86;
  --r-sm:4px;--r-md:6px;--r-lg:10px;--r-xl:14px;
  --font-sans:'Geist Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
  --font-serif:'Newsreader','Lyon Text',Georgia,serif;
  --font-mono:'Geist Mono','SF Mono',ui-monospace,monospace;
  font-family:var(--font-sans);color:var(--text);line-height:1.55;max-width:1080px;margin:0 auto;padding:24px 16px}
.pcf .hero{display:grid;grid-template-columns:1fr;gap:32px;margin-bottom:48px}
.pcf .hero-tags{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.pcf .tag{display:inline-block;padding:4px 11px;border-radius:9999px;font-size:.66rem;font-weight:600;text-transform:uppercase;letter-spacing:0.07em}
.pcf .tag-industrial{background:var(--blue-bg);color:var(--blue-fg)}
.pcf .tag-power{background:var(--yellow-bg);color:var(--yellow-fg)}
.pcf .tag-stock{background:var(--green-bg);color:var(--green-fg)}
.pcf .tag-premium{background:var(--purple-bg);color:var(--purple-fg)}
.pcf h1.title{font-family:var(--font-serif);font-size:2.2rem;font-weight:500;letter-spacing:-0.025em;line-height:1.08;margin:0 0 10px}
.pcf .ref{display:block;font-family:var(--font-mono);font-size:.78rem;color:var(--text-3);font-weight:400;letter-spacing:0.05em;text-transform:uppercase;margin-top:6px}
.pcf .lead{font-size:1.02rem;color:var(--text-2);line-height:1.6;margin:14px 0 20px;max-width:62ch}
.pcf .kpi-row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:18px 0 24px}
.pcf .kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-md);padding:14px 10px;text-align:center}
.pcf .kpi-val{font-family:var(--font-serif);font-size:1.45rem;font-weight:500;letter-spacing:-0.02em;line-height:1.05;margin-bottom:4px;word-break:break-word}
.pcf .kpi-lbl{font-size:.62rem;color:var(--text-2);text-transform:uppercase;letter-spacing:0.06em;font-weight:600}
.pcf .price-block{margin:24px 0 20px;padding:18px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.pcf .price{font-family:var(--font-serif);font-size:1.85rem;font-weight:500;letter-spacing:-0.025em;line-height:1}
.pcf .price small{font-family:var(--font-mono);font-size:.66rem;color:var(--text-3);font-weight:400;display:block;margin-top:5px;letter-spacing:0.05em;text-transform:uppercase}
.pcf .cta-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.pcf .btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:11px 18px;border-radius:var(--r-md);font-size:.86rem;font-weight:500;cursor:pointer;border:1px solid transparent;text-decoration:none;transition:.15s;line-height:1}
.pcf .btn-wa{background:#25D366;color:#fff}
.pcf .btn-wa:hover{background:#1ebe5b;color:#fff}
.pcf .btn-secd{background:var(--surface);color:var(--text);border-color:var(--border)}
.pcf .btn-secd:hover{background:var(--surface-2);border-color:var(--border-2)}
.pcf .btn svg{width:14px;height:14px}
.pcf .sec{margin:48px 0}
.pcf .sec h2{font-family:var(--font-serif);font-size:1.6rem;font-weight:500;letter-spacing:-0.025em;line-height:1.1;margin:0 0 6px}
.pcf .sec .sec-sub{font-size:.9rem;color:var(--text-2);margin-bottom:22px;max-width:60ch;line-height:1.55}
.pcf .narrative p{font-size:.94rem;color:var(--text);line-height:1.65;margin:0 0 14px;max-width:68ch}
.pcf .features{list-style:none;padding:0;margin:0}
.pcf .features li{display:flex;gap:12px;padding:9px 0;border-bottom:1px solid var(--border);font-size:.92rem;color:var(--text);line-height:1.45}
.pcf .features li:last-child{border-bottom:none}
.pcf .features li::before{content:'';width:18px;height:18px;border-radius:50%;background:var(--green-bg);flex-shrink:0;margin-top:2px;background-image:url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%23346538%22 stroke-width=%223%22 stroke-linecap=%22round%22 stroke-linejoin=%22round%22><polyline points=%2220 6 9 17 4 12%22/></svg>');background-repeat:no-repeat;background-position:center;background-size:10px}
.pcf .features.features-warn li::before{background:#FBF3DB;background-image:url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%23956400%22 stroke-width=%223%22 stroke-linecap=%22round%22 stroke-linejoin=%22round%22><line x1=%2212%22 y1=%229%22 x2=%2212%22 y2=%2213%22/><line x1=%2212%22 y1=%2217%22 x2=%2212.01%22 y2=%2217%22/><path d=%22m10.29 3.86-8.18 14.16a2 2 0 0 0 1.71 3h16.36a2 2 0 0 0 1.71-3l-8.18-14.16a2 2 0 0 0-3.42 0z%22/></svg>')}
.pcf table.spec-table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);overflow:hidden}
.pcf table.spec-table tr{border-bottom:1px solid var(--border)}
.pcf table.spec-table tr:last-child{border-bottom:none}
.pcf table.spec-table th,.pcf table.spec-table td{padding:12px 16px;text-align:left;font-size:.86rem;vertical-align:top}
.pcf table.spec-table th{width:34%;color:var(--text-2);font-weight:500;text-transform:uppercase;letter-spacing:0.05em;font-size:.68rem;background:var(--surface-2)}
.pcf table.spec-table td{color:var(--text);font-family:var(--font-mono);font-size:.83rem}
.pcf .video-wrap{position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:var(--r-lg);border:1px solid var(--border);background:#000}
.pcf .video-wrap iframe{position:absolute;top:0;left:0;width:100%;height:100%;border:none}
.pcf .cta-final{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-xl);padding:32px 28px;text-align:center;margin:40px 0 0}
.pcf .cta-final h3{font-family:var(--font-serif);font-size:1.5rem;font-weight:500;letter-spacing:-0.02em;margin:0 0 6px}
.pcf .cta-final p{color:var(--text-2);font-size:.92rem;margin:0 0 18px;line-height:1.55}
.pcf .cta-final .cta-row{justify-content:center}
@media(max-width:600px){.pcf .kpi-row{grid-template-columns:repeat(3,1fr)} .pcf .kpi-val{font-size:1.1rem} .pcf h1.title{font-size:1.7rem}}
</style>
"""

# SVG inline para CTAs (reutilizado)
SVG_WA = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.5 14.4c-.3-.15-1.77-.87-2.04-.97s-.47-.15-.67.15-.77.97-.95 1.17-.34.22-.64.07c-1.8-.9-3-1.6-4.2-3.65-.32-.55.32-.5.9-1.67.1-.2.05-.37-.02-.52s-.67-1.6-.92-2.2c-.24-.57-.49-.5-.67-.5-.17 0-.37-.02-.57-.02s-.52.07-.8.37c-.27.3-1.04 1.02-1.04 2.5s1.07 2.9 1.22 3.1c.15.2 2.1 3.2 5.1 4.5 1.9.82 2.65.9 3.6.77.58-.08 1.77-.72 2.02-1.42.25-.7.25-1.3.17-1.42-.08-.13-.27-.2-.57-.35M12 2C6.5 2 2 6.5 2 12c0 1.92.56 3.7 1.52 5.22L2 22l4.92-1.5C8.37 21.45 10.13 22 12 22c5.5 0 10-4.5 10-10S17.5 2 12 2"/></svg>'


def render(product, parsed, kpis, ficha_url=None, ai_extra=None):
    """Renderiza el HTML del bloque .pcf (sin <html><body>, listo para website_description).

    ficha_url: URL opcional al HTML imprimible.
    ai_extra: dict opcional con 'specs', 'applications', 'operation_notes' (de IA + foto).
              Si specs viene, se mergean con parsed['specs'] (parser HTML gana en duplicados).
    """
    name = html.escape(product['name'])
    code = html.escape(product.get('default_code') or '')
    price = product['list_price']

    # Merge specs IA → parsed['specs'] (parser HTML tiene prioridad)
    if ai_extra and ai_extra.get('specs'):
        seen_keys = {k.lower() for k, _ in parsed['specs']}
        for k, v in ai_extra['specs']:
            if k and v and k.lower() not in seen_keys:
                parsed['specs'].append((k, v))
                seen_keys.add(k.lower())

    # Tags
    tags = []
    if price >= PRICE_PREMIUM_MIN:
        tags.append('<span class="tag tag-premium">Premium</span>')
    if any('industrial' in (k.lower()+' '+v.lower()) for k,v in parsed['specs']):
        tags.insert(0, '<span class="tag tag-industrial">Industrial</span>')
    if kpis and any('Potencia' in k['lbl'] for k in kpis):
        pot = next(k for k in kpis if k['lbl']=='Potencia')
        tags.append(f'<span class="tag tag-power">{html.escape(pot["val"])}</span>')
    tags.append('<span class="tag tag-stock">En stock</span>')

    # Lead = primer párrafo razonablemente largo o concat de los 2 primeros
    leads = [p for p in parsed['paragraphs'] if len(p) > 40][:2]
    lead = ' '.join(leads) if leads else f'{name}. Disponible en Paracarpinteros — herramienta profesional para taller de carpintería en Costa Rica.'
    if len(lead) > 380:
        lead = lead[:377].rsplit(' ', 1)[0] + '…'

    # KPIs
    kpis_html = ''
    if kpis:
        kpis_html = '<div class="kpi-row">' + ''.join(
            f'<div class="kpi"><div class="kpi-val">{html.escape(k["val"])}</div><div class="kpi-lbl">{html.escape(k["lbl"])}</div></div>'
            for k in kpis
        ) + '</div>'

    # Tabla de specs
    specs_html = ''
    if parsed['specs']:
        rows = ''.join(
            f'<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>'
            for k, v in parsed['specs'][:25]
        )
        specs_html = f'''
  <section class="sec">
    <h2>Especificaciones técnicas</h2>
    <p class="sec-sub">Datos del fabricante. Tolerancia ±5% en medidas/pesos.</p>
    <table class="spec-table">{rows}</table>
  </section>'''

    # Narrativa (párrafos)
    narrative_html = ''
    extra_paragraphs = [p for p in parsed['paragraphs'][len(leads):] if len(p) > 30][:5]
    if extra_paragraphs:
        narrative_html = f'''
  <section class="sec narrative">
    <h2>Descripción</h2>
    {''.join(f'<p>{html.escape(p)}</p>' for p in extra_paragraphs)}
  </section>'''

    # Features (bullets no-spec)
    features_html = ''
    if parsed['bullets']:
        items = ''.join(f'<li>{html.escape(b)}</li>' for b in parsed['bullets'][:12])
        features_html = f'''
  <section class="sec">
    <h2>Características</h2>
    <ul class="features">{items}</ul>
  </section>'''

    # Video (primer YouTube encontrado)
    video_html = ''
    if parsed['videos']:
        yt = parsed['videos'][0]
        video_html = f'''
  <section class="sec">
    <h2>Verlo funcionar</h2>
    <p class="sec-sub">Demo del equipo en operación.</p>
    <div class="video-wrap"><iframe src="https://www.youtube.com/embed/{html.escape(yt)}?rel=0" allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>
  </section>'''

    # Aplicaciones recomendadas (de IA)
    apps_html = ''
    if ai_extra and ai_extra.get('applications'):
        apps = ai_extra['applications'][:6]
        items = ''.join(f'<li>{html.escape(a)}</li>' for a in apps)
        apps_html = f'''
  <section class="sec">
    <h2>Aplicaciones recomendadas</h2>
    <p class="sec-sub">Usos típicos identificados por análisis del producto.</p>
    <ul class="features">{items}</ul>
  </section>'''

    # Notas críticas de operación (de IA)
    notes_html = ''
    if ai_extra and ai_extra.get('operation_notes'):
        notes = ai_extra['operation_notes'][:6]
        items = ''.join(f'<li>{html.escape(n)}</li>' for n in notes)
        notes_html = f'''
  <section class="sec">
    <h2>Notas de operación</h2>
    <p class="sec-sub">Recomendaciones críticas para uso seguro y vida útil.</p>
    <ul class="features features-warn">{items}</ul>
  </section>'''

    # WhatsApp link con texto pre-llenado
    wa_text = f'Hola%2C%20me%20interesa%20{code}%20({name.replace(" ", "%20")})'[:200]
    wa_url = f'https://wa.me/{WA_NUMBER}?text={wa_text}'

    # Botón ficha técnica imprimible (opcional)
    ficha_btn = ''
    if ficha_url:
        ficha_btn = f'''<a class="btn btn-secd" href="{html.escape(ficha_url)}" target="_blank" rel="noopener">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            Descargar ficha técnica
        </a>'''

    return f"""{STYLE}
<div class="pcf">
  <section class="hero">
    <div>
      <div class="hero-tags">{''.join(tags)}</div>
      <h1 class="title">{name}<span class="ref">REF · {code}</span></h1>
      <p class="lead">{html.escape(lead)}</p>
      {kpis_html}
      <div class="price-block">
        <div class="price">₡ {price:,.0f} <small>IVA incluido · envío Costa Rica</small></div>
        <div class="cta-row">
          <a class="btn btn-wa" href="{wa_url}">{SVG_WA} Consultar por WhatsApp</a>
          {ficha_btn or '<a class="btn btn-secd" href="#specs">Ver ficha técnica</a>'}
        </div>
      </div>
    </div>
  </section>
  <a id="specs"></a>
  {specs_html}
  {narrative_html}
  {features_html}
  {apps_html}
  {notes_html}
  {video_html}
  <section class="cta-final">
    <h3>¿Dudas antes de comprar?</h3>
    <p>Te confirmamos disponibilidad, garantía y entrega por WhatsApp el mismo día.</p>
    <div class="cta-row">
      <a class="btn btn-wa" href="{wa_url}">{SVG_WA} Escribir por WhatsApp</a>
    </div>
  </section>
</div>
""".strip()


# ──────────────────────────── FICHA TÉCNICA IMPRIMIBLE ────────────────────────────

def render_print(product, parsed, ai_extra=None):
    """HTML standalone, print-friendly. Foto del producto + machote Paracarpinteros + auto-print.

    Servido como ir.attachment de Odoo. El cliente lo abre en su browser, se autodispara
    window.print() para que pueda guardar como PDF o imprimir.

    ai_extra: dict opcional con applications + operation_notes a incluir en la ficha.
    """
    pid = product['id']
    name = html.escape(product['name'])
    code = html.escape(product.get('default_code') or '')
    price = product['list_price']
    img_url = f'{WEB_URL}/web/image/product.template/{pid}/image_1920'

    # Merge specs IA
    if ai_extra and ai_extra.get('specs'):
        seen_keys = {k.lower() for k, _ in parsed['specs']}
        for k, v in ai_extra['specs']:
            if k and v and k.lower() not in seen_keys:
                parsed['specs'].append((k, v))
                seen_keys.add(k.lower())

    # Tabla de specs (toda, sin tope de 25 — la ficha imprimible no escatima)
    specs_rows = ''.join(
        f'<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>'
        for k, v in parsed['specs']
    ) if parsed['specs'] else '<tr><td colspan="2" style="text-align:center;color:#9B9A97;padding:24px">Sin especificaciones cargadas — consultar por WhatsApp</td></tr>'

    # Bullets (características) si hay
    feats_html = ''
    if parsed['bullets']:
        items = ''.join(f'<li>{html.escape(b)}</li>' for b in parsed['bullets'][:12])
        feats_html = f'<div class="feats-block"><h3>Características</h3><ul class="feats">{items}</ul></div>'

    # Aplicaciones (IA)
    apps_html = ''
    if ai_extra and ai_extra.get('applications'):
        items = ''.join(f'<li>{html.escape(a)}</li>' for a in ai_extra['applications'][:6])
        apps_html = f'<div class="feats-block"><h3>Aplicaciones recomendadas</h3><ul class="feats">{items}</ul></div>'

    # Notas de operación (IA)
    notes_html = ''
    if ai_extra and ai_extra.get('operation_notes'):
        items = ''.join(f'<li>{html.escape(n)}</li>' for n in ai_extra['operation_notes'][:6])
        notes_html = f'<div class="feats-block notes-block"><h3>Notas de operación</h3><ul class="feats feats-warn">{items}</ul></div>'

    # Descripción narrativa
    narrative_html = ''
    paras = [p for p in parsed['paragraphs'] if len(p) > 40][:2]
    if paras:
        narrative_html = f'<div class="narr-block"><h3>Descripción</h3>' + \
            ''.join(f'<p>{html.escape(p)}</p>' for p in paras) + '</div>'

    fecha = datetime.now().strftime('%d/%m/%Y')

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ficha técnica · {name} · {SHOP_NAME}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#fff;--surface:#fff;--surface-2:#F7F6F3;
  --text:#111;--text-2:#6F6F6E;--text-3:#9B9A97;
  --border:#EAEAEA;--accent:#346538;
  --font-sans:-apple-system,BlinkMacSystemFont,'Segoe UI','Geist Sans',system-ui,sans-serif;
  --font-serif:Georgia,'Newsreader','Lyon Text',serif;
  --font-mono:'SF Mono','Geist Mono',ui-monospace,monospace;
}}
html,body{{background:var(--surface-2);color:var(--text);font-family:var(--font-sans);line-height:1.5;-webkit-font-smoothing:antialiased}}
.page{{max-width:880px;margin:24px auto;background:#fff;padding:42px 52px;box-shadow:0 1px 4px rgba(0,0,0,.06);display:flex;flex-direction:column}}

/* Header / machote */
.head{{display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:16px;border-bottom:2px solid var(--text);margin-bottom:32px;gap:24px}}
.head .brand{{flex:1;min-width:0}}
.head .brand .brand-name{{font-family:var(--font-serif);font-size:1.6rem;font-weight:500;letter-spacing:-0.025em;line-height:1;color:var(--text)}}
.head .brand .brand-tag{{font-family:var(--font-sans);font-size:.7rem;color:var(--text-2);font-weight:400;margin-top:6px;letter-spacing:0.03em;line-height:1.3}}
.head .meta{{text-align:right;font-family:var(--font-mono);font-size:.7rem;color:var(--text-2);text-transform:uppercase;letter-spacing:0.05em;line-height:1.6;flex-shrink:0}}
.head .meta b{{color:var(--text);font-weight:500}}

/* Hero */
.hero{{display:grid;grid-template-columns:0.95fr 1.05fr;gap:28px;margin-bottom:28px;align-items:start}}
.hero .img-wrap{{position:relative;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;aspect-ratio:1/1;max-height:280px;overflow:hidden;display:flex;align-items:center;justify-content:center}}
.hero .img-wrap img{{max-width:100%;max-height:100%;object-fit:contain}}
.hero .img-fallback{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:var(--text-3);font-family:var(--font-mono);font-size:.7rem;text-transform:uppercase;letter-spacing:0.05em;padding:24px;text-align:center}}
.hero .img-fallback svg{{width:48px;height:48px;opacity:.4}}
.hero h1{{font-family:var(--font-serif);font-size:1.7rem;font-weight:500;letter-spacing:-0.025em;line-height:1.1;margin-bottom:8px}}
.hero .ref{{font-family:var(--font-mono);font-size:.74rem;color:var(--text-3);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:14px;display:block}}
.hero .price{{font-family:var(--font-serif);font-size:1.5rem;font-weight:500;color:var(--text);margin-top:20px;padding-top:14px;border-top:1px solid var(--border)}}
.hero .price small{{display:block;font-family:var(--font-mono);font-size:.62rem;color:var(--text-3);font-weight:400;margin-top:4px;letter-spacing:0.05em;text-transform:uppercase}}

/* Specs */
.specs-block, .feats-block, .narr-block{{margin-bottom:22px}}
.specs-block h3, .feats-block h3, .narr-block h3{{font-family:var(--font-serif);font-size:.98rem;font-weight:600;letter-spacing:-0.01em;margin:0 0 8px;color:var(--text);border-bottom:1px solid var(--border);padding-bottom:5px}}
table.specs{{width:100%;border-collapse:collapse;margin:0}}
table.specs tr{{border-bottom:1px solid var(--border)}}
table.specs tr:last-child{{border-bottom:none}}
table.specs th,table.specs td{{padding:8px 0;text-align:left;font-size:.83rem;vertical-align:top;text-indent:0}}
table.specs th{{width:38%;color:var(--text-2);font-weight:500;padding-right:14px;font-size:.74rem;text-transform:uppercase;letter-spacing:0.04em}}
table.specs td{{color:var(--text);font-family:var(--font-mono);font-size:.81rem;padding-left:0}}
.narr-block p{{font-size:.86rem;line-height:1.6;color:var(--text);margin:0 0 7px;text-indent:0;padding:0}}
.feats-block ul.feats{{list-style:none;padding:0;margin:0}}
.feats-block ul.feats li{{padding:5px 0 5px 18px;font-size:.84rem;color:var(--text);position:relative;line-height:1.5;text-indent:0;margin:0}}
.feats-block ul.feats li::before{{content:'';position:absolute;left:0;top:12px;width:6px;height:6px;background:var(--accent);border-radius:50%}}
.feats-block ul.feats.feats-warn li::before{{background:#956400}}

/* Footer */
.foot{{margin-top:auto;padding-top:18px;border-top:2px solid var(--text);display:flex;justify-content:space-between;align-items:flex-end;font-family:var(--font-mono);font-size:.7rem;color:var(--text-2);text-transform:uppercase;letter-spacing:0.04em;line-height:1.55}}
.foot .contact{{flex:1}}
.foot .contact b{{color:var(--text);font-weight:600;font-family:var(--font-sans);text-transform:none;letter-spacing:0}}
.foot .right{{text-align:right;color:var(--text-3)}}

/* Controles screen-only para evitar surprise (no se imprimen) */
.screen-only{{position:fixed;top:14px;right:14px;display:flex;gap:8px;z-index:99}}
.btn{{background:var(--text);color:#fff;border:none;border-radius:6px;padding:9px 16px;cursor:pointer;font-family:var(--font-sans);font-size:.78rem;font-weight:500;text-decoration:none;display:inline-flex;align-items:center;gap:6px}}
.btn:hover{{background:#333}}
.btn-secd{{background:#fff;color:var(--text);border:1px solid var(--border)}}
.btn-secd:hover{{background:var(--surface-2)}}

@media print {{
  body, .page{{background:#fff !important;margin:0;padding:0;box-shadow:none}}
  .page{{padding:20px 24px;max-width:none;width:100%}}
  .screen-only{{display:none}}
  .hero{{margin-bottom:20px;gap:24px}}
  .hero h1{{font-size:1.4rem}}
  .hero .img-wrap{{max-height:240px}}
  table.specs th, table.specs td{{padding:6px 0;font-size:.78rem}}
  table.specs td{{font-size:.76rem}}
  .feats-block ul.feats li{{padding:3px 0 3px 16px;font-size:.78rem}}
  .narr-block p{{font-size:.8rem}}
  .specs-block h3, .feats-block h3, .narr-block h3{{font-size:.9rem;margin-bottom:6px;padding-bottom:4px}}
  @page{{size:A4;margin:12mm}}
}}
</style>
</head>
<body>
<div class="screen-only">
  <button class="btn" onclick="window.print()">Imprimir / Guardar PDF</button>
  <a class="btn btn-secd" href="https://wa.me/{WA_NUMBER}?text=Hola%2C%20consulto%20por%20{code}">WhatsApp</a>
</div>

<article class="page">

  <header class="head">
    <div class="brand">
      <div class="brand-name">{SHOP_NAME}</div>
      <div class="brand-tag">{SHOP_TAGLINE}</div>
    </div>
    <div class="meta">
      <div>Ficha técnica</div>
      <div><b>{code}</b></div>
      <div>{fecha}</div>
    </div>
  </header>

  <section class="hero">
    <div class="img-wrap">
      <img src="{img_url}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
      <div class="img-fallback" style="display:none">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
        <div>Sin foto disponible</div>
      </div>
    </div>
    <div>
      <h1>{name}</h1>
      <span class="ref">REF · {code}</span>
      <div class="price">₡ {price:,.0f} <small>IVA incluido · envío Costa Rica</small></div>
    </div>
  </section>

  <section class="specs-block">
    <h3>Especificaciones técnicas</h3>
    <table class="specs">{specs_rows}</table>
  </section>

  {narrative_html}
  {feats_html}
  {apps_html}
  {notes_html}

  <footer class="foot">
    <div class="contact">
      <div><b>WhatsApp:</b> +506 6406 3012</div>
      <div><b>Web:</b> {WEB_URL.replace('https://','')}</div>
    </div>
    <div class="right">
      Generado {fecha}<br>{SHOP_NAME} · Costa Rica
    </div>
  </footer>

</article>

<script>
// Esperar a la imagen del producto antes de imprimir, con safety timeout
(function(){{
  function doPrint(){{ try{{ window.print(); }}catch(e){{}} }}
  window.addEventListener('load', function(){{
    var img = document.querySelector('.hero .img-wrap img');
    if (img && !img.complete) {{
      var done = false;
      var fire = function(){{ if(!done){{ done=true; setTimeout(doPrint, 250); }} }};
      img.addEventListener('load', fire);
      img.addEventListener('error', fire);
      setTimeout(fire, 4500); // safety: si la imagen tarda demasiado, igual imprimir
    }} else {{
      setTimeout(doPrint, 300);
    }}
  }});
}})();
</script>
</body></html>"""


# ──────────────────────────── PIPELINE ────────────────────────────

def odoo_connect():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_KEY, {})
    if not uid:
        sys.exit("Auth fallida")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    def call(model, method, args, kwargs=None):
        return models.execute_kw(ODOO_DB, uid, ODOO_KEY, model, method, args, kwargs or {})
    return call


def fetch_products(call, code=None, limit=None, min_price=None, max_price=None):
    """Lista product.template publicados, filtrando por precio."""
    if code:
        domain = [('default_code','=',code)]
    else:
        domain = [('sale_ok','=',True),('is_published','=',True)]
        if min_price is not None:
            domain.append(('list_price','>=',min_price))
        if max_price is not None:
            domain.append(('list_price','<',max_price))
    ids = call('product.template','search',[domain])
    if limit:
        ids = ids[:limit]
    print(f'  fetching {len(ids)} products …')
    rows = []
    BATCH = 50
    for i in range(0, len(ids), BATCH):
        rows += call('product.template','read',[ids[i:i+BATCH],
            ['id','name','default_code','list_price','description_sale','website_description']])
    rows.sort(key=lambda r: -r['list_price'])
    return rows


# Backward-compat
def fetch_premium(call, code=None, limit=None):
    return fetch_products(call, code=code, limit=limit, min_price=PRICE_PREMIUM_MIN)


def backup(rows):
    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    path = os.path.join(BACKUP_DIR, f'website_descriptions_{ts}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump([{
            'id': r['id'],
            'default_code': r.get('default_code'),
            'name': r['name'],
            'list_price': r['list_price'],
            'website_description': r.get('website_description') or '',
        } for r in rows], f, ensure_ascii=False, indent=2)
    print(f'  backup → {path} ({len(rows)} productos)')
    return path


def slugify(s):
    s = s or 'sin-ref'
    s = re.sub(r'[^A-Za-z0-9_-]+', '-', s)
    return s.strip('-') or 'sin-ref'


def generate(rows):
    index_items = []
    for r in rows:
        code = r.get('default_code') or f'ID{r["id"]}'
        parsed = parse_description(r.get('website_description') or '')
        kpis = detect_kpis(parsed['specs'], r['name'])
        html_body = render(r, parsed, kpis)

        full_doc = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(r['name'])} · Paracarpinteros</title>
<style>body{{margin:0;background:#FBFBFA;font-family:-apple-system,system-ui,sans-serif}}.mock-banner{{background:#FBF3DB;color:#956400;padding:10px 18px;text-align:center;font-size:.78rem;font-weight:500;text-transform:uppercase;letter-spacing:0.06em}}</style>
</head><body>
<div class="mock-banner">Preview · {html.escape(code)} · {html.escape(r['name'][:70])} — no es la URL real</div>
{html_body}
</body></html>"""

        out_path = os.path.join(OUT_DIR, f'{slugify(code)}.html')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(full_doc)

        n_specs = len(parsed['specs'])
        n_videos = len(parsed['videos'])
        n_bullets = len(parsed['bullets'])
        n_paragraphs = len(parsed['paragraphs'])
        index_items.append((code, r['name'], r['list_price'], n_specs, n_videos, n_bullets, n_paragraphs, len(html_body)))
        print(f"  ✓ {code:<14} ₡{r['list_price']:>10,.0f}  specs={n_specs:>2} vid={n_videos} bul={n_bullets:>2} → {os.path.basename(out_path)}")

    # Index navegable
    rows_html = ''.join(
        f'<tr><td><a href="{slugify(c)}.html">{html.escape(c)}</a></td>'
        f'<td>{html.escape(n[:70])}</td><td style="text-align:right;font-family:monospace">₡{p:,.0f}</td>'
        f'<td style="text-align:center">{s}</td><td style="text-align:center">{v}</td>'
        f'<td style="text-align:center">{b}</td><td style="text-align:center">{pp}</td>'
        f'<td style="text-align:right;font-family:monospace">{h//1024}k</td></tr>'
        for c, n, p, s, v, b, pp, h in index_items
    )
    idx = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<title>Index fichas premium · Paracarpinteros</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;background:#FBFBFA;color:#111;margin:24px;max-width:1200px}}
h1{{font-family:Georgia,serif;font-weight:500;letter-spacing:-0.02em}}
table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #EAEAEA;border-radius:8px;overflow:hidden;margin-top:16px}}
th,td{{padding:9px 12px;text-align:left;font-size:.85rem;border-bottom:1px solid #EAEAEA}}
th{{background:#F7F6F3;font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:#6F6F6E;font-weight:600}}
tr:last-child td{{border-bottom:none}}
a{{color:#1F6C9F;text-decoration:none}}a:hover{{text-decoration:underline}}
.intro{{color:#6F6F6E;font-size:.92rem;line-height:1.55;max-width:60ch}}</style>
</head><body>
<h1>Fichas premium generadas · {len(index_items)} productos</h1>
<p class="intro">Preview local de las fichas re-empaquetadas con la plantilla minimalist editorial. El backup del estado anterior está en <code>backup/</code>. Para subir a producción ejecutar el script de upload (aparte).</p>
<table>
<thead><tr><th>REF</th><th>Nombre</th><th>Precio</th><th>Specs</th><th>Vid</th><th>Bul</th><th>Párr</th><th>Tamaño</th></tr></thead>
<tbody>{rows_html}</tbody>
</table></body></html>"""
    idx_path = os.path.join(OUT_DIR, 'index.html')
    with open(idx_path, 'w', encoding='utf-8') as f:
        f.write(idx)
    print(f'\n  index → {idx_path}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('code', nargs='?', help='Default code para procesar uno solo')
    ap.add_argument('--limit', type=int, help='Limitar a N productos')
    args = ap.parse_args()

    print('Conectando a Odoo…')
    call = odoo_connect()
    print(f'Buscando productos premium (>=₡{PRICE_PREMIUM_MIN:,})…')
    rows = fetch_premium(call, code=args.code, limit=args.limit)
    if not rows:
        print('No hay productos')
        return 1
    print(f'\nBackup …')
    backup(rows)
    print(f'\nGenerando fichas …')
    generate(rows)
    print(f'\nListo. Abrir: {OUT_DIR}/index.html')
    return 0


if __name__ == '__main__':
    sys.exit(main())
