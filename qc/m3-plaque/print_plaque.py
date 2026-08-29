#!/usr/bin/env python3
"""
print_plaque.py — Imprime la plaque signalétique de l'imprimante 3D sur la M3, DEPUIS LE PAD.

Lit l'identité machine via Moonraker (localhost:7125) : UID STM32 (série) + YUMI_CONFIG
(modèle=device, tension=mains, lot), rend la plaque (render_plaque, Pillow), puis l'envoie
à la M3 (/dev/ttyACM*, driver niim_print). Conçu pour être lancé par la macro QC_PRINT_PLAQUE.

⚠️ Copie VENDORISÉE du dossier m3-driver/ du repo YUMI-POS-Printer (source de dev = pad
192.168.100.113). install.sh la déploie vers ~/yumi-m3-plaque/ (chemin appelé par la macro
QC_PRINT_PLAQUE de m3_plaque_macros.cfg, ci-contre) — c'est cette copie-ci qui tourne
réellement sur les pads d'usine, via YUMI_SYNC. Les deux copies se modifient ENSEMBLE.

Usage (pad) :
  python3 print_plaque.py                         # auto : lit tout via Moonraker
  python3 print_plaque.py --serial <UID> --model C235 --voltage "220V ~ 50/60Hz"
  python3 print_plaque.py --dry-run --out /tmp/plaque.png   # rend sans imprimer
Options : --moonraker URL, --port, --density 3, --qty 1, --orient none|180|mirror, --offx -1, --offy -0.2, --power "350 W"
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_plaque

def _mr(url, path, method='GET', timeout=6):
    req = urllib.request.Request(url.rstrip('/') + path, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

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
            yc = ''
            for e in store:
                m = e.get('message', '')
                if 'MCU_UID=' in m:
                    ident['serial'] = m.split('MCU_UID=', 1)[1].strip()
                if 'device=' in m.lower():
                    yc = m
            if yc:
                import re
                clean = re.sub(r'^\s*\[[^\]]*\]\s*', '', yc).strip()  # retire "// [mcu] " etc.
                clean = clean.lstrip('/ ').strip()
                kv = dict(p.split('=', 1) for p in clean.split(';') if '=' in p)
                ident['yumi_config'] = clean
                if kv.get('device'): ident['model'] = kv['device']
                if kv.get('mains'): ident['voltage'] = kv['mains'] + ' ~ 50/60Hz'
                if kv.get('lot'): ident['lot'] = kv['lot']
                if kv.get('bedw'): ident['bedw'] = kv['bedw']  # puissance plateau (W)
                if not ident.get('serial') and kv.get('uid'): ident['serial'] = kv['uid']  # repli (hash, non unique)
            if ident.get('serial') and ident.get('model') and ident.get('bedw'):
                return ident
        print('WARN: identite incomplete apres salve %d/%d (serial=%r model=%r bedw=%r)'
              % (attempt, salves, ident.get('serial'), ident.get('model'), ident.get('bedw')), file=sys.stderr)
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--moonraker', default='http://127.0.0.1:7125')
    ap.add_argument('--serial'); ap.add_argument('--model'); ap.add_argument('--voltage')
    ap.add_argument('--power', default=None, help='sinon calculé = plateau(bedw MCU) + PSU')
    ap.add_argument('--psu', type=int, default=120, help='puissance alim DC fixe C-SERIES (W)')
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
    # modèle/tension/lot = YUMI_CONFIG (DEVICE). Série = MACHINE UID. QR = page rapport QC.
    ident = moonraker_identity(a.moonraker)
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
            print('QC:PLAQUE:REFUS uid=%s -- %s' % (uid, why))
            sys.exit(3)
        print('QC:PLAQUE:GATE_OK PASSED uid=%s' % uid)

    # CALCUL AUTO PAR IMPRIMANTE (zéro saisie, AUCUNE table codée en dur) : la puissance du BED
    # est LUE SUR CHAQUE machine via Moonraker DEVICE (champ `bedw` du YUMI_CONFIG gravé au MCU).
    # Puissance MAX = bedw + PSU DC fixe (120 W, C-SERIES). Courant nominal = P / 220 V (tension
    # mini de la plage), arrondi au 1 A SUPÉRIEUR (sécurité).
    #   -> le 350 W du C235 vient du FIRMWARE (bedw=350), pas du script : 350 + 120 = 470 W ->
    #      470 / 220 = 2,14 A -> ceil -> 3,0 A. (Une machine à bedw=300 donnerait 2,0 A.)
    import math, re
    model = a.model or ident.get('model') or '?'
    bedw = ident.get('bedw')
    if a.power and re.search(r'\d+', a.power):            # override manuel éventuel --power
        pw = int(re.search(r'\d+', a.power).group())
    elif bedw and str(bedw).strip().isdigit():
        pw = int(bedw) + a.psu                            # bed (MCU, par machine) + PSU DC fixe
    else:
        pw = 0
        print('WARN: puissance bed (bedw) absente de DEVICE -> courant non calculé', file=sys.stderr)
    amps = math.ceil(pw / 220.0) if pw else 0
    data = {
        'serial': uid,
        'model': model,
        'voltage': a.voltage or ident.get('voltage') or '220–240 V~',
        'power': ('%d W' % pw) if pw else '', 'amps': ('%.1f' % amps) if amps else '?',
        'lot': a.lot or ident.get('lot') or '',
        'origin': a.origin, 'maker': a.maker,
        'qr': a.qr or (a.qr_base + uid),
        'qr2': a.qr2,
    }
    print('Identité plaque :', json.dumps(data, ensure_ascii=False))
    tpl = render_plaque.load_template(a.template)
    print('Template :', a.template if tpl else '(défaut embarqué — fetch échoué/absent)')
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
        print('✅ Plaque imprimée.')
    finally:
        m.close()

if __name__ == '__main__':
    main()
