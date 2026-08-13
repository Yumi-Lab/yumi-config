"""Cohérence docs/REPONSES-SERVEUR.md <-> qc/qc_machine_measures.py.

La liste fail_reason publiée au serveur (à figer ensemble) ne doit pas
diverger silencieusement du code : toute valeur normée émise par les
extracteurs, tout id de test instrumenté et toute raison retest doivent
apparaître dans le document de réponses.
"""
import os
import re
import unittest

from qc import qc_machine_measures as mm

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOC = os.path.join(ROOT, "docs", "REPONSES-SERVEUR.md")
SRC = os.path.join(ROOT, "qc", "qc_machine_measures.py")


def _doc_text():
    with open(DOC) as f:
        return f.read()


def _src_text():
    with open(SRC) as f:
        return f.read()


class TestReponsesServeur(unittest.TestCase):
    def test_doc_exists(self):
        self.assertTrue(os.path.isfile(DOC))

    def test_every_fail_reason_documented(self):
        src = _src_text()
        doc = _doc_text()
        assigned = set(re.findall(r'fail_reason"\] = "(\w+)"', src))
        defaults = set(re.findall(r'_extract_\w+(?:\([^)]*\))?, "(\w+)"\)', src))
        reasons = assigned | defaults
        self.assertGreater(len(reasons), 10)  # garde-fou : le scan trouve la liste
        for reason in sorted(reasons):
            self.assertIn("`%s`" % reason, doc,
                          "fail_reason %s absent de REPONSES-SERVEUR.md" % reason)

    def test_every_extractor_test_id_documented(self):
        doc = _doc_text()
        self.assertEqual(13, len(mm._EXTRACTORS))  # séquence machine complète
        for test_id in sorted(mm._EXTRACTORS):
            self.assertIn("`%s`" % test_id, doc,
                          "test id %s absent de REPONSES-SERVEUR.md" % test_id)

    def test_retest_reasons_documented(self):
        doc = _doc_text()
        for reason in sorted(set(mm.RETEST_REASONS.values())):
            self.assertIn(reason, doc,
                          "retest_reason %s absent de REPONSES-SERVEUR.md" % reason)
        self.assertIn("previous_report", doc)  # repli documenté

    def test_software_versions_keys_documented(self):
        doc = _doc_text()
        for key in ("klipper_version", "firmware_version", "image_version",
                    "qc_cfg_version"):
            self.assertIn("`%s`" % key, doc,
                          "clé software_versions %s absente" % key)


if __name__ == "__main__":
    unittest.main()
