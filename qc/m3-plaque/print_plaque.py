#!/usr/bin/env python3
"""
print_plaque.py — Imprime la plaque signalétique de l'imprimante 3D sur la M3, DEPUIS LE PAD.

Lit l'identité machine via Moonraker (localhost:7125) : UID STM32 (série) + YUMI_CONFIG
(modèle=device, tension secteur=mains, puissance plateau=bedw, lot), rend la plaque
(render_plaque, Pillow), puis l'envoie à la M3 (/dev/ttyACM*, driver niim_print).
Conçu pour être lancé par la macro QC_PRINT_PLAQUE.

⚠️ Copie VENDORISÉE du dossier m3-driver/ du repo YUMI-POS-Printer (source de dev = pad
192.168.100.113). install.sh la déploie vers ~/yumi-m3-plaque/ (chemin appelé par la macro
QC_PRINT_PLAQUE de m3_plaque_macros.cfg, ci-contre) — c'est cette copie-ci qui tourne
réellement sur les pads d'usine, via YUMI_SYNC. Les deux copies se modifient ENSEMBLE.

Usage (pad) :
  python3 print_plaque.py                         # auto : lit tout via Moonraker
  python3 print_plaque.py --serial <UID> --model C235 --mains 110V --power 420   # tout forcé (sans Moonraker)
  python3 print_plaque.py --dry-run --out /tmp/plaque.png   # rend sans imprimer
Options : --moonraker URL, --port, --density 3, --qty 1, --orient none|180|mirror, --offx -1, --offy -0.2,
          --mains 220V|110V (tension gravée, sinon lue au MCU), --voltage "libellé" (texte imprimé, sinon
          déduit de la tension), --power 420 (W total, sinon bedw gravé + PSU), --no-gate (réimpression)

Codes de sortie : 0 imprimé · 3 machine pas PASS sur qc.yumi-lab.com · 4 identité électrique
incomplète (tension/puissance inconnues) ou gabarit qui ne route pas la tension de la machine.
Dans les deux cas RIEN n'est imprimé : une plaque fausse est pire qu'une plaque absente.
"""
import os, sys, re, json, math, time, argparse, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_plaque

# ── Tension secteur gravée → libellé imprimé + tension de calcul du courant ──────────────
# La tension secteur est CHOISIE au build du firmware (firmware.yumi-lab.com, catalog.json
# `mains_voltages` = 220V | 110V) et gravée dans YUMI_CONFIG (`mains=110V`). Le libellé imprimé
# est la plage nominale correspondante ; le courant nominal est calculé à la borne BASSE de la
# plage (courant le plus élevé) et arrondi au 1 A SUPÉRIEUR (sécurité). SEULE table à toucher
# pour changer un libellé ou une borne : le gabarit (label.yumi-lab.com) ne porte que {voltage}.
# Cas vécu (12/09/2026) : gabarit avec « 220–240 V~ » codé en dur + courant divisé par 220 en
# dur -> toutes les machines 110 V sortaient avec une plaque 220 V et un courant moitié trop bas.
# Consigne Nicolas (12/09/2026) : le rendu 220 V reste STRICTEMENT identique à la plaque d'avant
# (« 220–240 V~ », P/220) ; seul le 110 V change. Plage 110 V et tension de calcul (vmin) = convention
# à confirmer par Nicolas (100–120 V~ et P/100 ici ; à 120 V nominal le C235 ferait 4 A au lieu de 5).
MAINS_RATINGS = {
    '220V': {'voltage': '220–240 V~', 'vmin': 220},
    '110V': {'voltage': '100–120 V~', 'vmin': 100},
}
PSU_WATTS = 120  # alim DC fixe C-SERIES (W), ajoutée à la puissance plateau gravée (bedw)

# Contrat avec le panel QC (qc_engine.plaque_feedback lit ces deux chaînes dans la console).
REFUSED = 'QC:PLAQUE:REFUS'
PRINTED = 'Plaque imprimée'

def norm_mains(v):
    """'110', '110v', ' 110V ' -> '110V' ; '' si vide (même règle que qc.yumi-lab.com)."""
    v = (v or '').strip().upper()
    if v and v[-1].isdigit():
        v += 'V'
    return v

def mains_rating(mains):
    """{'voltage': libellé, 'vmin': V de calcul} pour une tension gravée ; None si inconnue."""
    return MAINS_RATINGS.get(norm_mains(mains))

def rated_amps(total_watts, vmin):
    """Courant nominal (A) = P / borne basse de la plage, arrondi au 1 A supérieur."""
    return int(math.ceil(float(total_watts) / float(vmin)))

def parse_yumi_config(message):
    """Ligne DEVICE du gcode_store ('// [mcu] board=X;device=C235;mains=110V;bedw=300;…')
    -> (dict clé=valeur, ligne nettoyée sans préfixe '// [mcu] ')."""
    clean = str(message).strip().lstrip('/').strip()
    clean = re.sub(r'^\[[^\]]*\]\s*', '', clean).strip()
    kv = dict(p.split('=', 1) for p in clean.split(';') if '=' in p)
    return {k.strip(): v.strip() for k, v in kv.items()}, clean

_DASHES = re.compile(r'[‐-―−-]')
_MAINS_TXT = re.compile(r'\d\s*V\s*~|\d\s*V\s*AC\b|\d+\s*-\s*\d+\s*V\b', re.I)

def _norm_txt(s):
    return re.sub(r'\s+', ' ', _DASHES.sub('-', str(s))).strip().upper()

def voltage_routing_error(template, voltage):
    """Le gabarit doit ROUTER la tension de la machine : via {voltage}, ou en portant
    exactement son libellé. Une ligne d'entrée secteur codée en dur pour une AUTRE tension
    (le bug du 12/09/2026 : gabarit 220–240 V~ sur une machine 110 V) -> message, sinon None."""
    if voltage in (None, '', '?'):
        return 'tension inconnue : rien à router sur la plaque'
    want = _norm_txt(voltage)
    for e in template.get('elements', []):
        if e.get('t') not in ('text', 'serial'):
            continue
        c = str(e.get('c', ''))
        if '{voltage}' in c:
            continue
        if _MAINS_TXT.search(_norm_txt(c)) and want not in _norm_txt(c):
            return ('le gabarit code la tension en dur (« %s ») au lieu de {voltage} -- machine %s'
                    % (c, voltage))
    return None

def _mr(url, path, method='GET', timeout=6):
    req = urllib.request.Request(url.rstrip('/') + path, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def identity_from_store(store):
    """Extrait l'identité (serial, model, mains, bedw, lot, yumi_config) d'un gcode_store."""
    ident = {}
    yc = ''
    for e in store:
        m = e.get('message', '')
        if 'MCU_UID=' in m:
            ident['serial'] = m.split('MCU_UID=', 1)[1].strip()
        if 'device=' in m.lower():
            yc = m
    if yc:
        kv, clean = parse_yumi_config(yc)
        ident['yumi_config'] = clean
        for k in ('device', 'mains', 'bedw', 'lot'):
            if kv.get(k):
                ident['model' if k == 'device' else k] = kv[k]
        if not ident.get('serial') and kv.get('uid'):
            ident['serial'] = kv['uid']  # repli (hash, non unique)
    return ident

def moonraker_identity(url, salves=2, polls=8, poll_interval=0.4, store_count=150, send_timeout=1.5):
    """Exécute DEVICE + QUERY_MCU_UID (si pas déjà fait par l'appelant) puis POLL le
    gcode_store jusqu'à trouver les deux réponses.

    29/08/2026 -- root cause réelle : quand ce script est lancé par RUN_SHELL_COMMAND (macro
    QC_PRINT_PLAQUE), Klipper est déjà en train de traiter CETTE MÊME ligne de gcode ; un
    gcode_script(DEVICE) envoyé PENDANT ce temps ne peut jamais être dépilé (la queue gcode
    ne redevient libre qu'au retour du handler RUN_SHELL_COMMAND, qui attend justement CE
    script) -> le POST timeoute TOUJOURS dans ce contexte (vérifié : 100% des invocations via
    macro). Le fix précédent (poll élargi) ne marchait QUE par coïncidence, quand un autre
    appelant (ex. le panel QC, _load_mcu_uid) avait DÉJÀ peuplé le gcode_store avant le clic.
    Vrai fix : la macro QC_PRINT_PLAQUE envoie maintenant QUERY_MCU_UID + DEVICE en lignes
    gcode ORDINAIRES avant RUN_SHELL_COMMAND (pas de deadlock, ce sont deux commandes
    distinctes traitées l'une après l'autre) -- la donnée est donc déjà dans le gcode_store
    dès le démarrage de ce script, et le poll ci-dessous la trouve dès le premier tour.
    L'envoi actif ci-dessous est gardé UNIQUEMENT pour l'usage standalone (script lancé à la
    main, hors macro, cf. docstring du module) ; send_timeout court car il est structurellement
    voué à l'échec quand appelé depuis RUN_SHELL_COMMAND -- pas la peine d'attendre 6s x4.
    """
    ident = {}
    for attempt in range(1, salves + 1):
        for cmd in ('QUERY_MCU_UID', 'DEVICE'):
            try:
                _mr(url, '/printer/gcode/script?script=' + urllib.parse.quote(cmd), method='POST',
                    timeout=send_timeout)
            except Exception as e:
                print('WARN Moonraker gcode_script(%s): %s' % (cmd, e), file=sys.stderr)
        for _ in range(polls):
            time.sleep(poll_interval)
            try:
                store = _mr(url, '/server/gcode_store?count=%d' % store_count)['result']['gcode_store']
            except Exception as e:
                print('WARN Moonraker gcode_store:', e, file=sys.stderr)
                continue
            ident = identity_from_store(store) or ident
            if all(ident.get(k) for k in ('serial', 'model', 'mains', 'bedw')):
                return ident
        print('WARN: identite incomplete apres salve %d/%d (serial=%r model=%r mains=%r bedw=%r)'
              % (attempt, salves, ident.get('serial'), ident.get('model'), ident.get('mains'),
                 ident.get('bedw')), file=sys.stderr)
    return ident

def yumi_id():
    """YUMI ID = MAC end0/eth0 (12 hex majuscules) — l'identifiant machine affiché par le
    panel QC (qc_wizard lit /sys/class/net/end0/address) et utilisé comme printer_id."""
    for iface in ('end0', 'eth0'):
        try:
            with open('/sys/class/net/%s/address' % iface) as f:
                mac = f.read().strip()
            if mac:
                return mac.replace(':', '').upper()
        except Exception:
            pass
    return ''

def qc_status(uid, base='https://qc.yumi-lab.com/api/qc/report/', timeout=7):
    """Statut QC de la machine (par UID STM32) sur le serveur central.
    -> 'PASS' | 'FAIL'/'PARTIAL' | 'ABSENT' (404) | 'UNREACHABLE' (réseau)."""
    try:
        with urllib.request.urlopen(base + uid, timeout=timeout) as r:
            d = json.load(r)
        return (d.get('latest') or {}).get('overall_result') or 'ABSENT'
    except urllib.error.HTTPError as e:
        return 'ABSENT' if e.code == 404 else 'UNREACHABLE'
    except Exception:
        return 'UNREACHABLE'

def image_to_rows(img, threshold=128):
    """PIL L -> liste de bytearray (bit=1 où pixel BLANC = contenu à imprimer)."""
    img = img.convert('L')
    W, H = img.size; bpr = (W + 7) // 8
    px = img.load()
    rows = []
    for y in range(H):
        row = bytearray(bpr)
        for x in range(W):
            if px[x, y] > threshold:
                row[x >> 3] |= 0x80 >> (x & 7)
        rows.append(row)
    return rows, W, H

def electrical_rating(mains, bedw, psu=PSU_WATTS, power=None, voltage=None):
    """Tension imprimée + puissance + courant d'une machine, ou la liste des raisons de
    REFUSER l'impression. Pure (testable) : ne touche ni Moonraker ni le gabarit.
    -> ({'voltage', 'power', 'amps', 'watts'}, [problèmes])"""
    problems = []
    rating = mains_rating(mains)
    if not rating:
        problems.append('tension secteur inconnue (mains=%r absent de YUMI_CONFIG ou hors table %s) '
                        '-- reflasher via firmware.yumi-lab.com ou passer --mains'
                        % (mains, '/'.join(MAINS_RATINGS)))
    if power and re.search(r'\d+', str(power)):                  # override manuel --power
        watts = int(re.search(r'\d+', str(power)).group())
    elif bedw and str(bedw).strip().isdigit():
        watts = int(bedw) + psu                                  # bed gravé (par machine) + PSU DC fixe
    else:
        watts = 0
        problems.append('puissance plateau inconnue (bedw= absent de YUMI_CONFIG) -- courant non '
                        'calculable, reflasher via firmware.yumi-lab.com ou passer --power')
    amps = rated_amps(watts, rating['vmin']) if (watts and rating) else 0
    out = {
        'voltage': voltage or (rating['voltage'] if rating else '?'),
        'power': ('%d W' % watts) if watts else '',
        'amps': ('%.1f' % amps) if amps else '?',
        'watts': watts,
    }
    return out, problems

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--moonraker', default='http://127.0.0.1:7125')
    ap.add_argument('--serial'); ap.add_argument('--model')
    ap.add_argument('--mains', help='tension secteur gravée (%s) ; sinon lue au MCU (YUMI_CONFIG mains=)'
                    % '|'.join(MAINS_RATINGS))
    ap.add_argument('--voltage', help='libellé imprimé ; sinon déduit de la tension (ex. « 220–240 V~ »)')
    ap.add_argument('--power', default=None, help='puissance totale (W) ; sinon plateau (bedw MCU) + PSU')
    ap.add_argument('--psu', type=int, default=PSU_WATTS, help='puissance alim DC fixe C-SERIES (W)')
    ap.add_argument('--lot'); ap.add_argument('--qr')
    ap.add_argument('--qr-base', dest='qr_base', default='https://qc.yumi-lab.com/report/',
                    help='le QR encode <qr_base><machine_uid> (page rapport QC)')
    ap.add_argument('--qr2', default='https://go.yumi-lab.com',
                    help='2e QR (partie basse) = User Guide / doc en ligne')
    ap.add_argument('--template', default='https://label.yumi-lab.com/m3-template.json',
                    help='URL/fichier du template design (défaut = celui sauvé sur label ; sinon layout par défaut)')
    ap.add_argument('--origin', default='Made in China'); ap.add_argument('--maker', default='Yumi Lab · yumi-lab.com')
    ap.add_argument('--port'); ap.add_argument('--density', type=int, default=3); ap.add_argument('--qty', type=int, default=1)
    ap.add_argument('--orient', default='none'); ap.add_argument('--offx', type=float, default=-1.0); ap.add_argument('--offy', type=float, default=-0.2)
    ap.add_argument('--dry-run', action='store_true'); ap.add_argument('--out', default='/tmp/plaque.png')
    ap.add_argument('--no-gate', dest='no_gate', action='store_true',
                    help='réimpression forcée : n\'exige pas que la machine soit PASSED')
    a = ap.parse_args()

    # MACHINE UID = UID STM32 (24 hex) lu via QUERY_MCU_UID (Moonraker, nécessite [mcu_uid]).
    # modèle/tension/puissance/lot = YUMI_CONFIG (DEVICE). Série = MACHINE UID. QR = page rapport QC.
    # Tout forcé en ligne de commande -> pas de Moonraker (test hors pad, réimpression manuelle).
    forced = all((a.serial, a.model, a.mains, a.power))
    ident = {} if forced else moonraker_identity(a.moonraker)
    uid = (a.serial or ident.get('serial') or '').strip().upper()
    if not uid:
        uid = yumi_id()  # REPLI : MAC end0 (≠ UID STM32) si [mcu_uid] non chargé
        print("WARN: UID STM32 indisponible ([mcu_uid] non chargé ?) -> repli MAC", file=sys.stderr)

    # GUARD conformité : n'imprimer l'étiquette QUE si la machine est PASSED sur qc.yumi-lab.com
    # (contournable pour réimpression avec --no-gate ; ignorée en --dry-run).
    if not (a.dry_run or a.no_gate):
        st = qc_status(uid)
        if st != 'PASS':
            why = {'ABSENT': 'aucun QC PASSED sur qc.yumi-lab.com pour cette machine',
                   'UNREACHABLE': 'qc.yumi-lab.com injoignable (reseau ?)'}.get(st, 'QC=' + st)
            print('%s uid=%s -- %s' % (REFUSED, uid, why))
            sys.exit(3)
        print('QC:PLAQUE:GATE_OK PASSED uid=%s' % uid)

    # CALCUL AUTO PAR IMPRIMANTE (zéro saisie, AUCUNE table par modèle) : tension secteur (mains)
    # et puissance du BED (bedw) sont LUES SUR CHAQUE machine via DEVICE (YUMI_CONFIG gravé au
    # MCU). Puissance MAX = bedw + PSU DC fixe (120 W, C-SERIES). Courant = P / borne basse de la
    # plage de la tension gravée (MAINS_RATINGS), arrondi au 1 A SUPÉRIEUR.
    #   C235 220 V : 300 + 120 = 420 W -> 420 / 220 = 1,9 -> 2 A.   C235 110 V : 420 / 100 -> 5 A.
    model = a.model or ident.get('model') or '?'
    elec, problems = electrical_rating(a.mains or ident.get('mains'), ident.get('bedw'),
                                       psu=a.psu, power=a.power, voltage=a.voltage)
    data = {
        'serial': uid,
        'model': model,
        'voltage': elec['voltage'], 'power': elec['power'], 'amps': elec['amps'],
        'lot': a.lot or ident.get('lot') or '',
        'origin': a.origin, 'maker': a.maker,
        'qr': a.qr or (a.qr_base + uid),
        'qr2': a.qr2,
    }
    print('Identité plaque :', json.dumps(data, ensure_ascii=False))
    tpl = render_plaque.load_template(a.template)
    print('Template :', a.template if tpl else '(défaut embarqué — fetch échoué/absent)')
    # GUARD routage : le gabarit doit porter {voltage} (ou exactement la tension de CETTE machine).
    err = voltage_routing_error(tpl or render_plaque.DEFAULT_TEMPLATE, data['voltage'])
    if err:
        problems.append(err)
    if problems:
        for p in problems:
            print('%s uid=%s -- %s' % (REFUSED, uid, p))
        if not a.dry_run:
            sys.exit(4)
        print('DRY-RUN : rendu quand même pour contrôle (une impression réelle serait refusée)',
              file=sys.stderr)
    img = render_plaque.render(data, tpl)
    # décalage de calage (offset mm) : translate le contenu sur fond noir
    if a.offx or a.offy:
        from PIL import Image
        dx = round(a.offx / 25.4 * render_plaque.DPI); dy = round(a.offy / 25.4 * render_plaque.DPI)
        bg = Image.new('L', img.size, 0); bg.paste(img, (dx, dy)); img = bg
    if a.orient == '180': img = img.rotate(180)
    elif a.orient == 'mirror': from PIL import ImageOps; img = ImageOps.mirror(img)

    if a.dry_run:
        img.save(a.out); print('DRY-RUN : écrit', a.out); return

    import niim_print
    rows, W, Hh = image_to_rows(img)
    m = niim_print.M3(port=a.port)
    try:
        print('Impression sur', m.path, '…')
        m.print_image(rows, W, Hh, density=a.density, quantity=max(1, a.qty), count_mode='auto')
        print('✅ %s.' % PRINTED)
    finally:
        m.close()

if __name__ == '__main__':
    main()
