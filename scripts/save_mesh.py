#!/usr/bin/env python3
"""Persiste les profils bed_mesh de Klipper dans printer.cfg SANS reboot.

Reproduit exactement ce que fait SAVE_CONFIG, mais uniquement pour la
sous-section `#*# [bed_mesh ...]` du bloc autosave : remplacement chirurgical,
toutes les autres sections sauvees (input_shaper, PID, probe...) sont
preservees a l'identique. Ecriture atomique + backup => jamais de brick au boot.

Lecture de l'etat reel du mesh via l'API Moonraker (= memoire vive de Klipper).
"""
import json
import os
import sys
import tempfile
import urllib.request

MOONRAKER_URL = os.environ.get("MOONRAKER_URL", "http://127.0.0.1:7125")
CFG_PATH = os.environ.get(
    "PRINTER_CFG", os.path.expanduser("~/printer_data/config/printer.cfg")
)
AUTOSAVE_HEAD = "#*# <"
PARAM_ORDER = [
    "x_count", "y_count", "mesh_x_pps", "mesh_y_pps",
    "algo", "tension", "min_x", "max_x", "min_y", "max_y",
]


def fmt_val(v):
    """Formate une valeur de mesh_params comme le ferait Klipper."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v):
            return "%.1f" % v          # 10.0, 325.0
        return ("%.6f" % v).rstrip("0")  # 322.93, 0.2
    return str(v)


def build_block(name, prof):
    """Construit les lignes `#*#` d'une section [bed_mesh <name>]."""
    p = prof["mesh_params"]
    pts = prof["points"]
    out = ["#*# [bed_mesh %s]" % name, "#*# version = 1", "#*# points ="]
    for row in pts:
        out.append("#*# \t  " + ", ".join("%.6f" % float(z) for z in row))
    for k in PARAM_ORDER:
        out.append("#*# %s = %s" % (k, fmt_val(p[k])))
    return out


def section_profile_name(section_lines):
    """Retourne le nom de profil si la section est un bed_mesh, sinon None."""
    header = section_lines[0]
    inner = header[len("#*# ["):].rstrip()
    if inner.endswith("]"):
        inner = inner[:-1]
    inner = inner.strip()
    if inner == "bed_mesh":
        return "default"
    if inner.startswith("bed_mesh "):
        return inner[len("bed_mesh "):].strip()
    return None


def parse_autosave(block):
    """Decoupe le bloc autosave en (preamble, [sections])."""
    lines = block.rstrip("\n").split("\n")
    i, n = 0, len(lines)
    preamble = []
    while i < n and lines[i].strip() != "#*#":
        preamble.append(lines[i])
        i += 1
    sections = []
    while i < n:
        if lines[i].strip() == "#*#":
            i += 1
            cur = []
            while i < n and lines[i].strip() != "#*#":
                cur.append(lines[i])
                i += 1
            if cur:
                sections.append(cur)
        else:
            i += 1
    return preamble, sections


def rebuild_autosave(preamble, sections):
    out = list(preamble)
    for sec in sections:
        out.append("#*#")
        out.extend(sec)
    return "\n".join(out) + "\n"


def replace_bed_mesh(text, profiles):
    """Remplace les sections bed_mesh par celles de `profiles` (dict Moonraker).

    - Une section bed_mesh dont le nom existe dans profiles est regeneree.
    - Un profil absent du fichier est ajoute apres la derniere section bed_mesh.
    - Les sections non-bed_mesh ne sont jamais touchees.
    """
    idx = text.find(AUTOSAVE_HEAD)
    if idx == -1:
        raise RuntimeError("Bloc SAVE_CONFIG introuvable dans printer.cfg")
    head, block = text[:idx], text[idx:]
    preamble, sections = parse_autosave(block)

    used = set()
    last_mesh_pos = -1
    for pos, sec in enumerate(sections):
        name = section_profile_name(sec)
        if name is None:
            continue
        last_mesh_pos = pos
        if name in profiles:
            sections[pos] = build_block(name, profiles[name])
            used.add(name)

    # profils presents en memoire mais absents du fichier -> ajout
    leftovers = [n for n in profiles if n not in used]
    insert_at = last_mesh_pos + 1 if last_mesh_pos >= 0 else len(sections)
    for off, name in enumerate(leftovers):
        sections.insert(insert_at + off, build_block(name, profiles[name]))

    return head + rebuild_autosave(preamble, sections)


def fetch_profiles():
    url = "%s/printer/objects/query?bed_mesh" % MOONRAKER_URL
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.load(r)
    bm = data["result"]["status"]["bed_mesh"]
    profiles = bm.get("profiles", {})
    if not profiles:
        raise RuntimeError("Aucun profil bed_mesh en memoire")
    return profiles


def atomic_write(path, content):
    backup = path + ".bak"
    # backup de l'existant
    with open(path, "r") as f:
        old = f.read()
    with open(backup, "w") as f:
        f.write(old)
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".save_mesh_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main():
    profiles = fetch_profiles()
    with open(CFG_PATH, "r") as f:
        text = f.read()
    new_text = replace_bed_mesh(text, profiles)
    if new_text == text:
        print("bed_mesh deja a jour — aucune ecriture.")
        return 0
    atomic_write(CFG_PATH, new_text)
    print("OK — %d profil(s) bed_mesh persiste(s) dans %s (backup .bak)" %
          (len(profiles), CFG_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
