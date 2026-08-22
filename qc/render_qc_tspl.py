#!/usr/bin/env python3
"""
render_qc_tspl.py — Étiquettes QC PASS/FAIL, TSPL brut pour POS80L, TEMPLATE-DRIVEN.

Même principe que m3-driver/render_plaque.py (repo YUMI-POS-Printer) pour la plaque M3 :
le layout vient d'un TEMPLATE (liste d'éléments en mm + placeholders {code},{qc_model}…),
éditable dans Label Expert (boutons « QC label — … » du panneau QC Factory) et sauvé sur
label.yumi-lab.com. Repli silencieux sur un gabarit par défaut codé en dur si le fetch
échoue (réseau down, 404, JSON invalide) — jamais de blocage du banc QC.

v2 (22/08/2026) — rendu en IMAGE (Pillow), comme la plaque M3, plus en TSPL texte natif.
La v1 (TEXT/QRCODE natifs) ne pouvait pas reproduire le gras, ni la taille exacte du QR
(cellule approximée sans connaître le nombre de modules) : l'étiquette ne collait jamais
pixel pour pixel à ce que montre Label Expert. Ici on compose TOUT sur un même canevas
Pillow (texte avec vraie police + gras, QR encodé et dessiné module par module comme
render_plaque.py, logos, cadres) puis on envoie UN SEUL bitmap TSPL — fidélité garantie
au prix de perdre le texte/QR "natif" scannable par le firmware (un QR en bitmap scanne
tout aussi bien).

2 fichiers, un par taille physique de média (contrainte dure : SIZE/GAP en dépend) :
  qc-label-yms.json (50×30mm, boîtiers YMS), qc-label-machine.json (39×39mm, machines).
Chacun porte 2 sections `pass`/`fail`.

Autonome : Pillow + qrcodegen (vendorisé, copie de m3-driver/qrcodegen.py) + assets/ (PNG,
copie des logos M3) + polices DejaVu (repli système si absentes, pour tester hors pad).
"""
import base64, io, json, os, sys
from PIL import Image, ImageDraw, ImageFont

DPMM = 8  # 203 dpi ~ 8 dots/mm (POS80L / HS-584)
# realpath, PAS abspath : ce module est chargé sur le pad via le symlink
# ks_includes/render_qc_tspl.py -> yumi-config/qc/render_qc_tspl.py. abspath()
# ne résout PAS les symlinks, donc HERE pointait vers ks_includes/ (pas de
# assets/ là-bas) -> chaque logo échouait en silence (except Exception: return
# dans _draw_logo). Confirmé sur le pad le 22/08 : aucun logo ne sortait.
HERE = os.path.dirname(os.path.realpath(__file__))
ASSETS = os.path.join(HERE, "assets")

# qrcodegen.py est vendorisé À CÔTÉ de ce fichier (pas un paquet pip) — un simple
# "import qrcodegen" échoue dès que ce module est chargé comme qc.render_qc_tspl
# depuis un autre repertoire (le dossier qc/ n'est alors pas sur sys.path).
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import qrcodegen

MEDIA = {"yms": (50, 30), "machine": (39, 39)}

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
if not os.path.exists(FONT_REG):  # repli macOS, pour développer/tester hors pad
    for _c in ("/System/Library/Fonts/Supplemental/Arial.ttf",):
        if os.path.exists(_c):
            FONT_REG = _c
    for _c in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",):
        if os.path.exists(_c):
            FONT_BOLD = _c


def DOT(mm):
    return max(0, round(float(mm) * DPMM))


_FONT_CACHE = {}


def _font(path, sz_mm):
    """Police TrueType au corps exact demandé (en dots, donc directement en pixels sur un
    canevas à 8 dots/mm) — plus d'arrondi sur 4 tailles bitmap fixes comme en v1."""
    px = max(6, DOT(sz_mm))
    key = (path, px)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, px)
        except Exception:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def _subst(s, data):
    for k, v in data.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def _draw_qr(img, x, y, size_dots, content):
    """Encode le QR puis le colle à la taille EXACTE demandée (size_dots). Rendu à une
    résolution intermédiaire (plusieurs px/module) puis redimensionné — une simple division
    entière (cell = size // modules) arrondit TOUJOURS vers le bas et sortait un QR
    visiblement plus petit que `sz` à l'impression (jusqu'à ~15% selon le nombre de modules,
    constaté le 22/08/2026)."""
    qr = qrcodegen.QrCode.encode_text(str(content), qrcodegen.QrCode.Ecc.MEDIUM)
    n = qr.get_size()
    quiet = 1  # marge d'un module de chaque côté, même convention que render_plaque.py
    total = n + quiet * 2
    scale = 10  # px/module au rendu intermédiaire -> redimensionnement précis, pas quantifié
    hires = Image.new("L", (total * scale, total * scale), 255)
    hd = ImageDraw.Draw(hires)
    for r in range(n):
        for c in range(n):
            if qr.get_module(c, r):
                px, py = (c + quiet) * scale, (r + quiet) * scale
                hd.rectangle([px, py, px + scale - 1, py + scale - 1], fill=0)
    # NEAREST : garde les modules nets (pas de flou d'antialiasing qui nuirait au scan).
    qr_img = hires.resize((size_dots, size_dots), Image.NEAREST)
    img.paste(qr_img, (x, y))


def _draw_logo(img, x_mm, y_mm, w_mm, h_mm, logo_name=None, src=None):
    """Colle un logo (asset PNG connu ou data URL) sur le canevas — silencieux si
    introuvable/invalide, un logo manquant ne doit jamais bloquer l'étiquette."""
    try:
        if logo_name:
            path = os.path.join(ASSETS, logo_name + ".png")
            src_img = Image.open(path).convert("RGBA")
        elif src and str(src).startswith("data:"):
            b64 = src.split(",", 1)[-1]
            src_img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
        else:
            return
    except Exception:
        return
    W, H = DOT(w_mm), DOT(h_mm)
    if W <= 0 or H <= 0:
        return
    # C'est la SILHOUETTE (canal alpha) qui fait l'encre, jamais la couleur de l'asset — les
    # PNG viennent de la plaque M3 (dessinés en blanc sur fond transparent, ruban blanc/plaque
    # noire) : composer sur blanc et lire la luminance les rendrait blancs sur blanc ici.
    alpha = src_img.getchannel("A").resize((W, H))
    black = Image.new("L", (W, H), 0)
    img.paste(black, (DOT(x_mm), DOT(y_mm)), alpha)


def render(section_elements, data, w_mm, h_mm):
    """Compose TOUS les éléments d'une section (pass OU fail) sur UN canevas Pillow, puis
    l'encode en TSPL BITMAP unique. `data` porte les placeholders (code, qc_model, date, qr,
    bench_position, fail_reason, failed_tests…)."""
    W, H = DOT(w_mm), DOT(h_mm)
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)

    for e in section_elements:
        t = e.get("t")
        if t == "frame":
            x1, y1 = DOT(e["x"]), DOT(e["y"])
            x2, y2 = DOT(e["x"] + e["w"]), DOT(e["y"] + e["h"])
            if e.get("thick"):
                d.rectangle([x1, y1, x2, y2], outline=0, width=max(1, DOT(e["thick"])))
            elif e.get("fill"):
                d.rectangle([x1, y1, x2, y2], fill=0)
        elif t == "line":
            x1, y1 = DOT(e["x"]), DOT(e["y"])
            x2 = DOT(e["x"] + e["w"])
            thick = max(1, DOT(e.get("thick", 0.3)))
            d.rectangle([x1, y1, x2, y1 + thick], fill=0)
        elif t == "qr":
            content = _subst(e.get("c", "{qr}"), data)
            _draw_qr(img, DOT(e["x"]), DOT(e["y"]), DOT(e.get("sz", 14)), content)
        elif t == "text":
            s = _subst(e.get("c", ""), data)
            font = _font(FONT_BOLD if e.get("weight") == "bold" else FONT_REG, e.get("sz", 2.6))
            x = DOT(e["x"])
            if e.get("align") == "center":
                x = max(0, x - round(d.textlength(s, font=font) / 2))
            d.text((x, DOT(e["y"])), s, font=font, fill=0)
        elif t == "list":
            items = data.get(e.get("key", "failed_tests")) or []
            max_lines = e.get("max_lines", 8)
            if len(items) <= max_lines:
                shown = items
            else:
                shown = list(items[:max_lines - 1]) + [
                    e.get("more_fmt", "+{n} autres").replace("{n}", str(len(items) - (max_lines - 1)))
                ]
            font = _font(FONT_REG, e.get("sz", 1.6))
            y = e["y"]
            for line in shown:
                d.text((DOT(e["x"]), DOT(y)), str(line), font=font, fill=0)
                y += e.get("line_h", 2.25)
        elif t == "logo":
            # L'éditeur Label Expert sauve les logos en {"src": "ce.svg"} (nom de fichier nu,
            # pas une data: URL) — la convention M3 est {"logo": "ce"}. On dérive le nom
            # d'asset local depuis "src" quand "logo" est absent.
            name = e.get("logo")
            src = e.get("src")
            if not name and src and not str(src).startswith("data:"):
                name = os.path.splitext(os.path.basename(str(src)))[0]
            _draw_logo(img, e["x"], e["y"], e["w"], e["h"], name, src)

    return _to_tspl(img, w_mm, h_mm)


def _to_tspl(img, w_mm, h_mm, threshold=160):
    """Encode le canevas final en UN SEUL bloc TSPL BITMAP (mode 0, bit=1 -> blanc/pas
    d'impression, bit=0 -> noir/point imprimé — même convention que pos80l-bridge.py)."""
    W, H = img.size
    px = img.load()
    wbytes = (W + 7) // 8
    data = bytearray(b"\xff" * (wbytes * H))
    for y in range(H):
        for x in range(W):
            if px[x, y] < threshold:  # sombre => encre => bit à 0
                data[y * wbytes + (x >> 3)] &= ~(0x80 >> (x & 7)) & 0xFF
    head = [
        "SIZE %d mm,%d mm" % (w_mm, h_mm),
        "GAP 2 mm,0 mm",
        "DIRECTION 1",
        "SHIFT 8",
        "SET PEEL ON",
        "CLS",
    ]
    out = ("\r\n".join(head) + "\r\n").encode("ascii")
    out += ("BITMAP 0,0,%d,%d,0," % (wbytes, H)).encode("ascii")
    out += bytes(data)
    out += b"\r\n"
    out += b"PRINT 1,1\r\n"
    return out


def load_template(src):
    """Charge un template depuis une URL http(s) (timeout 6s) ou un fichier. None si échec —
    le pad doit TOUJOURS pouvoir imprimer même si label.yumi-lab.com est injoignable."""
    try:
        if str(src).startswith("http"):
            import urllib.request
            with urllib.request.urlopen(src, timeout=6) as r:
                return json.load(r)
        with open(src, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── Gabarits par défaut : le repli quand label.yumi-lab.com est injoignable. IDENTIQUES aux
# DEFAULTS de qc-plaque.js — les deux se modifient ENSEMBLE, sinon le pad imprime autre chose
# que ce que l'éditeur montre le jour où le réseau tombe.
# YMS = le standard figé par Nicolas le 2026-08-21 (logos de conformité, mention d'origine,
# ligne d'alimentation avec le symbole courant continu), FAIL corrigé le 22/08 ({qc_model}
# au lieu de "PRO V1.0" en dur). MACHINE = conversion mm du TSPL en dur historique de
# qc_yms.py::build_label_tspl (dots/8 = mm), pas encore repris à la main. ──
DEFAULTS = {
    "yms": {
        "pass": [
            {"t": "qr", "x": 2.4, "y": 7.23, "sz": 15, "c": "{qr}"},
            {"t": "text", "x": 18.93, "y": 6.34, "sz": 3.6, "weight": "bold", "c": "QC PASS"},
            {"t": "text", "x": 19.01, "y": 2.94, "sz": 2.6, "weight": "bold", "c": "{qc_model}"},
            {"t": "text", "x": 2.28, "y": 22.69, "sz": 1.6, "weight": "bold", "c": "{code}"},
            {"t": "logo", "logo": "ce", "x": 18.81, "y": 16.16, "w": 6.61, "h": 4.72},
            {"t": "logo", "logo": "ukca", "x": 25.91, "y": 15.43, "w": 6.61, "h": 6.61},
            {"t": "logo", "logo": "fcc", "x": 32.69, "y": 16.06, "w": 6.61, "h": 5.55},
            {"t": "logo", "logo": "weee", "x": 39.98, "y": 22.5, "w": 6.61, "h": 6.61},
            {"t": "logo", "logo": "rohs", "x": 40.03, "y": 15.52, "w": 6.6, "h": 6.61},
            {"t": "logo", "logo": "yumi", "x": 2.12, "y": 1.8, "w": 15.82, "h": 4.09},
            {"t": "text", "x": 2.22, "y": 24.68, "sz": 3.7, "c": "Made in china"},
            {"t": "text", "x": 19.35, "y": 11.37, "sz": 2.38, "weight": "bold", "c": "Input :24V"},
            {"t": "logo", "logo": "dc", "x": 34.85, "y": 11.52, "w": 3.0, "h": 2.2},
            {"t": "text", "x": 38.35, "y": 11.37, "sz": 2.38, "weight": "bold", "c": "2A"},
        ],
        "fail": [
            {"t": "frame", "x": 0.5, "y": 0.5, "w": 49, "h": 29, "thick": 0.75},
            {"t": "text", "x": 5.4, "y": 2.57, "sz": 3.6, "c": "QC FAIL"},
            {"t": "text", "x": 5.41, "y": 7.85, "sz": 3.6, "c": "POS {bench_position}"},
            {"t": "text", "x": 5.25, "y": 13.8, "sz": 1.6, "c": "{fail_reason}"},
            {"t": "text", "x": 5.37, "y": 17.37, "sz": 1.6, "c": "{date}"},
            {"t": "text", "x": 4.84, "y": 23, "sz": 1.6, "c": "{code}"},
            {"t": "text", "x": 26.9, "y": 2.29, "sz": 3.7, "c": "{qc_model}"},
        ],
    },
    "machine": {
        "pass": [
            {"t": "text", "x": 2, "y": 1.75, "sz": 5.2, "c": "QC PASS"},
            {"t": "text", "x": 2, "y": 6.75, "sz": 2.6, "c": "{qc_model}"},
            {"t": "text", "x": 2, "y": 10.5, "sz": 1.6, "c": "{date}"},
            {"t": "qr", "x": 11.25, "y": 14, "sz": 14, "c": "{qr}"},
            {"t": "text", "x": 3, "y": 35.25, "sz": 1.6, "c": "{code}"},
        ],
        "fail": [
            {"t": "frame", "x": 0.5, "y": 0.5, "w": 38, "h": 38, "thick": 0.75},
            {"t": "text", "x": 2, "y": 1.5, "sz": 3.6, "c": "QC FAIL"},
            {"t": "list", "key": "failed_tests", "x": 2, "y": 5.75, "line_h": 2.25,
             "max_lines": 8, "sz": 1.6, "more_fmt": "+{n} autres"},
            {"t": "qr", "x": 25.6, "y": 21.25, "sz": 10, "c": "{qr}"},
            {"t": "text", "x": 2, "y": 31.25, "sz": 1.6, "c": "{date}"},
            {"t": "text", "x": 2, "y": 36, "sz": 1.6, "c": "{code}"},
        ],
    },
}

TEMPLATE_URL = {
    "yms": "https://label.yumi-lab.com/qc-label-yms.json",
    "machine": "https://label.yumi-lab.com/qc-label-machine.json",
}


def render_qc_label(kind, section, data):
    """Point d'entrée : kind='yms'|'machine', section='pass'|'fail', data=dict de
    placeholders. Fetch le template live, repli sur DEFAULTS si absent/invalide/injoignable."""
    w_mm, h_mm = MEDIA[kind]
    tpl = load_template(TEMPLATE_URL[kind])
    elements = None
    if tpl and isinstance(tpl.get(section), dict) and tpl[section].get("elements"):
        elements = tpl[section]["elements"]
    if not elements:
        elements = DEFAULTS[kind][section]
    return render(elements, data, w_mm, h_mm)


if __name__ == "__main__":
    sample = {
        "code": "YMSPROV3-20260814A1B2C3D4E5", "qc_model": "YMS-PRO",
        "date": "2026-08-14 15:32", "qr": "https://qc.yumi-lab.com/report/YMSPROV3-20260814A1B2C3D4E5",
        "bench_position": "7", "fail_reason": "sensor_lost_feed", "failed_tests": [],
    }
    out = render_qc_label("yms", "pass", sample)
    path = os.environ.get("OUT", "/tmp/qc_label_test.tspl")
    with open(path, "wb") as f:
        f.write(out)
    print("écrit", path, "(%d octets)" % len(out))
