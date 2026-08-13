#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# shlint.sh — détecte les fonctions shell qui écrivent PLUSIEURS fois sur stdout non redirigé.
#
# Piège visé (vécu — gate impassable PAR CONSTRUCTION depuis sa création) : un helper capturé
# par $(...) faisait `echo "$out" | tee -a "$LOG"` (stdout, tee laisse passer) PUIS
# `printf '%s' "$out"` comme valeur de retour → sortie DOUBLE → l'extraction numérique rend
# deux nombres → la comparaison meurt en « integer expression expected ». Sournois : le défaut
# a été ré-introduit dans un script neuf UNE HEURE après avoir été corrigé ailleurs.
#
# Règle : dans une fonction capturée par $(...), le SEUL stdout est la valeur rendue —
# traces vers >&2, log vers >>"$LOG".
#
# Heuristique : ≥2 lignes produisant du stdout non redirigé (echo/printf/cat/tee sans >&2,
# sans >>fichier, sans >fichier) dans le corps d'une même fonction. Les faux positifs
# (fonctions d'affichage pur, jamais capturées) sont possibles : avertissement, pas verdict.
#
# Usage : ./shlint.sh fichier.sh [...]   — rc=1 si au moins un avertissement, 0 sinon.
# ─────────────────────────────────────────────────────────────────────────────
rc=0
for f in "$@"; do
  [ -f "$f" ] || { echo "shlint: fichier introuvable : $f" >&2; rc=1; continue; }
  out="$(awk '
    function eff() { b = (bcount > bmax ? bcount : bmax); return base + b }
    function report() {
      if (infunc && !skip && eff() >= 2)
        printf "%s:%d: fonction %s() — %d écritures stdout NON redirigées (une fonction capturée par $(...) ne doit émettre QUE sa valeur de retour ; traces >&2, log >>fichier ; # shlint-ok sur la déclaration pour une fonction d AFFICHAGE assumée)\n", FILENAME, fstart, fname, eff()
    }
    /^[[:space:]]*(function[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\(\)[[:space:]]*\{/ {
      report(); infunc=1; depth=0; fstart=NR
      base=0; bcount=0; bmax=0; incase=0
      skip = ($0 ~ /shlint-ok/) ? 1 : 0
      fname=$0; sub(/^[[:space:]]*(function[[:space:]]+)?/, "", fname); sub(/[[:space:]]*\(\).*/, "", fname)
    }
    infunc {
      depth += gsub(/{/, "{") - gsub(/}/, "}")
      line=$0; sub(/#.*$/, "", line)
      # Une écriture stdout non redirigée ?
      w = 0
      if (line ~ /(^|[;&|[:space:]])(echo|printf|cat|tee)([[:space:]]|$)/) {
        if (line !~ />&2/ && line !~ />>/ && line !~ /[^&2]>[[:space:]]*["$\/A-Za-z0-9_.]/) w = 1
      }
      # Les branches d un case sont EXCLUSIVES : on ne retient que la branche la plus bavarde.
      if (line ~ /(^|[;[:space:]])case[[:space:]].*[[:space:]]in([[:space:]]|$)/) incase = 1
      if (w) { if (incase) bcount++; else base++ }
      if (incase && line ~ /;;[[:space:]]*$/) { if (bcount > bmax) bmax = bcount; bcount = 0 }
      if (line ~ /^[[:space:]]*esac/) { if (bcount > bmax) bmax = bcount; base += bmax; bmax = 0; bcount = 0; incase = 0 }
      # depth 0 sur la ligne de déclaration = fonction UNE LIGNE (l accolade ouvrante est déjà
      # comptée par la regex de déclaration) : fermer aussi dans ce cas, sinon la ligne suivante
      # du fichier serait comptée dans le corps (faux rc=1 sur un idiome courant).
      if (depth <= 0) { report(); infunc=0 }
    }
    END { report() }
  ' "$f")"
  [ -n "$out" ] && { printf '%s\n' "$out"; rc=1; }
done
exit $rc
