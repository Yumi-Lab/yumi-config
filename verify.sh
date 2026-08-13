#!/bin/bash
# Verification gate for the QC YMS workstream.
set -euo pipefail

cd "$(dirname "$0")"

ROOT="$(pwd)"
QC="$ROOT/qc"

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

echo "== 1/4 py_compile qc/*.py =="
for py in "$QC"/*.py; do
    python3 -m py_compile "$py" || fail "py_compile failed for $py"
done
echo "  OK"

echo "== 2/4 generate_yms12_cfg.py =="
(cd "$QC" && python3 generate_yms12_cfg.py) || fail "generate_yms12_cfg.py failed"
echo "  OK"

echo "== 3/4 parse qc_printer_YMS12.cfg =="
python3 - <<'PY' || fail "configparser failed"
import configparser, os, sys
p = os.path.join(os.path.dirname(__file__), "qc", "qc_printer_YMS12.cfg")
cfg = configparser.ConfigParser(strict=False)
cfg.read(p)
if not cfg.sections():
    sys.exit("no sections")
print("  sections:", len(cfg.sections()))
PY

echo "== 4/4 unittest discover qc/tests =="
python3 -m unittest discover -s qc/tests -v || fail "unittest failed"
echo "  OK"

echo "verify.sh: PASS"
