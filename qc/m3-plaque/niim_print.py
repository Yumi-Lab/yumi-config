#!/usr/bin/env python3
"""
niim_print.py — Driver d'impression NIIMBOT M3 (série CDC), portable & réutilisable.

Cible : NIIMBOT M3 (transfert 300 dpi, largeur 20–78 mm). Port série CDC :
  - macOS  : /dev/cu.usbmodemM3_*
  - Linux/SmartPad : /dev/ttyACM*   (⇐ futur usage : appelé par une macro Klipper)

Framing VALIDÉ sur le device : 55 55 | cmd | len(1o) | data… | crc(XOR cmd..data) | AA AA, 115200 bauds.
Média = étiquette NOIRE + ruban BLANC (transfert) → bit=1 = tête déclenchée = trait BLANC.

Reproduit EXACTEMENT la séquence de niimbot.js (web Web Serial) : si l'impression est
correcte ici, la version navigateur l'est aussi (mêmes octets).

Entrée bitmap : fichier "W,H,bytesPerRow,<base64 des lignes empilées>" (export du renderer web),
ou --info pour une sonde non destructive.

Usage :
  python3 niim_print.py --info
  python3 niim_print.py plate_bits.txt [--port /dev/cu.usbmodemM3_*] [--density 3] [--qty 1] [--orient none|180|mirror] [--count auto|zero]
"""
import os, sys, glob, time, select, termios, tty, base64, argparse

# ---------------------------------------------------------------- protocole ---
CMD = dict(HEARTBEAT=0xDC, GET_INFO=0x40, SET_DENSITY=0x21, SET_LABEL_TYPE=0x23,
           START_PRINT=0x01, START_PAGE=0x03, SET_PAGE_SIZE=0x13, SET_QUANTITY=0x15,
           IMAGE_ROW=0x85, END_PAGE=0xE3, END_PRINT=0xF3, PRINT_STATUS=0xA3)

def encode(cmd, data=b''):
    data = bytes(data)
    body = bytes([cmd, len(data)]) + data
    crc = 0
    for b in body:
        crc ^= b
    return bytes([0x55, 0x55]) + body + bytes([crc & 0xff, 0xAA, 0xAA])

def extract_frames(buf):
    """Renvoie (frames, reste). frame = (cmd, data)."""
    frames, i = [], 0
    while i + 7 <= len(buf):
        if buf[i] != 0x55 or buf[i+1] != 0x55:
            i += 1; continue
        cmd, ln = buf[i+2], buf[i+3]
        end = i + 4 + ln + 1 + 2
        if end > len(buf):
            break
        if buf[end-2] != 0xAA or buf[end-1] != 0xAA:
            i += 1; continue
        frames.append((cmd, buf[i+4:i+4+ln]))
        i = end
    return frames, buf[i:]

# ------------------------------------------------------------------ device ----
class M3:
    def __init__(self, port=None, log=print):
        self.log = log
        self.path = port or self._autodetect()
        if not self.path:
            raise RuntimeError("Port M3 introuvable (/dev/cu.usbmodemM3_* ou /dev/ttyACM*). Branchée/allumée ?")
        self.fd = self._open(self.path)
        self.rx = b''

    @staticmethod
    def _autodetect():
        for pat in ('/dev/cu.usbmodemM3*', '/dev/tty.usbmodemM3*', '/dev/ttyACM*'):
            g = sorted(glob.glob(pat))
            if g:
                return g[0]
        return None

    def _open(self, path):
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(fd)
        a = termios.tcgetattr(fd)
        a[4] = termios.B115200; a[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, a)
        termios.tcflush(fd, termios.TCIOFLUSH)
        return fd

    def close(self):
        try: os.close(self.fd)
        except Exception: pass

    # écriture intégrale (flow-control naturel : bloque via select si buffer plein)
    def write_all(self, data):
        mv = memoryview(data); off = 0
        while off < len(mv):
            select.select([], [self.fd], [], 2.0)
            try:
                off += os.write(self.fd, mv[off:])
            except BlockingIOError:
                time.sleep(0.002)

    def read_frames(self, secs):
        end = time.time() + secs
        out = []
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], max(0, end - time.time()))
            if not r:
                break
            try:
                chunk = os.read(self.fd, 8192)
            except BlockingIOError:
                continue
            if chunk:
                self.rx += chunk
                fr, self.rx = extract_frames(self.rx)
                out.extend(fr)
        return out

    def send(self, cmd, data=b'', expect=None, timeout=1.5):
        self.write_all(encode(cmd, data))
        if not expect:
            return None
        exp = set(expect)
        end = time.time() + timeout
        while time.time() < end:
            for c, d in self.read_frames(min(0.4, end - time.time() + 0.01)):
                if c in exp:
                    return (c, d)
        return None

    # -- infos (non destructif) --
    def info(self):
        self.log(f"Port : {self.path}")
        hb = self.send(CMD['HEARTBEAT'], b'\x04', expect=[0xD9, 0xDD, 0xDE, 0xDF], timeout=0.8)
        self.log("Heartbeat : " + (hb[1].hex() if hb else "(pas de réponse)"))
        for name, key in (("device_type", 8), ("soft_ver", 9), ("battery", 10), ("serial", 11), ("hard_ver", 12)):
            r = self.send(CMD['GET_INFO'], bytes([key]), expect=None)
            fr = self.read_frames(0.4)
            val = fr[-1][1] if fr else b''
            txt = val.decode('latin1') if all(32 <= b < 127 for b in val) and val else val.hex()
            self.log(f"  {name:12s}: {txt}")

    # -- impression --
    def print_image(self, rows, width, height, density=3, quantity=1, count_mode='auto'):
        q = max(1, quantity)
        self.send(CMD['SET_DENSITY'], bytes([density]), expect=[0x31], timeout=1.5)
        self.send(CMD['SET_LABEL_TYPE'], bytes([1]), expect=[0x33], timeout=1.5)
        self.send(CMD['START_PRINT'], bytes([0x00, q & 0xff, 0, 0, 0, 0, 0]), expect=[0x02], timeout=2.0)
        self.send(CMD['START_PAGE'], bytes([1]), expect=[0x04], timeout=2.0)
        self.send(CMD['SET_PAGE_SIZE'],
                  bytes([(height >> 8) & 0xff, height & 0xff, (width >> 8) & 0xff, width & 0xff]),
                  expect=[0x14], timeout=1.5)
        if q > 1:
            self.send(CMD['SET_QUANTITY'], bytes([q & 0xff]), expect=[0x16], timeout=1.5)
        self.log(f"Envoi {height} lignes ({width}px)…")
        for y in range(height):
            self.write_all(row_packet(y, rows[y], width, count_mode))
            if (y & 0x7f) == 0:
                self.read_frames(0)  # draine les heartbeats/statuts éventuels
        self.send(CMD['END_PAGE'], bytes([1]), expect=[0xE4], timeout=6.0)
        self._wait_done(q)
        self.send(CMD['END_PRINT'], bytes([1]), expect=[0xF4], timeout=3.0)
        self.log("Fin d'impression.")

    def _wait_done(self, q, timeout=40):
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.3)
            r = self.send(CMD['PRINT_STATUS'], bytes([1]), expect=[0xB3], timeout=0.8)
            if r and len(r[1]) >= 2 and r[1][0] >= q:
                return

def popcount_thirds(row, width):
    c = [0, 0, 0]; third = width // 3
    for x in range(width):
        if (row[x >> 3] >> (7 - (x & 7))) & 1:
            c[0 if x < third else (1 if x < 2*third else 2)] += 1
    return [min(255, v) for v in c]

def row_packet(y, row, width, count_mode):
    c = [0, 0, 0] if count_mode == 'zero' else popcount_thirds(row, width)
    header = bytes([(y >> 8) & 0xff, y & 0xff, c[0], c[1], c[2], 1])
    return encode(CMD['IMAGE_ROW'], header + bytes(row))

# ---------------------------------------------------------- orientation / io --
def apply_orientation(rows, width, height, mode):
    if mode in (None, 'none'):
        return rows
    bpr = (width + 7) // 8
    def get(r, x): return (rows[r][x >> 3] >> (7 - (x & 7))) & 1
    out = [bytearray(bpr) for _ in range(height)]
    for y in range(height):
        for x in range(width):
            sy, sx = y, x
            if mode == '180': sy, sx = height-1-y, width-1-x
            elif mode == 'mirror': sx = width-1-x
            if get(sy, sx):
                out[y][x >> 3] |= 0x80 >> (x & 7)
    return out

def load_bits(path):
    raw = open(path).read().strip()
    w, h, bpr, b64 = raw.split(',', 3)
    w, h, bpr = int(w), int(h), int(bpr)
    flat = base64.b64decode(b64)
    rows = [flat[i*bpr:(i+1)*bpr] for i in range(h)]
    return rows, w, h

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bits', nargs='?', help='fichier bitmap "W,H,bpr,b64"')
    ap.add_argument('--port'); ap.add_argument('--info', action='store_true')
    ap.add_argument('--density', type=int, default=3); ap.add_argument('--qty', type=int, default=1)
    ap.add_argument('--orient', default='none'); ap.add_argument('--count', default='auto')
    args = ap.parse_args()
    m = M3(port=args.port)
    try:
        if args.info or not args.bits:
            m.info(); return
        rows, w, h = load_bits(args.bits)
        rows = apply_orientation(rows, w, h, args.orient)
        print(f"Impression {w}×{h} (densité {args.density}, qty {args.qty}, orient {args.orient})…")
        m.print_image(rows, w, h, density=args.density, quantity=args.qty, count_mode=args.count)
        print("✅ Terminé.")
    finally:
        m.close()

if __name__ == '__main__':
    main()
