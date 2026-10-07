# -*- coding: utf-8 -*-
"""Audit log per risposta (core/audit_log.py)."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import audit_log


class TestAudit(unittest.TestCase):
    def test_scrive_una_riga(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AUDIT_DIR": d, "AUDIT_LOG": "1"}):
            audit_log.inizia("sessione-123456789", "vorrei un tris")
            audit_log.nota("ricetta", {"id": "TRIS_BAR_01"})
            riga = audit_log.chiudi({"tipo_locale": "bar", "esclusioni_cliente": ["tonno"]}, "ok")
            self.assertEqual(riga["profilo"], {"tipo_locale": "bar"})
            files = os.listdir(d)
            self.assertEqual(len(files), 1)
            with open(os.path.join(d, files[0]), encoding="utf-8") as f:
                r = json.loads(f.readline())
            self.assertEqual(r["ricetta"]["id"], "TRIS_BAR_01")
            self.assertEqual(r["esclusioni"], ["tonno"])
            self.assertEqual(r["query"], "vorrei un tris")

    def test_spento_e_senza_turno(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AUDIT_DIR": d, "AUDIT_LOG": "0"}):
            audit_log.inizia("s", "q")
            self.assertIsNone(audit_log.chiudi({}, ""))
            self.assertEqual(os.listdir(d), [])
        self.assertIsNone(audit_log.chiudi({}, ""))  # nessun turno aperto: nessuna eccezione

    def test_non_solleva_mai(self):
        with tempfile.NamedTemporaryFile() as f, mock.patch.dict(os.environ, {"AUDIT_DIR": os.path.join(f.name, "sotto"), "AUDIT_LOG": "1"}):
            audit_log.inizia("s", "q")  # la "cartella" e' dentro un file: la scrittura deve fallire senza eccezioni
            self.assertIsNone(audit_log.chiudi({}, ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
