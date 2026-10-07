# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.parse_formato import parse_formato, formato_prodotto

# (stringa reale del catalogo, valore atteso in g/ml, unita', canale)
CASI = [
    ("VASETTO VETRO 200 GR", 200, "g", "retail"),
    ("BOTTIGLIA IN VETRO 10CL", 100, "ml", "retail"),
    ("BOTTIGLIA QUADRA 100CL", 1000, "ml", "horeca"),
    ("BOTTIGLIA IN VETRO 1 LITRO", 1000, "ml", "horeca"),
    ("Vaso in vetro da 720ml", 720, "ml", "misto"),
    ("INTERO 2.5 KG", 2500, "g", "horeca"),
    ("INTERO 2 KG", 2000, "g", "horeca"),
    ("VASO IN VETRO 2800G POMODORI APPASSITI SOTT'OLIO", 2800, "g", "horeca"),
    ("BAFFA INTERA ASTUCCIO 0,8/1,3 KG", 800, "g", "misto"),
    ("BUSTA 250G (5X50G)", 250, "g", "retail"),
    ("VASCHETTA DA 1,8KG - 2,5LT", 1800, "g", "horeca"),
    ("6 x 1 kg", 6000, "g", "horeca"),
    ("922 g", 922, "g", "misto"),
    ("Confezione da 500gr", 500, "g", "retail"),
    ("500GR - BIOLOGICO", 500, "g", "retail"),
    ("Macinino ricaricabile da 150 g", 150, "g", "retail"),
    ("VASCHETTA 1.8KG - SENZA GLUTENE", 1800, "g", "horeca"),
]


class TestParseFormato(unittest.TestCase):
    def test_casi_reali(self):
        for testo, valore, unita, canale in CASI:
            r = parse_formato(testo)
            self.assertAlmostEqual(r["valore"] or -1, valore, msg=testo)
            self.assertEqual(r["unita"], unita, testo)
            self.assertEqual(r["canale_formato"], canale, testo)

    def test_senza_peso(self):
        self.assertEqual(parse_formato("")["canale_formato"], "sconosciuto")
        self.assertEqual(parse_formato(None)["valore"], None)
        r = parse_formato("TRANCIO INTERO")
        self.assertIsNone(r["valore"])
        self.assertTrue(r["intero"])
        self.assertEqual(r["canale_formato"], "horeca")
        self.assertEqual(parse_formato("AFFETTATO VASCHETTA")["canale_formato"], "sconosciuto")

    def test_pezzi_e_variabile(self):
        r = parse_formato("Monoporzione da 120 g - Confezione da 10 pezzi")
        self.assertEqual(r["pezzi"], 10)
        self.assertAlmostEqual(r["valore"], 120)
        self.assertTrue(parse_formato("Peso variabile, circa 4,4 kg a pezzo")["peso_variabile"])

    def test_non_confonde_lettere(self):
        # "glutine", "gusto", "grande" contengono g/l ma non sono unita'
        self.assertIsNone(parse_formato("GUSTO GRANDE SENZA GLUTINE")["valore"])

    def test_fonti_in_cascata(self):
        meta = {"formato_variante_liv5": ""}
        doc = "PRODOTTO: X\nFORMATO: 3 kg\nDESCRIZIONE: ..."
        self.assertAlmostEqual(formato_prodotto(meta, doc)["valore"], 3000)
        self.assertAlmostEqual(formato_prodotto({"formato_variante_liv5": "BUSTA 150G"}, doc)["valore"], 150)
        self.assertAlmostEqual(formato_prodotto({}, "﻿\nPRODOTTO: FARINA 1KG\n")["valore"], 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
