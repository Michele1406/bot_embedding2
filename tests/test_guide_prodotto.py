# -*- coding: utf-8 -*-
"""Guida tecnica ai prodotti (tagli di carne, legumi, riso...) e consiglio per uso: core/guide_prodotto.py."""
import unittest

from core import guide_prodotto as gp
from core.second_brain import SecondBrain


def P(i, nome, sub, reparto="CARNE", forn="Oberto", doc_extra="", **m):
    meta = {"sottocategoria": sub, "nome_fornitore": forn, "reparto": reparto, "tipo_prodotto": nome.lower()}
    meta.update(m)
    return {"id": i, "metadata": meta, "document": f"PRODOTTO: {nome}\n{doc_extra}"}


CATALOGO = [
    P("f", "Oberto - Fassona Fiorentina Singola", "COSTATA", doc_extra='FORMATO: Monoporzione 1 kg\nLinea "Griglia Selezione"'),
    P("t", "Oberto - Fassona Tagliata di Coscia Monoporzione", "COSCIA"),
    P("b", "Oberto - Fassona Biancostato con Osso", "ANTERIORE"),
    P("s", "Oberto - Pancia Disossata Suino Nero Fresco", "PANCIA"),
    P("c", "LA VALLETTA - Ceci Umbria 400g", "VEGETALI SECCHI", "DISPENSA", "La Valletta",
      doc_extra="Tempi di cottura 2 ore (previo ammollo di 12 ore)"),
    P("cc", "LA VALLETTA - Ceci Umbri Cotti 2,5 kg", "VEGETALI SECCHI", "DISPENSA", "La Valletta"),
    P("z", "LA VALLETTA - Zuppa Etrusca Italiana 400g", "VEGETALI SECCHI", "DISPENSA", "La Valletta"),
    P("r", "Agricola Lodigiana - Riso Carnaroli 1kg", "RISO BIANCO", "DISPENSA", "Agricola Lodigiana"),
    P("n", "Noce moscata macinata", "AROMI E SPEZIE", "DISPENSA", "X"),
]


def base(p):
    g = gp.guide_per(p)[0]
    return g["id"] if g else None


class TestGuidaPerProdotto(unittest.TestCase):
    def test_assegnazione(self):
        byid = {p["id"]: p for p in CATALOGO}
        self.assertEqual(base(byid["f"]), "fiorentina")
        self.assertEqual(base(byid["t"]), "tagliata")
        self.assertEqual(base(byid["b"]), "bollito")
        self.assertEqual(base(byid["s"]), "suino_fresco")      # la pancia di suino non e' un taglio da bollito
        self.assertEqual(base(byid["c"]), "ceci")
        self.assertEqual(base(byid["z"]), "zuppe")
        self.assertEqual(base(byid["r"]), "riso_carnaroli")
        self.assertIsNone(base(byid["n"]))                      # "noce" non e' un taglio fuori dalla carne

    def test_cotti_aggiungono_la_nota(self):
        byid = {p["id"]: p for p in CATALOGO}
        b, mod = gp.guide_per(byid["cc"])
        self.assertEqual(b["id"], "ceci")
        self.assertEqual([g["id"] for g in mod], ["pronti"])
        self.assertEqual(gp.guide_per(byid["c"])[1], [])

    def test_dati_della_scheda(self):
        byid = {p["id"]: p for p in CATALOGO}
        self.assertEqual(gp.dati_scheda(byid["f"])["linea"], "Griglia Selezione")
        self.assertIn("2 ore", gp.dati_scheda(byid["c"])["cottura"])
        riga = gp.riga(byid["c"])
        self.assertIn("tempi di cottura", riga)
        self.assertIn("ammollo", riga)
        self.assertIn("gia' porzionato", gp.riga(byid["f"]))   # monoporzione dal formato

    def test_nessuna_riga_senza_guida(self):
        self.assertEqual(gp.riga(CATALOGO[-1]), "")


class TestConsiglio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brain = SecondBrain().costruisci(CATALOGO)

    def test_griglia_per_persone(self):
        c = gp.consiglio("che taglio mi consigli per una grigliata per 20 persone?", self.brain)
        self.assertIsNotNone(c)
        self.assertIn("Fiorentina", c["blocco"])
        self.assertIn("20 persone x 500 g = circa 10 kg", c["blocco"])
        self.assertIn("f", [p["id"] for p in c["prodotti"]])

    def test_legumi_per_zuppa(self):
        c = gp.consiglio("che legumi mi consigli per una zuppa?", self.brain)
        self.assertIsNotNone(c)
        ids = {p["id"] for p in c["prodotti"]}
        self.assertTrue(ids & {"c", "z"})
        self.assertNotIn("f", ids)

    def test_non_scatta_senza_consiglio_o_uso(self):
        self.assertIsNone(gp.consiglio("fammi una carbonara", self.brain))
        self.assertIsNone(gp.consiglio("quanto costa la fiorentina?", self.brain))
        self.assertIsNone(gp.consiglio("che vini avete?", self.brain))   # niente carne/legumi nominati

    def test_bollito_senza_nominare_la_carne(self):
        c = gp.consiglio("e per un bollito?", self.brain)
        self.assertEqual([p["id"] for p in c["prodotti"]], ["b"])

    def test_olio_per_il_pesce_dal_fruttato_della_scheda(self):
        oli = [P("ol", "Guglielmi - Olio Leggero 500ML", "OLIO EXTRAVERGINE DI OLIVA", "DISPENSA", "Guglielmi",
                 doc_extra="Tipo di fruttato - Fruttato Leggero (Blend)"),
               P("oi", "Guglielmi - Olio Intenso 500ml", "OLIO EXTRAVERGINE DI OLIVA", "DISPENSA", "Guglielmi",
                 doc_extra="Tipo di fruttato - Fruttato Intenso (Coratina)")]
        self.assertEqual(base(oli[0]), "olio_leggero")
        self.assertIn("fruttato: Fruttato Leggero", gp.riga(oli[0]))
        c = gp.consiglio("che olio mi consigli per il pesce?", SecondBrain().costruisci(oli))
        self.assertEqual([p["id"] for p in c["prodotti"]], ["ol"])

    def test_olio_non_e_un_accompagnamento(self):
        # chat 2026-10-07: "con l'Olio Leggero sta benissimo il Tonno affumicato" (abbinamento statistico)
        from core.second_brain import _e_accompagnamento
        olio = P("ol", "Guglielmi - Olio Leggero 500ML", "OLIO EXTRAVERGINE DI OLIVA", "DISPENSA", "Guglielmi",
                 uso_antipasto=True)
        self.assertFalse(_e_accompagnamento(olio))

    def test_pesce_in_olio_non_prende_le_verdure(self):
        self.assertEqual(base(P("t", "Tonno in olio di oliva", "ALTRI PRODOTTI", "MARE", "Delfino")), "pesce_olio")
        self.assertIsNone(base(P("m", "Melanzane a filetti in olio", "SOTTOLI", "DISPENSA", "Marrazzo")))

    def test_fabbisogno(self):
        g = {"porzione": "circa 150-200 g a persona"}
        self.assertEqual(gp.fabbisogno("per 40 coperti", g), "40 persone x 150-200 g = circa 6-8 kg")
        self.assertEqual(gp.fabbisogno("per tutti", g), "")


if __name__ == "__main__":
    unittest.main()
