#!/usr/bin/env python3
"""
render_qc_tspl.py — Étiquettes QC PASS/FAIL, TSPL brut pour POS80L, TEMPLATE-DRIVEN.

Même principe que m3-driver/render_plaque.py (repo YUMI-POS-Printer) pour la plaque M3 :
le layout vient d'un TEMPLATE (liste d'éléments en mm + placeholders {code},{qc_model}…),
éditable dans Label Expert (boutons « QC label — … » du panneau QC Factory) et sauvé sur
label.yumi-lab.com. Repli silencieux sur un gabarit par défaut codé en dur si le fetch
échoue (réseau down, 404, JSON invalide) — jamais de blocage du banc QC.

Différence avec le M3 : la sortie n'est PAS une image (Pillow) mais du TSPL TEXTE pur
(TEXT/QRCODE/BOX), comme le faisait le TSPL en dur historique de qc_yms.py::build_label_tspl.
Un seul élément sort en bitmap : `logo` (un logo n'a pas d'équivalent TEXT en TSPL — patché
en BITMAP sur sa seule zone, le reste de l'étiquette reste du texte).

2 fichiers, un par taille physique de média (contrainte dure : SIZE/GAP en dépend) :
  qc-label-yms.json (50×30mm, boîtiers YMS), qc-label-machine.json (39×39mm, machines).
Chacun porte 2 sections `pass`/`fail`.

Autonome : Pillow (pour les logos bitmap uniquement) + assets/ (PNG, copie des logos M3).
"""
import base64, io, json, os

DPMM = 8  # 203 dpi ~ 8 dots/mm (POS80L / HS-584)
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")

MEDIA = {"yms": (50, 30), "machine": (39, 39)}


def DOT(mm):
    return max(0, round(float(mm) * DPMM))


# ── Snapping police : les fonts bitmap TSPL sont des tailles FIXES ("1".."4"), pas un
# corps arbitraire comme une TrueType. Table APPROXIMATIVE (hauteur de caractère en mm) —
# à recaler après une impression réelle mesurée, comme tout gate visuel de ce projet.
FONT_MM = {"1": 1.6, "2": 2.6, "3": 3.6, "4": 5.2}


def snap_font(sz_mm):
    """Police TSPL la plus proche + multiplicateur xmul/ymul (1-3) pour affiner."""
    best_id, best_mm = min(FONT_MM.items(), key=lambda kv: abs(kv[1] - sz_mm))
    mult = max(1, min(3, round(sz_mm / best_mm))) if best_mm else 1
    return best_id, mult


def snap_qr_cell(sz_mm):
    """Cellule QRCODE (dots/module, 1-10) — approximation linéaire, la taille finale dépend
    aussi de la longueur des données (nombre de modules), non prédictible sans encoder."""
    return max(2, min(6, round(sz_mm / 4)))


def _subst(s, data):
    for k, v in data.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def _tspl_text(s):
    """TSPL est envoyé en ASCII pur : un caractère hors ASCII (ex. '°') deviendrait
    illisible. Les données métier (ex. fail_reason) sont déjà ASCII en amont ; ce filtre
    est une garde, pas la responsabilité principale du nettoyage."""
    return s.encode("ascii", "replace").decode("ascii").replace('"', "'")


def _logo_bitmap(x_mm, y_mm, w_mm, h_mm, logo_name=None, src=None, threshold=160):
    """Rasterise un logo (asset PNG connu ou data URL) en BITMAP TSPL 1-bit, packé comme
    pos80l-bridge.py (bit=1 -> blanc, bit=0 -> noir imprimé). Retourne None si l'image est
    introuvable/invalide (silencieux — un logo manquant ne doit jamais bloquer l'étiquette)."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        if logo_name:
            path = os.path.join(ASSETS, logo_name + ".png")
            src_img = Image.open(path).convert("RGBA")
        elif src and src.startswith("data:"):
            b64 = src.split(",", 1)[-1]
            src_img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
        else:
            return None
    except Exception:
        return None
    W, H = DOT(w_mm), DOT(h_mm)
    if W <= 0 or H <= 0:
        return None
    # C'est la SILHOUETTE (canal alpha) qui fait l'encre, jamais la couleur de l'asset.
    # L'éditeur peint déjà tout logo en noir plein (`filter: brightness(0)` sur l'img du
    # canevas) : le pad doit imprimer la même chose, sinon l'aperçu ment. Composer sur blanc
    # et lire la LUMINANCE ne marchait que par accident — les PNG viennent de la plaque M3,
    # où ils sont dessinés en BLANC sur fond transparent (ruban blanc, plaque noire). Sur
    # l'étiquette QC (papier blanc, encre noire) ils redevenaient blancs sur blanc : les six
    # logos de conformité ne sortaient tout simplement pas du papier.
    img = src_img.getchannel("A").resize((W, H))
    px = img.load()
    opacite_min = max(1, 255 - threshold)  # en dessous = transparent, donc pas d'impression
    wbytes = (W + 7) // 8
    # Trame initialisée à BLANC (bits à 1), l'encre vient ENSUITE éteindre des bits. Partir
    # de zéro laissait noires les colonnes de bourrage au-delà de W (jusqu'à 7 dots, la
    # largeur n'étant presque jamais un multiple de 8) : chaque logo traînait une barre
    # noire sur son flanc droit.
    data = bytearray(b"\xff" * (wbytes * H))
    for y in range(H):
        for xx in range(W):
            if px[xx, y] >= opacite_min:  # opaque => encre, bit à 0
                data[y * wbytes + (xx >> 3)] &= ~(0x80 >> (xx & 7)) & 0xFF
    head = ("BITMAP %d,%d,%d,%d,0," % (DOT(x_mm), DOT(y_mm), wbytes, H)).encode("ascii")
    return head + bytes(data) + b"\r\n"


def render(section_elements, data, w_mm, h_mm):
    """Construit le TSPL complet (bytes) pour UNE section (pass OU fail) déjà choisie par
    l'appelant. `data` porte les placeholders (code, qc_model, date, qr, bench_position,
    fail_reason, failed_tests…)."""
    head = [
        "SIZE %d mm,%d mm" % (w_mm, h_mm),
        "GAP 2 mm,0 mm",
        "DIRECTION 1",
        "SHIFT 8",
        "SET PEEL ON",
        "CLS",
    ]
    out = ("\r\n".join(head) + "\r\n").encode("ascii")

    for e in section_elements:
        t = e.get("t")
        if t == "frame":
            # Couvre tout rectangle QC : bordure (thick), remplissage (fill), ou les deux.
            # TSPL n'a pas de primitive "rectangle rempli" séparée — BOX avec une épaisseur
            # couvrant la moitié du plus petit côté remplit la boîte entière.
            x1, y1 = DOT(e["x"]), DOT(e["y"])
            x2, y2 = DOT(e["x"] + e["w"]), DOT(e["y"] + e["h"])
            if e.get("thick"):
                thick = max(1, DOT(e["thick"]))
            elif e.get("fill"):
                thick = max(1, min(x2 - x1, y2 - y1) // 2)
            else:
                thick = 0
            if thick:
                out += ('BOX %d,%d,%d,%d,%d\r\n' % (x1, y1, x2, y2, thick)).encode("ascii")
        elif t == "line":
            x1, y1 = DOT(e["x"]), DOT(e["y"])
            x2 = DOT(e["x"] + e["w"])
            thick = max(1, DOT(e.get("thick", 0.3)))
            out += ('BOX %d,%d,%d,%d,%d\r\n' % (x1, y1, x2, y1 + thick, thick)).encode("ascii")
        elif t == "qr":
            content = _subst(e.get("c", "{qr}"), data)
            cell = snap_qr_cell(e.get("sz", 14))
            out += ('QRCODE %d,%d,M,%d,A,0,"%s"\r\n' %
                    (DOT(e["x"]), DOT(e["y"]), cell, _tspl_text(content))).encode("ascii")
        elif t == "text":
            s = _tspl_text(_subst(e.get("c", ""), data))
            font, mult = snap_font(e.get("sz", 2.6))
            x = DOT(e["x"])
            if e.get("align") == "center":
                x = max(0, x - DOT(len(s) * FONT_MM.get(font, 2.6) * mult * 0.55 / 2))
            out += ('TEXT %d,%d,"%s",0,%d,%d,"%s"\r\n' %
                    (x, DOT(e["y"]), font, mult, mult, s)).encode("ascii")
        elif t == "list":
            items = data.get(e.get("key", "failed_tests")) or []
            max_lines = e.get("max_lines", 8)
            if len(items) <= max_lines:
                shown = items
            else:
                shown = list(items[:max_lines - 1]) + [
                    e.get("more_fmt", "+{n} autres").replace("{n}", str(len(items) - (max_lines - 1)))
                ]
            font, mult = snap_font(e.get("sz", 1.6))
            y = e["y"]
            for line in shown:
                out += ('TEXT %d,%d,"%s",0,%d,%d,"%s"\r\n' %
                        (DOT(e["x"]), DOT(y), font, mult, mult, _tspl_text(str(line)))).encode("ascii")
                y += e.get("line_h", 2.25)
        elif t == "logo":
            # L'éditeur Label Expert sauve les logos en {"src": "ce.svg"} (nom de
            # fichier nu, pas une data: URL) — la convention M3 est {"logo": "ce"}.
            # On dérive le nom d'asset local depuis "src" quand "logo" est absent,
            # sinon les 6 logos de tout gabarit sauvé depuis le site restent muets.
            name = e.get("logo")
            src = e.get("src")
            if not name and src and not str(src).startswith("data:"):
                name = os.path.splitext(os.path.basename(str(src)))[0]
            bmp = _logo_bitmap(e["x"], e["y"], e["w"], e["h"], name, src)
            if bmp:
                out += bmp

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


# ── Gabarits par défaut : conversion mm exacte du TSPL en dur historique de
# qc_yms.py::build_label_tspl (dots/8 = mm). IDENTIQUES aux DEFAULTS de qc-plaque.js —
# les deux doivent être modifiés ensemble si jamais le défaut change. ──
DEFAULTS = {
    "yms": {
        "pass": [
            {"t": "qr", "x": 2, "y": 3.75, "sz": 15, "c": "{qr}"},
            {"t": "text", "x": 22.5, "y": 1.75, "sz": 3.6, "c": "QC PASS"},
            {"t": "text", "x": 22.5, "y": 6.25, "sz": 2.6, "c": "{qc_model}"},
            {"t": "text", "x": 22.5, "y": 10.25, "sz": 1.6, "c": "{date}"},
            {"t": "text", "x": 2, "y": 25.75, "sz": 1.6, "c": "{code}"},
        ],
        "fail": [
            {"t": "frame", "x": 0.5, "y": 0.5, "w": 49, "h": 29, "thick": 0.75},
            {"t": "qr", "x": 2.5, "y": 4.25, "sz": 12, "c": "{qr}"},
            {"t": "text", "x": 23, "y": 1.5, "sz": 3.6, "c": "QC FAIL"},
            {"t": "text", "x": 23, "y": 6, "sz": 3.6, "c": "POS {bench_position}"},
            {"t": "text", "x": 23, "y": 10.25, "sz": 1.6, "c": "{fail_reason}"},
            {"t": "text", "x": 23, "y": 13.25, "sz": 1.6, "c": "{date}"},
            {"t": "text", "x": 2.5, "y": 26.5, "sz": 1.6, "c": "{code}"},
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
