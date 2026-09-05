#!/usr/bin/env python3
"""
yumi-detect — composition materielle d'un pad : cartes MCU + webcams UVC.

Sort un seul JSON normalise {boards, cameras} : c'est le socle commun du generateur
(printer.cfg depuis boards, config camera/udev depuis cameras), orchestre par yumi-sync.

- Cartes  : delegue a yumi-scan.py --json (scan serie direct). Klippy doit etre ARRETE
            (il occupe les ports serie). Tourne avec n'importe quel python (yumi-scan
            se relance tout seul avec klippy-env).
- Webcams : detection v4l2 directe, A CHAUD (sans toucher a Klipper). Cible les vrais
            peripheriques de CAPTURE USB UVC (via /dev/v4l/by-id/*-index0 + capacite
            "Video Capture") -> ignore le VPU (video0/cedrus) et les noeuds metadonnees.

Usage :
    yumi-detect.py [--no-boards] [PORT ...]
"""
import os
import sys
import glob
import re
import json
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
# Fichier d'etat runtime du pad, HORS repo (comme monitoring_state.json /
# .yumi_install_state.json de yumi_sync) -> n'introduit jamais de "git dirty".
DEFAULT_OUT = os.path.expanduser('~/.yumi_composition.json')


def scan_boards(ports=None):
    """Lit les cartes via yumi-scan.py --json et normalise le descripteur en champs."""
    cmd = [sys.executable, os.path.join(HERE, 'yumi-scan.py'), '--json']
    if ports:
        cmd += ports
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        raw = json.loads(p.stdout)
    except Exception as e:
        return [{'error': 'yumi-scan KO: %r' % e}]
    boards = []
    for r in raw:
        cfg = r.get('YUMI_CONFIG')
        if not cfg:
            continue
        d = dict(kv.split('=', 1) for kv in cfg.split(';') if '=' in kv)
        # axes drivers (x/y/z/e0..) et moteurs (mx/my/mz) deja dans d
        drivers = {k: v for k, v in d.items()
                   if k in ('x', 'y', 'z') or re.fullmatch(r'e\d+', k)}
        motors = {k[1:]: v for k, v in d.items() if re.fullmatch(r'm[xyz]', k)}
        # ensemble piezo Z-tap grave: piezo=element ; pzb=carte ; pzbv=version carte
        piezo = None
        if d.get('piezo') or d.get('pzb'):
            piezo = {'element': d.get('piezo'),
                     'board': d.get('pzb'),
                     'board_version': d.get('pzbv')}
        boards.append({
            'port': r.get('port'),
            'uid': d.get('uid'),
            'board': d.get('board'),
            'cpu': d.get('cpu'),
            'device': d.get('device'),
            'conn': d.get('conn'),
            'lot': d.get('lot'),
            'drivers': drivers,
            'motors': motors,
            'piezo': piezo,
            'comment': r.get('YUMI_COMMENT') or None,
            'klipper': r.get('version'),
            'descriptor': cfg,
        })
    return boards


def _v4l2(dev, *args):
    try:
        return subprocess.run(['v4l2-ctl', '-d', dev, *args],
                              capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return ''


def scan_cameras():
    """Vraies webcams = peripheriques de CAPTURE USB UVC (by-id *-index0 + 'Video Capture')."""
    cams, seen = [], set()
    for byid in sorted(glob.glob('/dev/v4l/by-id/*-index0')):
        real = os.path.realpath(byid)
        if real in seen:
            continue
        info = _v4l2(real, '--all')
        if 'Video Capture' not in info:      # exclut VPU / noeuds non-capture
            continue
        seen.add(real)
        m = re.search(r'Card type\s*:\s*(.+)', info)
        name = m.group(1).strip() if m else os.path.basename(byid)
        res = sorted(set(re.findall(r'Size: Discrete (\d+x\d+)',
                                    _v4l2(real, '--list-formats-ext'))),
                     key=lambda s: int(s.split('x')[0]) * int(s.split('x')[1]))
        # symlink udev yumi eventuel pointant sur ce device
        webcam_link = next((l for l in glob.glob('/dev/webcam*')
                            if os.path.realpath(l) == real), None)
        cams.append({
            'name': name,
            'by_id': os.path.basename(byid),
            'capture': real,
            'webcam_symlink': webcam_link,
            'resolutions': res,
        })
    return cams


def main():
    ap = argparse.ArgumentParser(description="Composition materielle du pad (boards + cameras)")
    ap.add_argument('--no-boards', action='store_true',
                    help="cameras seules (ne touche pas a klippy)")
    ap.add_argument('ports', nargs='*', help="ports cartes (defaut: auto-detection)")
    ap.add_argument('--out', default=DEFAULT_OUT,
                    help="fichier JSON de composition (defaut: ~/.yumi_composition.json, hors repo ; '-' = stdout)")
    a = ap.parse_args()
    out = {
        'boards': [] if a.no_boards else scan_boards(a.ports or None),
        'cameras': scan_cameras(),
    }
    js = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out == '-':
        print(js)
    else:
        # ecriture atomique : le generateur lit toujours un JSON complet
        tmp = a.out + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(js + '\n')
        os.replace(tmp, a.out)
        print("composition -> %s  (%d cartes, %d cameras)"
              % (a.out, len(out['boards']), len(out['cameras'])))


if __name__ == '__main__':
    main()
