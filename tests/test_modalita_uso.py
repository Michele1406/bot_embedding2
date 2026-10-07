# -*- coding: utf-8 -*-
"""
Test della logica "prodotto singolo / solo ingrediente / entrambi" (core/ontologia.py).

    python -m unittest tests.test_modalita_uso -v

Esempi del cliente: petali di tartufo = solo ingrediente; anacardi al tartufo = singolo e topping;
carciofi sott'olio = singolo e composizione di sottoli.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import ontologia


def rec(id_, nome, modalita, usi=(), **meta):
    m = {"reparto": "DISPENSA", "sottocategoria": "X", "tipo_prodotto": nome.lower(), "modalita_uso": modalita,
         "usi_ingr": "|" + "|".join(usi) + "|" if usi else "",
         "snack_tipo": "", "formato_valore": 100.0, "nome_fornitore": "F" + id_}
    for u in ("topping_finitura", "composizione_sottoli", "aroma_spezia", "farcitura"):
        m[f"ingr_{u}"] = u in usi
    m.update(meta)
    return {"id": id_, "metadata": m, "document": f"PRODOTTO: {nome}\nINGREDIENTI: x"}


PETALI = rec("petali", "Petali di Tartufo con Tartufo Estivo", "ingrediente", ("topping_finitura",))
ANACARDI = rec("anacardi", "Anacardi tostati e salati al tartufo", "entrambi", ("topping_finitura",), snack_tipo="frutta_secca")
CARCIOFI = rec("carciofi", "Carciofi Grigliati Interi", "entrambi", ("composizione_sottoli", "farcitura"),
               sottocategoria="SOTTOLI")
MELANZANE = rec("melanzane", "Melanzane Grigliate", "entrambi", ("composizione_sottoli",), sottocategoria="SOTTOLI")
TARALLI = rec("taralli", "Taralli caserecci", "singolo", snack_tipo="taralli")
SPEZIA = rec("pepe", "Pepe nero macinato", "ingrediente", ("aroma_spezia",))


class TestEscludiSoloIngredienti(unittest.TestCase):
    def setUp(self):
        self.tutti = [PETALI, ANACARDI, CARCIOFI, TARALLI, SPEZIA]

    def ids(self, query):
        return [r["id"] for r in ontologia.escludi_solo_ingredienti(query, self.tutti)]

    def test_proposta_generica_niente_ingredienti(self):
        ids = self.ids("cosa mi consigli per l'aperitivo")
        self.assertNotIn("petali", ids)
        self.assertNotIn("pepe", ids)
        for ok in ("anacardi", "carciofi", "taralli"):
            self.assertIn(ok, ids)

    def test_nominato_dal_cliente_resta(self):
        self.assertIn("petali", self.ids("avete i petali di tartufo?"))
        self.assertIn("pepe", self.ids("pepe nero"))

    def test_richiesta_di_ingredienti_resta(self):
        ids = self.ids("vorrei qualche topping per finire i piatti")
        self.assertIn("petali", ids)

    def test_match_esplicito_resta(self):
        r = dict(SPEZIA, match_esatto=True)
        self.assertEqual(len(ontologia.escludi_solo_ingredienti("dammi quello", [r])), 1)


class TestSlotConModalita(unittest.TestCase):
    def test_ciotolina_non_usa_solo_ingredienti(self):
        s = ontologia.risolvi_slot("anacardi o nocciole")
        indice = [dict(PETALI, metadata=dict(PETALI["metadata"], snack_tipo="frutta_secca")), ANACARDI]
        ids = [r["id"] for r in ontologia.scegli(s, indice, n=3)]
        self.assertEqual(ids, ["anacardi"])

    def test_topping_ammette_solo_ingredienti_e_entrambi(self):
        s = ontologia.risolvi_slot("petali di tartufo come topping")
        self.assertEqual(s["famiglia"], "topping_finitura")
        ids = {r["id"] for r in ontologia.scegli(s, [PETALI, ANACARDI, TARALLI], n=5)}
        self.assertEqual(ids, {"petali", "anacardi"})

    def test_sottoli_composizione_per_ortaggio_specifico(self):
        s = ontologia.risolvi_slot("carciofi sott'olio")
        self.assertEqual(s["famiglia"], "sottoli_composizione")
        ids = [r["id"] for r in ontologia.scegli(s, [CARCIOFI, MELANZANE, TARALLI], n=5)]
        self.assertEqual(ids, ["carciofi"])  # niente ripiego su melanzane
        s2 = ontologia.risolvi_slot("verdure sott'olio")
        ids2 = {r["id"] for r in ontologia.scegli(s2, [CARCIOFI, MELANZANE, TARALLI], n=5)}
        self.assertEqual(ids2, {"carciofi", "melanzane"})

    def test_aromi(self):
        s = ontologia.risolvi_slot("mix di spezie")
        self.assertEqual(s["famiglia"], "aromi_spezie")
        self.assertEqual([r["id"] for r in ontologia.scegli(s, [SPEZIA, TARALLI])], ["pepe"])


class TestDescrizioneUso(unittest.TestCase):
    def test_testi(self):
        self.assertIn("SOLO INGREDIENTE", ontologia.descrizione_uso(PETALI["metadata"]))
        self.assertIn("singolo E ingrediente", ontologia.descrizione_uso(ANACARDI["metadata"]))
        self.assertEqual(ontologia.descrizione_uso(TARALLI["metadata"]), "")
        self.assertEqual(ontologia.descrizione_uso({}), "")


class TestConservazioneOlive(unittest.TestCase):
    def test_righe_contesto(self):
        m = {"snack_tipo": "olive_olio", "conservazione": "olio", "denocciolate": True}
        self.assertEqual(ontologia.descrizione_conservazione(m), "Olive: SOTT'OLIO, denocciolate")
        m2 = {"snack_tipo": "olive_salamoia", "conservazione": "salamoia", "denocciolate": False}
        self.assertEqual(ontologia.descrizione_conservazione(m2), "Olive: in salamoia, intere con nocciolo")
        self.assertEqual(ontologia.descrizione_conservazione({"snack_tipo": "taralli"}), "")
        # dal ciclo 3 ogni prodotto porta anche la riga degli allergeni (core/allergeni.py)
        self.assertEqual(ontologia.righe_extra_contesto(m2), ["Olive: in salamoia, intere con nocciolo",
                                                              "Allergeni: non dichiarati in scheda"])

    def test_sottolio_senza_chiedere_denocciolate_prefer_con_nocciolo(self):
        den = rec("den", "Olive Leccino Denocciolate", "singolo", snack_tipo="olive_olio", denocciolate=True, conservazione="olio")
        con = rec("con", "Olive Schiacciate sott'olio", "singolo", snack_tipo="olive_olio", denocciolate=False, conservazione="olio")
        s = ontologia.risolvi_slot("olive da tavola", "olive sott'olio")
        self.assertIsNone(s["denocciolate"])
        ids = [r["id"] for r in ontologia.scegli(s, [den, con], n=2)]
        self.assertEqual(ids[0], "con")


class TestVeganoDedotto(unittest.TestCase):
    def test_riga(self):
        m = {"vegano": "SI*", "vegano_ingredienti_ok": True}
        self.assertIn("dedotto dagli ingredienti", ontologia.righe_extra_contesto(m)[0])
        self.assertEqual(ontologia.descrizione_vegano_dedotto({"vegano": "SI", "vegano_ingredienti_ok": True}), "")
        self.assertEqual(ontologia.descrizione_vegano_dedotto({"vegano": "SI*", "vegano_ingredienti_ok": False}), "")


class TestEsclusioniCliente(unittest.TestCase):
    def tearDown(self):
        ontologia.ESCLUSIONI_CORRENTI.set([])

    def test_escluso_per_nome(self):
        tonno = rec("tonno", "Tonno in olio di oliva", "singolo", sottocategoria="CONSERVE")
        self.assertTrue(ontologia.prodotto_escluso_da_cliente(tonno["metadata"], tonno["document"], ["tonno"]))
        self.assertFalse(ontologia.prodotto_escluso_da_cliente(TARALLI["metadata"], TARALLI["document"], ["tonno"]))

    def test_non_guarda_la_descrizione(self):
        senape = {"id": "s", "metadata": {"tipo_prodotto": "senape", "sottocategoria": "SALSE"},
                  "document": "PRODOTTO: Senape Media\nDESCRIZIONE: ottima con il tonno e la carne"}
        self.assertFalse(ontologia.prodotto_escluso_da_cliente(senape["metadata"], senape["document"], ["tonno"]))

    def test_contesto_corrente_applicato_da_scegli(self):
        s = ontologia.risolvi_slot("taralli")
        self.assertEqual(len(ontologia.scegli(s, [TARALLI], n=1)), 1)
        ontologia.ESCLUSIONI_CORRENTI.set(["taralli"])
        self.assertEqual(ontologia.scegli(s, [TARALLI], n=1), [])

    def test_vuote(self):
        self.assertFalse(ontologia.prodotto_escluso_da_cliente(TARALLI["metadata"], TARALLI["document"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
