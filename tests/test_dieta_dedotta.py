# -*- coding: utf-8 -*-
"""Opzione 'vegano dedotto verificato sugli ingredienti' (default spenta).

    python -m unittest tests.test_dieta_dedotta -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import retrieval_utils as ru
from core.attributi_derivati import completa_flag_dieta, correggi_flag_dieta, vegano_ingredienti_ok

REGOLE_BASE = {"richiede_flag_assoluto": "SI", "esclude_reparti": ["CARNE", "SALUMI", "MARE", "FORMAGGI"],
               "esclude_sottocategorie": ["UOVA"]}


def doc(ing, extra=""):
    return f"PRODOTTO: X\nINGREDIENTI: {ing}\nVALORI NUTRIZIONALI: ...\n{extra}"


class TestVeganoIngredienti(unittest.TestCase):
    def test_pulito(self):
        self.assertTrue(vegano_ingredienti_ok({"vegano": "SI*"}, doc("Olive, acqua, sale. Correttore di acidita': E270.")))

    def test_ingrediente_animale(self):
        self.assertFalse(vegano_ingredienti_ok({"vegano": "SI*"}, doc("Pomodoro, burro, sale")))
        self.assertFalse(vegano_ingredienti_ok({"vegano": "SI*"}, doc("Farina, uova, sale")))

    def test_tracce_animali(self):
        self.assertFalse(vegano_ingredienti_ok({"vegano": "SI*"}, doc("Olive, acqua, sale. Puo contenere tracce di latte.")))

    def test_ingredienti_assenti_o_flag_diverso(self):
        self.assertFalse(vegano_ingredienti_ok({"vegano": "SI*"}, "PRODOTTO: X\nDESCRIZIONE: buono"))
        self.assertFalse(vegano_ingredienti_ok({"vegano": "NO"}, doc("Olive, acqua")))
        self.assertFalse(vegano_ingredienti_ok({"vegano": "SI"}, doc("Olive, acqua")))


class TestPoliticaDieta(unittest.TestCase):
    META = {"reparto": "DISPENSA", "sottocategoria": "OLIVE", "vegano": "SI*", "vegano_ingredienti_ok": True}

    def _compat(self, regole):
        with mock.patch("core.config_manager.get_regole_dieta", return_value=regole):
            return ru.prodotto_compatibile_con_dieta(self.META, "vegano")

    def test_default_spenta_si_stella_escluso(self):
        self.assertFalse(self._compat(REGOLE_BASE))

    def test_opzione_accesa_accetta_verificati(self):
        self.assertTrue(self._compat({**REGOLE_BASE, "accetta_dedotto_se_ingredienti_vegetali": True}))

    def test_opzione_accesa_non_accetta_non_verificati(self):
        meta = dict(self.META, vegano_ingredienti_ok=False)
        with mock.patch("core.config_manager.get_regole_dieta",
                        return_value={**REGOLE_BASE, "accetta_dedotto_se_ingredienti_vegetali": True}):
            self.assertFalse(ru.prodotto_compatibile_con_dieta(meta, "vegano"))

    def test_reparti_esclusi_restano_validi(self):
        meta = dict(self.META, reparto="SALUMI")
        with mock.patch("core.config_manager.get_regole_dieta",
                        return_value={**REGOLE_BASE, "accetta_dedotto_se_ingredienti_vegetali": True}):
            self.assertFalse(ru.prodotto_compatibile_con_dieta(meta, "vegano"))

    def test_clausola_chroma(self):
        with mock.patch("core.config_manager.get_regole_dieta", return_value=REGOLE_BASE):
            self.assertIn({"vegano": "SI"}, ru._clausole_dieta_chroma("vegano"))
        with mock.patch("core.config_manager.get_regole_dieta",
                        return_value={**REGOLE_BASE, "accetta_dedotto_se_ingredienti_vegetali": True}):
            cl = ru._clausole_dieta_chroma("vegano")
            self.assertTrue(any("$or" in c for c in cl))


class TestCorrezioniFlag(unittest.TestCase):
    def test_vegano_si_con_panna_diventa_no(self):
        r = correggi_flag_dieta({"vegano": "SI", "vegetariano": "SI"}, doc("panna, patate, burro, tartufo, sale"))
        self.assertEqual(r["vegano"], "NO")
        self.assertNotIn("vegetariano", r)  # la panna e' vegetariana

    def test_seppia_toglie_anche_vegetariano(self):
        r = correggi_flag_dieta({"vegano": "SI", "vegetariano": "SI"}, doc("funghi, nero di seppia (mollusco), sale"))
        self.assertEqual((r["vegano"], r["vegetariano"]), ("NO", "NO"))

    def test_vegano_vero_e_marketing_non_toccati(self):
        self.assertEqual(correggi_flag_dieta({"vegano": "SI"}, doc("farina, olio, lievito, sale", "senza formaggi, vegan")), {})
        self.assertEqual(correggi_flag_dieta({"vegano": "SI"}, doc("farina, olio, sale. " + "x" * 400 + " formaggi")), {})

    def test_senza_non_conta(self):
        self.assertEqual(correggi_flag_dieta({"vegano": "SI"}, doc("farina, olio, senza latte, sale")), {})

    def test_solo_il_si_si_corregge(self):
        self.assertEqual(correggi_flag_dieta({"vegano": "NO"}, doc("latte")), {})
        self.assertEqual(correggi_flag_dieta({"vegano": "SI*"}, doc("latte")), {})

    def test_fornitori_senza_flag(self):
        self.assertEqual(completa_flag_dieta({"codice_fornitore": "19010930", "vegano": "NO"}, doc("semola di grano duro, acqua")),
                         {"vegano": "SI*", "vegetariano": "SI*"})
        self.assertEqual(completa_flag_dieta({"codice_fornitore": "19010930"}, doc("semola, uova")), {})
        self.assertEqual(completa_flag_dieta({"codice_fornitore": "19010004"}, doc("semola, acqua")), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
