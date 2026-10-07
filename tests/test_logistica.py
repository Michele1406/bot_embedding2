# -*- coding: utf-8 -*-
"""Test della logistica deterministica (core/logistica.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import logistica, ontologia


def doc(cons=""):
    return f"PRODOTTO: X\nINGREDIENTI: y\n\nCONSERVAZIONE:\n{cons}\n\nPRODUTTORE:\nz"


class TestZona(unittest.TestCase):
    def test_coperte(self):
        for c in ("Bari", "bari città", "Altamura", "Lecce", "Matera", "Potenza", "Martina Franca", "Puglia", "Basilicata",
                  "Monopoli (BA)", "Santa Maria di Leuca"):
            self.assertEqual(logistica.zona_cliente(c), "coperta", c)

    def test_fuori(self):
        for c in ("Milano", "Roma", "Napoli", "Sardegna", "Reggio Calabria", "L'Aquila"):
            self.assertEqual(logistica.zona_cliente(c), "fuori", c)

    def test_sconosciuta(self):
        for c in (None, "", "Paperopoli"):
            self.assertEqual(logistica.zona_cliente(c), "sconosciuta", c)

    def test_parole_intere(self):
        # "Roma" dentro "Romagna" non e' Roma (ne' Puglia): citta' non riconosciuta
        self.assertEqual(logistica.zona_cliente("Romagna"), "sconosciuta")


class TestTemperatura(unittest.TestCase):
    def test_reparti(self):
        self.assertEqual(logistica.classe_temperatura({"reparto": "GELO"}), "surgelato")
        self.assertEqual(logistica.classe_temperatura({"reparto": "FORMAGGI"}, doc("Conservare a +4°C")), "refrigerato")
        self.assertEqual(logistica.classe_temperatura({"reparto": "MARE"}), "refrigerato")

    def test_dispensa_ambiente_e_dopo_apertura(self):
        self.assertEqual(logistica.classe_temperatura({"reparto": "DISPENSA"}, doc("Conservare in luogo fresco ed asciutto.")), "ambiente")
        self.assertEqual(logistica.classe_temperatura(
            {"reparto": "DISPENSA"}, doc("Conservare in luogo fresco. Dopo l'apertura conservare in frigorifero e consumare entro 3 giorni.")),
            "ambiente")
        self.assertEqual(logistica.classe_temperatura({"reparto": "DISPENSA"}, doc("Conservare in frigorifero a +4°C.")), "refrigerato")

    def test_salume_stagionato_ambiente_se_la_scheda_lo_dice(self):
        self.assertEqual(logistica.classe_temperatura({"reparto": "SALUMI"}, doc("Conservare in luogo fresco e asciutto")), "ambiente")


class TestCasiReali(unittest.TestCase):
    def test_range_freddo_nella_scheda(self):
        self.assertEqual(logistica.classe_temperatura({"reparto": "SALUMI"}, doc("Conservare tra 0°C e +10°C.")), "refrigerato")
        self.assertEqual(logistica.classe_temperatura({"reparto": "SALUMI"}, doc("Conservare a temperatura max +15°C.")), "ambiente")

    def test_formaggio_molle_con_scheda_generica_resta_refrigerato(self):
        self.assertEqual(logistica.classe_temperatura(
            {"reparto": "FORMAGGI", "tipo_prodotto": "formaggio a pasta molle"}, "PRODOTTO: LA CREMOSA\n" + doc("Conservare in luogo fresco e asciutto.")), "refrigerato")
        self.assertEqual(logistica.classe_temperatura(
            {"reparto": "FORMAGGI", "tipo_prodotto": "grana padano"}, "PRODOTTO: GRANA PADANO\n" + doc("Conservare in luogo fresco e asciutto.")), "ambiente")


class TestFiltro(unittest.TestCase):
    def tearDown(self):
        logistica.ZONA_CORRENTE.set(None)
        ontologia.ESCLUSIONI_CORRENTI.set([])

    def test_fuori_zona_esclude_il_freddo(self):
        freddo = {"reparto": "FORMAGGI", "temperatura": "refrigerato"}
        secco = {"reparto": "DISPENSA", "temperatura": "ambiente"}
        logistica.ZONA_CORRENTE.set("fuori")
        self.assertTrue(ontologia.prodotto_escluso_da_cliente(freddo, "PRODOTTO: Mozzarella"))
        self.assertFalse(ontologia.prodotto_escluso_da_cliente(secco, "PRODOTTO: Taralli"))
        logistica.ZONA_CORRENTE.set("coperta")
        self.assertFalse(ontologia.prodotto_escluso_da_cliente(freddo, "PRODOTTO: Mozzarella"))
        logistica.ZONA_CORRENTE.set("sconosciuta")
        self.assertFalse(ontologia.prodotto_escluso_da_cliente(freddo, "PRODOTTO: Mozzarella"))

    def test_righe_contesto(self):
        r = ontologia.righe_extra_contesto({"reparto": "GELO", "nome_fornitore": "DI TRIA"})
        self.assertTrue(any("SURGELATO" in x for x in r))
        self.assertEqual(logistica.righe_logistica({"reparto": "DISPENSA", "temperatura": "ambiente"}), [])


class TestCalendario(unittest.TestCase):
    def test_riga(self):
        self.assertIn("Lunedì", logistica.riga_calendario("OBERTO"))
        self.assertIn("Giovedì", logistica.riga_calendario("ITALFISH/BALTIK/SALUMI DI MARE") or "Giovedì")
        self.assertEqual(logistica.riga_calendario("Pasta Gentile"), "")


@unittest.skipUnless(os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database_vettoriale")), "serve il DB")
class TestCatalogoReale(unittest.TestCase):
    def test_distribuzione_temperature(self):
        import chromadb
        from collections import Counter
        r = chromadb.PersistentClient("database_vettoriale").get_collection(os.getenv("CATALOGO_COLLECTION", "catalogo_v2")).get(include=["metadatas", "documents"])
        c = Counter(logistica.classe_temperatura(m, d) for m, d in zip(r["metadatas"], r["documents"]))
        print("\n[temperature catalogo]", dict(c))
        self.assertGreater(c["ambiente"], 500)
        self.assertGreater(c["surgelato"], 40)
        self.assertGreater(c["refrigerato"], 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
