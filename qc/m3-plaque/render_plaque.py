#!/usr/bin/env python3
"""
render_plaque.py — Rend la plaque signalétique M3 @300 dpi en Pillow, TEMPLATE-DRIVEN.

Sortie : image L (blanc = contenu à imprimer ; sur média noir + ruban blanc = trait blanc).
Le layout vient d'un TEMPLATE (liste d'éléments en mm + placeholders {serial},{model},…),
éditable dans Label Expert et sauvé sur label.yumi-lab.com. Défaut embarqué si pas de template.

Autonome : Pillow + qrcodegen (vendorisé) + polices DejaVu + logos PNG blancs (assets/).
"""
import os, json
from PIL import Image, ImageDraw, ImageFont
import qrcodegen

DPI = 300
def MM(mm): return round(float(mm) / 25.4 * DPI)

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets')
FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_REG  = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_MONO = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf'
if not os.path.exists(FONT_BOLD):  # repli macOS pour tester le rendu localement
    for c in ('/System/Library/Fonts/Supplemental/Arial Bold.ttf',):
        if os.path.exists(c): FONT_BOLD = FONT_REG = c
    for c in ('/System/Library/Fonts/Menlo.ttc',):
        if os.path.exists(c): FONT_MONO = c

# ── Template par défaut : plaque signalétique conforme (mm). L'éditeur peut le remplacer. ──
LOGO_AR = {'yumi': 3.87, 'ce': 1.4, 'ukca': 1.0, 'fcc': 1.19, 'weee': 1.0, 'rohs': 1.0, 'classii': 1.2}

def _logo_row(keys, x0, y, wav, h):
    ws = [round(h * LOGO_AR.get(k, 1), 2) for k in keys]
    s = sum(ws); gap = min(6, (wav - s) / (len(keys) - 1)) if len(keys) > 1 else 0
    lx = x0 + max(0, (wav - (s + gap * (len(keys) - 1))) / 2); out = []
    for k, wd in zip(keys, ws):
        out.append({'t': 'logo', 'logo': k, 'x': round(lx, 2), 'y': y, 'w': wd, 'h': h}); lx += wd + gap
    return out

def default_template():
    els = [
        {'t': 'logo', 'logo': 'yumi', 'x': 21, 'y': 4, 'w': 33, 'h': 8.5},
        {'t': 'text', 'c': 'Legal Manufacturer:', 'x': 4, 'y': 14.5, 'sz': 2.8, 'weight': '600'},
        {'t': 'text', 'c': 'YUMi TECHNOLOGY TRADING LIMITED', 'x': 4, 'y': 17.8, 'sz': 3.0},
        {'t': 'text', 'c': 'UNIT 9 BLK D 6/F FUK SING FACTORY BLDG', 'x': 4, 'y': 21.0, 'sz': 2.4, 'weight': 'normal'},
        {'t': 'text', 'c': '2 WALNUT ST', 'x': 4, 'y': 23.8, 'sz': 2.4, 'weight': 'normal'},
        {'t': 'text', 'c': 'TAI KOK TSUI', 'x': 4, 'y': 26.6, 'sz': 2.4, 'weight': 'normal'},
        {'t': 'text', 'c': 'HONG KONG', 'x': 4, 'y': 29.4, 'sz': 2.4, 'weight': 'normal'},
        {'t': 'line', 'x': 4, 'y': 33.5, 'w': 67, 'thick': 0.3},
        {'t': 'text', 'c': 'Product: 3D Printer', 'x': 4, 'y': 36, 'sz': 3.2, 'weight': 'normal'},
        {'t': 'text', 'c': 'Model: {model}', 'x': 4, 'y': 40.5, 'sz': 3.6},
        {'t': 'serial', 'c': 'S/N: {serial}', 'x': 4, 'y': 46, 'sz': 3.2, 'fitw': 47, 'mono': True},
        {'t': 'qr', 'c': '{qr}', 'x': 53, 'y': 39, 'sz': 17},
        {'t': 'text', 'c': 'Input: 220–240 V~, 50/60 Hz, {amps} A', 'x': 4, 'y': 58, 'sz': 2.9, 'weight': 'normal'},
        {'t': 'text', 'c': 'Made in China', 'x': 4, 'y': 62.5, 'sz': 3.2},
        {'t': 'line', 'x': 4, 'y': 67, 'w': 67, 'thick': 0.3},
    ]
    els += _logo_row(['ce', 'ukca', 'fcc'], 4, 70, 67, 10.5)
    els += _logo_row(['weee', 'rohs', 'classii'], 4, 84, 67, 10.5)
    els += [
        {'t': 'line', 'x': 4, 'y': 97, 'w': 67, 'thick': 0.3},
        {'t': 'qr', 'c': '{qr2}', 'x': 6, 'y': 99, 'sz': 15},
        {'t': 'text', 'c': 'User Guide', 'x': 13.5, 'y': 114.6, 'sz': 2.6, 'align': 'center'},
        {'t': 'text', 'c': 'www.yumi-lab.com', 'x': 28, 'y': 105, 'sz': 3.4},
    ]
    return {'version': 1, 'label': {'w_mm': 75, 'h_mm': 120}, 'elements': els}

DEFAULT_TEMPLATE = default_template()

_logocache = {}
def logo(name):
    if name not in _logocache:
        _logocache[name] = Image.open(os.path.join(ASSETS, name + '.png')).convert('RGBA')
    return _logocache[name]

_fontcache = {}
def font(path, mm):
    key = (path, MM(mm))
    if key not in _fontcache:
        _fontcache[key] = ImageFont.truetype(path, MM(mm))
    return _fontcache[key]

def _fontpath(e):
    if e.get('mono') or e.get('t') == 'serial': return FONT_MONO
    return FONT_REG if str(e.get('weight', 'bold')) in ('normal', 'regular', '400') else FONT_BOLD

def hline(d, x, y, w, thick):
    d.line([x, y, x + w, y], fill=255, width=max(1, MM(thick)))

def draw_logo(img, name, x, y, w, h):
    try: lg = logo(name)
    except Exception: return
    ar = lg.width / lg.height; dw, dh = w, w / ar
    if dh > h: dh, dw = h, h * ar
    lg2 = lg.resize((max(1, round(dw)), max(1, round(dh))), Image.LANCZOS)
    img.paste(lg2, (round(x + (w - dw) / 2), round(y + (h - dh) / 2)), lg2)

def draw_serial(d, x, y, s, maxw, maxmm, path):
    mm = maxmm
    while mm > 2.6 and d.textlength(s, font=font(path, mm)) > maxw:
        mm -= 0.2
    d.text((x, y), s, font=font(path, mm), fill=255, anchor='la')

def draw_qr(img, x, y, size, content):
    qr = qrcodegen.QrCode.encode_text(str(content), qrcodegen.QrCode.Ecc.MEDIUM)
    n = qr.get_size(); cell = max(1, size // (n + 2)); s = cell * (n + 2)
    d = ImageDraw.Draw(img)
    d.rectangle([x, y, x + s - 1, y + s - 1], fill=255)
    for r in range(n):
        for c in range(n):
            if qr.get_module(c, r):
                px, py = x + (c + 1) * cell, y + (r + 1) * cell
                d.rectangle([px, py, px + cell - 1, py + cell - 1], fill=0)

def _subst(s, data):
    for k, v in data.items():
        s = s.replace('{' + k + '}', str(v))
    return s

def render(data, template=None):
    tpl = template or DEFAULT_TEMPLATE
    lbl = tpl.get('label', {'w_mm': 75, 'h_mm': 120})
    W, H = MM(lbl['w_mm']), MM(lbl['h_mm'])
    img = Image.new('L', (W, H), 0)
    d = ImageDraw.Draw(img)
    for e in tpl.get('elements', []):
        t = e.get('t')
        if t == 'frame':
            d.rectangle([MM(e['x']), MM(e['y']), MM(e['x'] + e['w']) - 1, MM(e['y'] + e['h']) - 1],
                        outline=255, width=max(1, MM(e.get('thick', 0.4))))
        elif t == 'line':
            hline(d, MM(e['x']), MM(e['y']), MM(e['w']), e.get('thick', 0.3))
        elif t == 'logo':
            draw_logo(img, e['logo'], MM(e['x']), MM(e['y']), MM(e['w']), MM(e['h']))
        elif t == 'qr':
            draw_qr(img, MM(e['x']), MM(e['y']), MM(e.get('sz', e.get('w', 19))), _subst(e.get('c', '{qr}'), data))
        elif t in ('text', 'serial'):
            s = _subst(e.get('c', ''), data); fp = _fontpath(e); sz = e.get('sz', 4)
            if t == 'serial' and e.get('fitw'):
                draw_serial(d, MM(e['x']), MM(e['y']), s, MM(e['fitw']), sz, fp)
            else:
                # AUTO-FIT : si le texte déborde de l'étiquette (police pad DejaVu plus large
                # que l'aperçu, ou édition libre), on réduit la police jusqu'à ce qu'il rentre.
                align = e.get('align'); x = MM(e['x'])
                avail = (2 * min(x, W - x) if align == 'center' else W - x) - MM(1.2)
                while sz > 2.4 and d.textlength(s, font=font(fp, sz)) > avail:
                    sz -= 0.1
                d.text((x, MM(e['y'])), s, font=font(fp, sz), fill=255,
                       anchor=('ma' if align == 'center' else 'la'))
    return img

def load_template(src):
    """Charge un template depuis un fichier ou une URL http(s). None si échec."""
    try:
        if str(src).startswith('http'):
            import urllib.request
            with urllib.request.urlopen(src, timeout=6) as r:
                return json.load(r)
        with open(src, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

if __name__ == '__main__':
    sample = {'model': 'C235', 'serial': '570039001051353135323934',
              'voltage': '220–240 V~', 'power': '420 W', 'amps': '2.0', 'lot': '20260616',
              'origin': 'Made in China', 'maker': 'Yumi Lab · yumi-lab.com',
              'qr': 'https://qc.yumi-lab.com/report/570039001051353135323934',
              'qr2': 'https://go.yumi-lab.com'}
    out = os.environ.get('OUT', '/tmp/plaque_test.png')
    render(sample).save(out); print('écrit', out)
