# -*- coding: utf-8 -*-
"""Riconoscimento dei produttori nominati dal cliente e aggancio delle schede azienda (core/anagrafica_fornitori.py)."""
import unittest

from core.anagrafica_fornitori import Anagrafica, alias_di, stesso_fornitore

NOMI = ["pastificio masciarelli", "la valletta", "italfish", "menodiciotto",
        "san salvatore | azienda agricola san salvatore 1988", "amodio | conserve gentile | forni gentile | pastificio gentile",
        "agricola buongiorno", "mulino marino", "formaggeria toscana", "salumi martina franca", "boschi", "latte nobile"]


class TestAlias(unittest.TestCase):
    def setUp(self):
        self.an = Anagrafica().costruisci(NOMI)

    def test_nomi_brevi_riconosciuti(self):
        # erano 0 risultati con la lista di alias scritta a mano
        for q, atteso in [("avete la pasta masciarelli?", "pastificio masciarelli"), ("prodotti la valletta", "la valletta"),
                          ("cosa fa italfish", "italfish"), ("prodotti menodiciotto", "menodiciotto"),
                          ("san salvatore", "san salvatore | azienda agricola san salvatore 1988"),
                          ("le conserve gentile", "amodio | conserve gentile | forni gentile | pastificio gentile")]:
            self.assertIn(atteso, self.an.fornitori_in_testo(q), q)

    def test_parole_comuni_non_sono_fornitori(self):
        for q in ("buongiorno, cerco taralli", "sale marino", "gentile cliente", "mi chiamo Salvatore", "frutti di bosco",
                  "un tagliere toscana", "salumi di mare", "il latte", "capocollo per Martina"):
            self.assertEqual(self.an.fornitori_in_testo(q), [], q)

    def test_alias_generici_esclusi(self):
        self.assertNotIn("buongiorno", alias_di("Agricola Buongiorno"))
        self.assertNotIn("gentile", alias_di("Conserve Gentile"))
        self.assertIn("masciarelli", alias_di("PASTIFICIO MASCIARELLI"))

    def test_scheda_e_catalogo_stessa_azienda(self):
        self.assertTrue(stesso_fornitore("ITALFISH/BALTIK/SALUMI DI MARE", "Italfish"))
        self.assertTrue(stesso_fornitore("Forni Farino", "Farino"))
        self.assertTrue(stesso_fornitore("CONSERVE GENTILE", "Amodio | Conserve Gentile | Forni Gentile | Pastificio Gentile"))
        self.assertTrue(stesso_fornitore("SAN SALVATORE", "San Salvatore | Azienda Agricola San Salvatore 1988"))
        self.assertFalse(stesso_fornitore("LA CASERA", "LA VALLETTA"))


class TestSchedaAzienda(unittest.TestCase):
    def test_scheda_per_nome_breve_e_per_prodotto(self):
        from core.second_brain import SecondBrain
        b = SecondBrain()
        prodotto = {"id": "p1", "metadata": {"nome_fornitore": "Italfish", "sottocategoria": "X"}, "document": "PRODOTTO: Tonno"}
        b.costruisci([prodotto], [], [({"nome_fornitore": "ITALFISH/BALTIK/SALUMI DI MARE"}, "Storia di Italfish"),
                                      ({"nome_fornitore": "PASTIFICIO MASCIARELLI"}, "Storia del pastificio")])
        self.assertIn("Masciarelli", b.scheda_azienda("chi e' masciarelli? raccontami la storia", []).title())
        self.assertIn("ITALFISH", b.scheda_azienda("raccontami la storia del produttore", [prodotto]))
        self.assertEqual(b.scheda_azienda("che tonno avete?", [prodotto]), "")  # nessuna domanda sull'azienda


if __name__ == "__main__":
    unittest.main()
