#!/usr/bin/env python3
"""
yumi-scan — lit l'identite YUMI (YUMI_CONFIG / YUMI_COMMENT) de chaque carte MCU
connectee, EN DIRECT sur le port serie, SANS que Klipper soit demarre.

Pourquoi : la macro console `DEVICE` n'existe que si klippy tourne ET est connecte.
Pour le QC usine et le SAV (carte vierge de config, printer.cfg casse, klippy en erreur),
on doit pouvoir interroger la carte quand meme. Ce script parle directement au MCU avec
le protocole Klipper (recupere le data dictionary, qui contient les constantes gravees).

Tourne SUR LE PAD (host NanoPi/Pi), pas sur le VPS. Reutilise les libs de ~/klipper/klippy.

IMPORTANT : Klipper doit etre ARRETE (il occupe les ports serie) :
    sudo systemctl stop klipper
    ./yumi-scan.py
    sudo systemctl start klipper

Usage :
    yumi-scan.py [PORT ...] [-b 250000] [--json]
Sans PORT : auto-detection depuis printer.cfg ([mcu] serial:) + /dev/serial/by-id/*.
"""
import os
import sys
import glob
import re
import json
import argparse
import subprocess


def ensure_klippy_python():
    """Les libs Klipper (reactor->greenlet) ne sont que dans le venv klippy-env.
    Si on tourne avec un python qui n'a pas greenlet, on se relance avec celui du venv."""
    try:
        import greenlet  # noqa: F401
        return
    except ImportError:
        pass
    # NB: comparer les chemins BRUTS (pas realpath) : le python du venv est souvent un
    # symlink vers le python systeme, mais ses site-packages (greenlet) sont differents.
    for venv in (os.path.expanduser('~/klippy-env/bin/python'),
                 '/home/pi/klippy-env/bin/python'):
        if os.path.isfile(venv) and os.path.abspath(venv) != os.path.abspath(sys.executable):
            os.execv(venv, [venv] + sys.argv)
    sys.exit("ERREUR: 'greenlet' absent et klippy-env introuvable (lancer avec ~/klippy-env/bin/python).")


def find_klippy():
    for d in (os.path.expanduser('~/klipper/klippy'),
              '/home/pi/klipper/klippy', '/opt/klipper/klippy'):
        if os.path.isfile(os.path.join(d, 'serialhdl.py')):
            return d
    sys.exit("ERREUR: Klipper introuvable (klippy/serialhdl.py).")


def detect_ports():
    ports = []
    # 1. serials declares dans la config Klipper
    for cfg in glob.glob(os.path.expanduser('~/printer_data/config/*.cfg')):
        try:
            txt = open(cfg, encoding='utf-8', errors='ignore').read()
        except OSError:
            continue
        for m in re.finditer(r'(?m)^\s*serial\s*:\s*(\S+)', txt):
            ports.append(m.group(1))
    # 2. tous les peripheriques serie USB (cartes non encore dans la config incluses)
    ports += sorted(glob.glob('/dev/serial/by-id/*'))
    ports += sorted(glob.glob('/dev/ttyACM*'))
    ports += sorted(glob.glob('/dev/ttyUSB*'))
    # uniques par device REEL (realpath) -> evite le doublon /dev/serial/by-id <-> /dev/ttyACMx
    seen, out = set(), []
    for p in ports:
        if 'klipper_host_mcu' in p or p.startswith('/tmp/'):
            continue
        rp = os.path.realpath(p)
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def read_port(port, baud, timeout=8.):
    """Lit un port (UN seul) — execute dans son propre process pour un etat reactor propre."""
    sys.path.insert(0, find_klippy())
    import reactor as kreactor
    import serialhdl
    r = kreactor.Reactor()
    ser = serialhdl.SerialReader(r)
    out = {'port': port}

    def cb(eventtime):
        try:
            ser.connect_uart(port, baud)
            mp = ser.get_msgparser()
            c = mp.get_constants()
            out['version'] = mp.get_version_info()[0]
            out['YUMI_CONFIG'] = c.get('YUMI_CONFIG')
            out['YUMI_COMMENT'] = c.get('YUMI_COMMENT')
        except Exception as e:
            out['error'] = repr(e)
        r.end()
        return r.NEVER

    def watchdog(eventtime):
        if 'YUMI_CONFIG' not in out and 'error' not in out:
            out['error'] = 'timeout (pas de reponse Klipper sur ce port)'
        r.end()
        return r.NEVER

    r.register_callback(cb)
    r.register_timer(watchdog, r.monotonic() + timeout)
    try:
        r.run()
    finally:
        try:
            ser.disconnect()
        except Exception:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description="Scan des cartes MCU Yumi (hors klippy)")
    ap.add_argument('ports', nargs='*', help="ports serie (defaut: auto-detection)")
    ap.add_argument('-b', '--baud', type=int, default=250000)
    ap.add_argument('--json', action='store_true', help="sortie JSON")
    ap.add_argument('--single', help=argparse.SUPPRESS)  # mode interne: 1 port = 1 process
    args = ap.parse_args()

    ensure_klippy_python()   # se relance avec le python de klippy-env si besoin

    # Mode interne : on lit exactement un port et on rend du JSON sur stdout.
    if args.single:
        print(json.dumps(read_port(args.single, args.baud), ensure_ascii=False))
        return

    ports = args.ports or detect_ports()
    if not ports:
        sys.exit("Aucun port a scanner (passe-les en argument ou demarre depuis le pad).")

    # Un sous-process par port -> etat reactor/serie propre a chaque fois.
    results = []
    for p in ports:
        try:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), '--single', p, '-b', str(args.baud)],
                capture_output=True, text=True, timeout=20)
            results.append(json.loads(proc.stdout.strip().splitlines()[-1]))
        except Exception as e:
            results.append({'port': p, 'error': 'scan KO: %r %s' % (e, proc.stderr[-200:] if 'proc' in dir() else '')})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    found = 0
    for res in results:
        print("\n=== %s ===" % res['port'])
        if res.get('YUMI_CONFIG'):
            found += 1
            print("  YUMI_CONFIG : %s" % res['YUMI_CONFIG'])
            if res.get('YUMI_COMMENT'):
                print("  YUMI_COMMENT: %s" % res['YUMI_COMMENT'])
            print("  Klipper     : %s" % res.get('version', '?'))
        elif res.get('error'):
            print("  (non lu) %s" % res['error'])
        else:
            print("  (carte sans constante YUMI — firmware non-Yumi ?)")
    print("\n%d carte(s) Yumi identifiee(s) sur %d port(s)." % (found, len(results)))


if __name__ == '__main__':
    main()
