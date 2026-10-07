# -*- coding: utf-8 -*-
"""Collegamenti tra prodotti (REPORT_COLLEGAMENTI.md): abbinamenti per tipo, complementari, altri formati,
dal prodotto al piatto, alternative per gli ingredienti, regione dei produttori, tratti da non ripetere."""
import os
import tempfile
import unittest

from core import territorio
from core.second_brain import SecondBrain, chiave_formato, combacia


def P(i, nome, sub="", forn="F", reparto="DISPENSA", **meta):
    m = {"sottocategoria": sub, "nome_fornitore": forn, "reparto": reparto, "tipo_prodotto": meta.pop("tipo", nome.lower())}
    m.update(meta)
    return {"id": i, "metadata": m, "document": f"PRODOTTO: {nome}", "titolo_lower": nome.lower(),
            "sottocat_lower": f"{sub} {m['tipo_prodotto']}".lower()}


TONNO = P("t", "Tonno in olio di oliva 300g", "ALTRI PRODOTTI", "Mare srl", "MARE", tipo="tonno in olio di oliva")
CIPOLLA = P("c", "Cipolla Rossa di Tropea in Agrodolce", "SOTTACETI", "Suriano", uso_antipasto=True)
CONF_CIP = P("cc", "Confettura Cipolle Rosse", "CONFETTURE/SPALMABILI FRUTTA", "Suriano")
PINSA = P("p", "Pinsa Romana", "BASI PER PIZZA E IMPASTI", "Farino")
PELATI = P("pe", "Pomodori pelati 800g", "PELATI E POMODORINI", "Gentile", modalita_uso="ingrediente")
MARM40 = P("m1", "Marmellata di arance 40g", "CONFETTURE/SPALMABILI FRUTTA", "Mongetto")
MARM1K = P("m2", "Marmellata di arance 1,2kg", "CONFETTURE/SPALMABILI FRUTTA", "Mongetto")
OLIO1 = P("o1", "Olio EVO Coratina", "OLIO EXTRAVERGINE DI OLIVA", "A", modalita_uso="ingrediente")
OLIO2 = P("o2", "Olio EVO Peranzana", "OLIO EXTRAVERGINE DI OLIVA", "B", modalita_uso="ingrediente")
RICETTA = {"id": "R1", "metadata": {"nome_piatto": "Insalata di tonno e cipolla", "categoria": "antipasto",
                                    "ingredienti_json": '[{"INGREDIENTE_GENERICO": "tonno sott\'olio", "RUOLO": "protagonista"},'
                                                        '{"INGREDIENTE_GENERICO": "cipolla rossa", "RUOLO": "secondario"}]'}}


class TestSecondBrainCollegamenti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = SecondBrain().costruisci([TONNO, CIPOLLA, CONF_CIP, PINSA, PELATI, MARM40, MARM1K, OLIO1, OLIO2], [RICETTA])

    def test_abbinamento_per_tipo(self):  # A1: il tonno non aveva abbinamenti
        self.assertEqual([a["prodotto"]["id"] for a in self.b.abbinamenti(TONNO, 1)], ["c"])  # non la confettura

    def test_complementari(self):  # B3
        self.assertEqual([c["id"] for c in self.b.complementari(PINSA)], ["pe", "o1"])  # pelati, olio

    def test_altri_formati(self):  # A3
        self.assertEqual(chiave_formato(MARM40), chiave_formato(MARM1K))
        self.assertEqual([x["id"] for x in self.b.altri_formati(MARM40)], ["m2"])
        self.assertNotIn("m2", [x["id"] for x in self.b.alternative(MARM40, 3)])  # mai alternativa di se stesso

    def test_alternative_per_ingredienti(self):  # A2
        self.assertEqual([x["id"] for x in self.b.alternative(OLIO1, 1)], ["o2"])

    def test_dal_prodotto_al_piatto(self):  # B2
        self.assertEqual(self.b.piatti_con(TONNO), ["Insalata di tonno e cipolla"])

    def test_selettore(self):
        self.assertTrue(combacia(TONNO, {"parole": ["tonno"], "reparti": ["MARE"]}, tutte=False))
        self.assertFalse(combacia(TONNO, {"parole": ["tonno"], "reparti": ["DISPENSA"]}, tutte=False))
        self.assertFalse(combacia(CONF_CIP, {"parole": ["cipoll"], "sottocategorie": ["SOTTACETI"]}, tutte=True))
        self.assertTrue(combacia(CONF_CIP, {"parole": ["cipoll"], "sottocategorie": ["SOTTACETI"]}, tutte=False))

    def test_contesto(self):
        blocco = self.b.blocco_contesto([MARM40], "avete un'alternativa a questa marmellata?", dettaglio_prodotto=True)
        self.assertIn("COLLEGAMENTI VERIFICATI DEL PRODOTTO", blocco)
        self.assertIn("1.2 kg", blocco)


class TestTerritorio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        self.tmp.write("nome_fornitore,regione,confidenza,fonte,indizi,verificata\n"
                       "Lucano srl,basilicata,media,abstract,,si\nIncerto,campania,bassa,schede prodotto,,\n")
        self.tmp.close()
        os.environ["REGIONI_PRODUTTORI_CSV"] = self.tmp.name
        territorio._CACHE.clear()

    def tearDown(self):
        os.environ.pop("REGIONI_PRODUTTORI_CSV", None)
        territorio._CACHE.clear()
        os.unlink(self.tmp.name)

    def test_regione_richiesta(self):
        self.assertEqual(territorio.regione_richiesta("che salumi lucani avete?"), "basilicata")
        self.assertIsNone(territorio.regione_richiesta("una pinsa romana e pesto alla genovese"))  # stile, non origine

    def test_solo_verificate_o_sicure(self):
        self.assertEqual(territorio.regione_produttore("Lucano srl"), "basilicata")
        self.assertIsNone(territorio.regione_produttore("Incerto"))  # bassa e non verificata: non si usa

    def test_ordine_e_filtro(self):
        rec = [P("a", "Salame", forn="Altro"), P("b", "Salsiccia", forn="Lucano srl")]
        self.assertEqual([r["id"] for r in territorio.ordina_per_regione(rec, "basilicata", "salumi lucani")], ["b", "a"])
        self.assertEqual([r["id"] for r in territorio.ordina_per_regione(rec, "basilicata", "solo salumi lucani")], ["b"])


class TestTrattiTagliere(unittest.TestCase):
    def test_due_piccanti_solo_se_serve(self):
        from core.ricettario import _scegli_vari
        S = lambda i, nome: {"id": i, "metadata": {"reparto": "SALUMI", "nome_fornitore": i, "tipo_prodotto": nome.lower()},
                             "document": f"PRODOTTO: {nome}"}
        pool = [S("1", "Salame piccante"), S("2", "Capocollo piccante"), S("3", "Capocollo dolce")]
        scelti = _scegli_vari(pool, 2, "salumi", "tagliere", 3, False)
        self.assertEqual({r["id"] for r in scelti}, {"1", "3"})
        scelti = _scegli_vari(pool, 2, "salumi", "tagliere piccante", 3, False)  # chiesto dal cliente: vale
        self.assertEqual({r["id"] for r in scelti}, {"1", "2"})


if __name__ == "__main__":
    unittest.main()
