# -*- coding: utf-8 -*-
"""Test del guardrail anti-allucinazione (core/guardrail_output.py).

    python -m unittest tests.test_guardrail -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import guardrail_output as g


def prod(id_, nome, fornitore, **meta):
    m = {"nome_fornitore": fornitore, "reparto": "DISPENSA", "sottocategoria": "X", "tipo_prodotto": nome.lower()}
    m.update(meta)
    return {"id": id_, "metadata": m, "document": f"PRODOTTO: {nome}\nINGREDIENTI: x"}


INDICE = [
    prod("1", "FRANCHI SALUMI - FINOCCHIONA IGP SOTTOVUOTO", "FRANCHI SALUMI", reparto="SALUMI", vegano="NO"),
    prod("2", "Olive Termite Di Bitetto", "Capuano", vegano="SI"),
    prod("3", "Carne Salada Crucolo", "Crucolo", reparto="SALUMI", vegano="NO"),
    prod("4", "FARINO - TARALLI CASERECCI", "Forni Farino", vegano="SI"),
]


class TestGuardrail(unittest.TestCase):
    def test_prodotto_inventato_viene_segnalato(self):
        t = ("Ecco tre finger food:\n- **Arancini di Riso Classici** di Arancileria: sfiziose monoporzioni.\n"
             "- **Taralli Caserecci** di Farino: da banco.")
        e = g.verifica_prodotti_citati(t, INDICE)
        self.assertEqual([c for _, c in e["non_verificati"]], ["Arancini di Riso Classici"])
        self.assertEqual(e["verificati"], 1)
        self.assertIn("Arancini di Riso Classici", g.nota_correzione(e))

    def test_prodotti_reali_ok(self):
        t = "- **Finocchiona IGP** di Franchi Salumi\n- **Carne Salada** di Crucolo\n- **Olive Termite di Bitetto**"
        e = g.verifica_prodotti_citati(t, INDICE)
        self.assertEqual(e["non_verificati"], [])
        self.assertEqual(e["verificati"], 3)
        self.assertEqual(g.nota_correzione(e), "")

    def test_titoli_e_etichette_generiche_ignorati(self):
        t = ("**I Salumi:**\n**La Selezione dei Formaggi**\n- **pasta all'uovo**: ingrediente generico\n"
             "**Nota di servizio B2B**\n**Salumificio BBS**")
        e = g.verifica_prodotti_citati(t, INDICE)
        self.assertEqual(e["non_verificati"], [])
        self.assertEqual(e["verificati"], 0)

    def test_dieta(self):
        t = "- **Finocchiona IGP** di Franchi Salumi\n- **Olive Termite di Bitetto**"
        e = g.verifica_prodotti_citati(t, INDICE, "vegano")
        self.assertEqual([c for _, c, _m in e["incompatibili"]], ["Finocchiona IGP"])
        self.assertIn("dieta", g.nota_correzione(e))

    def test_pari_merito_dieta(self):
        # due prodotti con lo stesso nome: uno non vegano, uno vegano -> NON e' incompatibile
        indice = INDICE + [prod("5", "Carciofi Spicchi al Naturale", "Marrazzo", vegano="SI*"),
                           prod("6", "Carciofi Spicchi al Naturale", "Di Tria", vegano="SI", reparto="GELO")]
        t = "- **Carciofi Spicchi al Naturale** (surgelati)"
        e = g.verifica_prodotti_citati(t, indice, "vegano")
        self.assertEqual(e["incompatibili"], [])

    def test_ripulisci_storico(self):
        t = "Ecco:\n- **Arancini di Riso Classici** di Arancileria\n- **Carne Salada** di Crucolo"
        e = g.verifica_prodotti_citati(t, INDICE)
        pulito = g.ripulisci_testo(t, e)
        self.assertNotIn("Arancini", pulito)
        self.assertIn("Carne Salada", pulito)

    def test_parafrasi_e_solo_dubbio(self):
        # copertura intermedia: non si corregge il cliente, si registra
        t = "- **Finocchiona Toscana Artigianale** di Franchi"
        e = g.verifica_prodotti_citati(t, INDICE)
        self.assertEqual(e["non_verificati"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
