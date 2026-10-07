# -*- coding: utf-8 -*-
"""Test dell'abstention lessicale (core/copertura_richiesta.py).

    python -m unittest tests.test_copertura -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import copertura_richiesta as cr


def p(nome, desc="", **meta):
    m = {"nome_fornitore": "Forn", "tipo_prodotto": nome.lower(), "sottocategoria": "X", "specifiche_liv4": ""}
    m.update(meta)
    return {"id": nome, "metadata": m, "document": f"PRODOTTO: {nome}\nDESCRIZIONE: {desc}"}


# df >= 3 per "parmigiano" cosi' un refuso viene riconosciuto
INDICE = [p("Parmigiano Reggiano 24 mesi"), p("Parmigiano grattugiato"), p("Parmigiano 36 mesi"),
          p("Carne Salada Crucolo"), p("Salame Nostrano"), p("Taralli caserecci"), p("Olive Termite di Bitetto"),
          p("Finocchiona IGP", "salume toscano")]


class TestCopertura(unittest.TestCase):
    def test_prodotto_assente(self):
        self.assertEqual(cr.termini_senza_riscontro("dammi tre arancini di riso", INDICE), ["arancini"])
        self.assertIn("wagyu", cr.termini_senza_riscontro("sushi di wagyu giapponese", INDICE))

    def test_prodotti_presenti(self):
        for q in ("salame nostrano", "taralli caserecci", "olive termite", "finocchiona", "carne salada"):
            self.assertEqual(cr.termini_senza_riscontro(q, INDICE), [], q)

    def test_plurali_e_radici(self):
        self.assertEqual(cr.termini_senza_riscontro("dei taralli", INDICE), [])
        self.assertEqual(cr.termini_senza_riscontro("un salame", INDICE), [])

    def test_refusi_frequenti(self):
        self.assertEqual(cr.termini_senza_riscontro("con parmiggiano", INDICE), [])

    def test_verbi_e_parole_generiche_non_scattano(self):
        for q in ("parlami delle aziende", "dimmi che salumi hai", "fammi un tagliere per il mio ristorante",
                  "proponimi qualche idea", "mi spiego meglio"):
            self.assertEqual(cr.termini_senza_riscontro(q, INDICE), [], q)

    def test_citta_e_regioni_non_sono_prodotti(self):
        for q in ("sono un bar a Milano", "ho un locale a Monopoli in Puglia", "ho un locale in Sardegna"):
            self.assertEqual(cr.termini_senza_riscontro(q, INDICE), [], q)
        self.assertEqual(cr.termini_senza_riscontro("arancini a Milano", INDICE), ["arancini"])

    def test_riga_contesto(self):
        self.assertEqual(cr.riga_contesto([]), "")
        r = cr.riga_contesto(["arancini"])
        self.assertIn("'arancini'", r)
        self.assertIn("non presentare come sostituti", r)

    def test_indice_vuoto(self):
        self.assertEqual(cr.termini_senza_riscontro("arancini", []), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
