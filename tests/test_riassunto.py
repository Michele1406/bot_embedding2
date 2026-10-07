# -*- coding: utf-8 -*-
"""Riassunto rolling della conversazione (core/riassunto.py)."""
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import riassunto


def msg(ruolo, testo):
    return NS(role=ruolo, parts=[NS(text=testo)])


class FakeModels:
    def __init__(self, testo=None, errore=None):
        self.testo, self.errore, self.prompts = testo, errore, []

    def generate_content(self, model, contents, config=None):
        self.prompts.append(contents)
        if self.errore:
            raise self.errore
        return NS(text=self.testo)


class FakeClient:
    def __init__(self, **kw):
        self.models = FakeModels(**kw)


class TestRiassunto(unittest.TestCase):
    def test_testo_da_contents(self):
        t = riassunto.testo_da_contents([msg("user", "voglio un tris"), msg("model", "ecco"), msg("user", "")])
        self.assertEqual(t, "Cliente: voglio un tris\nNino: ecco")

    def test_aggiorna_usa_precedente_e_nuovi(self):
        c = FakeClient(testo="- Cliente vuole un tris per il bar")
        r = riassunto.aggiorna(c, "m", "- locale a Bari", [msg("user", "voglio un tris")])
        self.assertEqual(r, "- Cliente vuole un tris per il bar")
        self.assertIn("locale a Bari", c.models.prompts[0])
        self.assertIn("voglio un tris", c.models.prompts[0])

    def test_errore_tiene_il_precedente(self):
        c = FakeClient(errore=RuntimeError("429"))
        self.assertEqual(riassunto.aggiorna(c, "m", "- vecchio", [msg("user", "ciao")]), "- vecchio")

    def test_niente_da_riassumere_non_chiama(self):
        c = FakeClient(testo="x")
        self.assertEqual(riassunto.aggiorna(c, "m", "- vecchio", []), "- vecchio")
        self.assertEqual(c.models.prompts, [])

    def test_limite_caratteri(self):
        c = FakeClient(testo="a" * 5000)
        self.assertEqual(len(riassunto.aggiorna(c, "m", "", [msg("user", "x")])), riassunto.MAX_CARATTERI)

    def test_blocco(self):
        self.assertEqual(riassunto.blocco_prompt(""), "")
        self.assertEqual(riassunto.blocco_prompt(None), "")
        self.assertIn("- punto", riassunto.blocco_prompt("- punto"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
