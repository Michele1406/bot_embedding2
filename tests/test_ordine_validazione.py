# -*- coding: utf-8 -*-
"""Validazione dell'ordine prima dell'inoltro (core/ordine_validazione.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import ordine_validazione as ov


def prod(id_, nome, forn, codice):
    m = {"reparto": "DISPENSA", "sottocategoria": "X", "tipo_prodotto": nome.lower(), "nome_fornitore": forn, "codice_prodotto": codice}
    return {"id": id_, "metadata": m, "document": f"PRODOTTO: {forn} - {nome}\nPESO/FORMATO: 1 kg", "titolo_lower": nome.lower(), "sottocat_lower": ""}


INDICE = [prod("a", "Taralli Pugliesi Classici", "Forno Bari", "TAR001"),
          prod("b", "Olive Termite di Bitetto", "Capuano", "OLV010"),
          prod("c", "Pasta Mista Gourmet", "Masciarelli", "PAS500"),
          prod("d", "Pasta Mista Classica", "Masciarelli", "PAS501")]


class TestPiva(unittest.TestCase):
    def test_valide(self):
        for p in ("00743110157", "01114601006", "IT 00743110157", "0074 3110 157"):
            self.assertTrue(ov.piva_valida(p), p)

    def test_non_valide(self):
        for p in ("12345678901", "00743110158", "123", "", None, "00000000000", "abcdefghijk"):
            self.assertFalse(ov.piva_valida(p), p)


class TestRighe(unittest.TestCase):
    def test_per_codice_e_per_nome(self):
        v, s = ov.valida_righe([{"codice_articolo": "TAR001", "nome_prodotto": "boh", "quantita": 2},
                                {"codice_articolo": None, "nome_prodotto": "Olive Termite di Bitetto", "fornitore": "Capuano", "quantita": 1}], INDICE)
        self.assertEqual(s, [])
        self.assertEqual([(r["codice_prodotto"], r["quantita"]) for r in v], [("TAR001", 2), ("OLV010", 1)])
        self.assertEqual(v[0]["nome"], "Forno Bari - Taralli Pugliesi Classici")  # nome del catalogo, non quello del modello

    def test_inesistente(self):
        v, s = ov.valida_righe([{"nome_prodotto": "Arancini di Riso", "fornitore": "Arancileria", "quantita": 3}], INDICE)
        self.assertEqual(v, [])
        self.assertEqual(s[0][1], "non presente a catalogo")

    def test_ambiguo(self):
        v, s = ov.valida_righe([{"nome_prodotto": "Pasta Mista Masciarelli", "quantita": 1}], INDICE)
        self.assertEqual(v, [])
        self.assertIn("ambiguo", s[0][1])

    def test_quantita_non_valida(self):
        v, s = ov.valida_righe([{"codice_articolo": "TAR001", "nome_prodotto": "x", "quantita": 0},
                                {"codice_articolo": "TAR001", "nome_prodotto": "x", "quantita": 5000}], INDICE)
        self.assertEqual(len(s), 2)
        v, s = ov.valida_righe([{"codice_articolo": "TAR001", "nome_prodotto": "x", "quantita": "tre"}], INDICE)
        self.assertEqual(v[0]["quantita"], 1)

    def test_vuoto(self):
        self.assertEqual(ov.valida_righe([], INDICE), ([], []))
        self.assertEqual(ov.valida_righe(None, INDICE), ([], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
