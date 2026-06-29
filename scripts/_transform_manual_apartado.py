#!/usr/bin/env python3
# Reestiliza el apartado "Manual de uso"/"Guía de uso" de las fichas .pcf
# al bloque .manual (timeline + vídeo de configuración integrado).
# DETERMINISTA: reusa el texto y los vídeos existentes; NO inventa contenido.
import sys, os, re, time
sys.path.insert(0, os.path.dirname(__file__))
from _env import load_project_env; load_project_env()
import xmlrpc.client

DRY = '--apply' not in sys.argv

CSS_BLOCK = """/* === Apartado Manual de uso: bloque distinguido === */
.pcf .manual{background:var(--surface-2);border:1px solid var(--border-2);border-radius:18px;padding:32px 28px;margin:52px 0;position:relative;overflow:hidden;box-shadow:0 1px 3px rgba(17,17,17,.04)}
.pcf .manual::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,var(--purple-fg),var(--blue-fg))}
.pcf .manual-eyebrow{display:inline-flex;align-items:center;gap:7px;background:var(--purple-bg);color:var(--purple-fg);font-size:.66rem;font-weight:700;text-transform:uppercase;letter-spacing:0.09em;padding:6px 13px;border-radius:9999px}
.pcf .manual-eyebrow svg{width:14px;height:14px;stroke:var(--purple-fg)}
.pcf .manual h2{font-family:var(--font-serif);font-size:1.7rem;font-weight:500;letter-spacing:-0.025em;line-height:1.1;margin:16px 0 6px}
.pcf .manual .sec-sub{font-size:.9rem;color:var(--text-2);margin-bottom:24px;max-width:62ch;line-height:1.55}
.pcf .manual .video-wrap{margin:0 0 28px}
.pcf .steps{position:relative;display:grid;gap:0;margin:0}
.pcf .steps .step{display:flex;gap:18px;padding:0 0 24px;position:relative}
.pcf .steps .step:last-child{padding-bottom:0}
.pcf .steps .step::before{content:'';position:absolute;left:18px;top:40px;bottom:-2px;width:2px;background:var(--border-2)}
.pcf .steps .step:last-child::before{display:none}
.pcf .steps .step-n{flex-shrink:0;width:38px;height:38px;border-radius:50%;background:var(--surface);border:2px solid var(--purple-fg);color:var(--purple-fg);font-family:var(--font-serif);font-weight:600;font-size:1.05rem;display:flex;align-items:center;justify-content:center;line-height:1;z-index:1}
.pcf .steps .step-body{padding-top:5px}
.pcf .steps .step-body h4{margin:0 0 4px;font-size:1rem;font-weight:600;letter-spacing:-0.01em;color:var(--text)}
.pcf .steps .step-body p{margin:0;font-size:.92rem;color:var(--text-2);line-height:1.6}
@media(max-width:600px){.pcf .manual{padding:24px 18px}}
</style>"""

BOOK_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
            '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>')

SEC_RE = re.compile(r'<section\b[^>]*>.*?</section>', re.S)
H2_RE = re.compile(r'<h2>(.*?)</h2>', re.S)
SUB_RE = re.compile(r'<p class="sec-sub">(.*?)</p>', re.S)
GUIDEBLOCK_RE = re.compile(r'<div class="guide">.*?</div>\s*</section>', re.S)  # no usado, ver abajo
VIDEOWRAP_RE = re.compile(r'<div class="video-wrap">.*?</div>', re.S)
STEP_RE = re.compile(r'<div class="guide-step"><div class="guide-num">(.*?)</div><div>(.*?)</div></div>', re.S)

def convert_steps(guide_html):
    # guide_html: el <div class="guide">...</div>
    inner = STEP_RE.sub(r'<div class="step"><div class="step-n">\1</div><div class="step-body">\2</div></div>', guide_html)
    inner = inner.replace('<div class="guide">', '<div class="steps">', 1)
    return inner

def transform(d):
    if 'class="manual"' in d:
        return d, 'YA-HECHO'
    secs = list(SEC_RE.finditer(d))
    guide_sec = None; video_sec = None
    for m in secs:
        s = m.group(0)
        if guide_sec is None and 'class="guide"' in s:
            guide_sec = m
        h2 = H2_RE.search(s)
        if h2 and video_sec is None and re.search(r'video oficial|configurarla', h2.group(1), re.I) and 'video-wrap' in s:
            video_sec = m
    if guide_sec is None:
        return d, 'SIN-GUIDE'
    g = guide_sec.group(0)
    g_h2 = (H2_RE.search(g).group(1).strip() if H2_RE.search(g) else 'Manual de uso')
    g_sub = (SUB_RE.search(g).group(1).strip() if SUB_RE.search(g) else '')
    guide_div = re.search(r'<div class="guide">.*?</div>\s*</section>', g, re.S)
    # extraer el div.guide completo (equilibrando el cierre)
    gd = re.search(r'(<div class="guide">.*?)</section>', g, re.S).group(1)
    gd = gd.rstrip()
    if gd.endswith('</div>'):
        pass
    steps_html = convert_steps(gd)
    # vídeo a integrar
    video_html = ''
    if video_sec is not None:
        vw = VIDEOWRAP_RE.search(video_sec.group(0))
        if vw:
            video_html = '    ' + vw.group(0) + '\n'
    # construir apartado
    sub_html = f'    <p class="sec-sub">{g_sub}</p>\n' if g_sub else ''
    new_sec = (
        '<section class="manual">\n'
        f'    <span class="manual-eyebrow">{BOOK_SVG} {g_h2}</span>\n'
        '    <h2>C&oacute;mo se usa</h2>\n'
        f'{sub_html}'
        f'{video_html}'
        f'    {steps_html}\n'
        '  </section>'
    )
    out = d.replace(g, new_sec, 1)
    # eliminar la sección de vídeo de configuración ya integrada
    if video_sec is not None:
        out = out.replace(video_sec.group(0), '', 1)
    # insertar CSS
    if '.pcf .manual{' not in out:
        out = out.replace('</style>', CSS_BLOCK, 1)
    merged = 'video+steps' if video_sec is not None else 'solo-steps'
    return out, f'OK ({merged}, h2="{g_h2}")'

def main():
    url=os.environ['ODOO_URL']; db=os.environ['ODOO_DB']; user=os.environ['ODOO_USERNAME']; key=os.environ['ODOO_API_KEY']
    common=xmlrpc.client.ServerProxy(url+'/xmlrpc/2/common', allow_none=True)
    uid=common.authenticate(db,user,key,{})
    models=xmlrpc.client.ServerProxy(url+'/xmlrpc/2/object', allow_none=True)
    dom=['|','|',('website_description','like','class="guide"'),('website_description','ilike','video oficial'),('website_description','like','class="manual"')]
    ids=models.execute_kw(db,uid,key,'product.template','search',[dom])
    recs=models.execute_kw(db,uid,key,'product.template','read',[ids],{'fields':['id','default_code','name','website_description']})
    recs.sort(key=lambda r:(r['default_code'] or 'zzz'))
    stamp=time.strftime('%Y%m%d-%H%M%S')
    bdir='scripts/_backups'
    n_ok=0
    for r in recs:
        d=r['website_description'] or ''
        out,status=transform(d)
        code=r['default_code'] or f"id{r['id']}"
        # métricas de seguridad
        steps_old=d.count('class="guide-step"'); steps_new=out.count('class="step"')
        if_old=d.count('<iframe'); if_new=out.count('<iframe')
        leftover_guide='class="guide"' in out
        leftover_vof=bool(re.search(r'<h2>[^<]*video oficial[^<]*</h2>', out, re.I))
        changed = out!=d
        flag=''
        if status.startswith('OK'):
            if steps_new!=steps_old: flag+=' !!PASOS('+str(steps_old)+'->'+str(steps_new)+')'
            if if_new!=if_old and 'video+steps' not in status: flag+=' !!IFRAMES'
            if leftover_guide: flag+=' !!QUEDA-GUIDE'
            if leftover_vof: flag+=' !!QUEDA-VOFICIAL'
        print(f'{code:<8} {status:<34} pasos={steps_old} iframes:{if_old}->{if_new} chg={int(changed)}{flag}')
        if not DRY and status.startswith('OK') and changed and not flag:
            open(f'{bdir}/backup_{code}_websdesc_{stamp}.html','w').write(d)
            models.execute_kw(db,uid,key,'product.template','write',[[r['id']],{'website_description':out}])
            n_ok+=1
        # guardar muestra para revisar en dry-run
        if DRY and code in ('A009','A2340','A043'):
            open(f'/tmp/sample_{code}.html','w').write(out)
    print('='*70)
    print('MODO:', 'DRY-RUN (nada escrito)' if DRY else f'APLICADO: {n_ok} fichas escritas (backups en {bdir})')

if __name__=='__main__':
    main()
