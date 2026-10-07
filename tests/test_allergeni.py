# -*- coding: utf-8 -*-
"""Allergie del cliente: filtro prudente su allergeni, tracce e dati mancanti (core/allergeni.py)."""
import unittest

from core import allergeni as al
from core import ontologia


class TestAllergeni(unittest.TestCase):
    def test_categorie_da_testo(self):
        self.assertEqual(al.categorie_da_testo("sono allergico alle noci e al sesamo"), {"frutta_a_guscio", "sesamo"})
        self.assertIn("glutine", al.categorie_da_testo("ho clienti celiaci"))
        self.assertEqual(al.categorie_da_testo("vorrei olive con nocciolo"), set())  # nocciolo non e' frutta a guscio

    def test_rischio(self):
        self.assertEqual(al.rischio({"allergeni": "Frutta a guscio", "tracce_di": "Nessuna"}, "", ["frutta_a_guscio"]),
                         "contiene frutta a guscio")
        self.assertIn("tracce", al.rischio({"allergeni": "Glutine", "tracce_di": "soia e senape"}, "", ["soia"]))
        self.assertEqual(al.rischio({"allergeni": "Non specificato"}, "", ["latte"]), "allergeni non dichiarati in scheda")
        self.assertIsNone(al.rischio({"allergeni": "Nessuno rilevato", "tracce_di": "Nessuna"},
                                     "INGREDIENTI: olive, acqua, sale.", ["latte"]))
        # rete di sicurezza sugli ingredienti della scheda
        self.assertIsNotNone(al.rischio({"allergeni": "Nessuno rilevato", "tracce_di": "Nessuna"},
                                        "INGREDIENTI: farina, latte intero, sale.", ["latte"]))
        self.assertIsNone(al.rischio({"allergeni": "Frutta a guscio"}, "", []))  # nessuna allergia, nessun filtro

    def test_esclusione_nel_filtro_cliente(self):
        meta = {"allergeni": "Latte", "tracce_di": "Nessuna", "reparto": "DISPENSA"}
        token = al.ALLERGIE_CORRENTI.set(["latte"])
        try:
            self.assertTrue(ontologia.prodotto_escluso_da_cliente(meta, "PRODOTTO: Crema"))
        finally:
            al.ALLERGIE_CORRENTI.reset(token)
        self.assertFalse(ontologia.prodotto_escluso_da_cliente(meta, "PRODOTTO: Crema"))

    def test_guardrail_allergie(self):
        from core import guardrail_output as g
        idx = [{"id": "1", "metadata": {"nome_fornitore": "Forno", "allergeni": "Frutta a guscio", "tracce_di": "Nessuna"},
                "document": "PRODOTTO: Cantucci alle mandorle"}]
        e = g.verifica_prodotti_citati("- **Cantucci alle Mandorle** per il caffe'", idx, None, ["frutta_a_guscio"])
        self.assertEqual(len(e["incompatibili"]), 1)
        self.assertIn("allergie", g.nota_correzione(e))


if __name__ == "__main__":
    unittest.main()
