# -*- coding: utf-8 -*-
"""
Test delle regole di dominio (core/ontologia*.py, core/attributi_derivati.py) e del ricettario v2.

    python -m unittest tests.test_ontologia -v

Gli offline non richiedono rete. Gli integrazione richiedono GEMINI_API_KEY, catalogo_v2 e ricette_v2
(scripts/costruisci_catalogo_v2.py + scripts/aggiorna_ricettario.py).
"""
import os
import sys
import unittest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

from core import ontologia
from core.attributi_derivati import classifica_snack, sezione_ingredienti


def _p(id_, nome, ing="", **meta):
    base = {"reparto": "DISPENSA", "sottocategoria": "OLIVE", "nome_fornitore": "X", "tipo_prodotto": ""}
    base.update(meta)
    doc = f"PRODOTTO: {nome}\nINGREDIENTI: {ing}\nVALORI NUTRIZIONALI: x"
    d = classifica_snack(base, doc)
    base.update(d)
    return {"id": id_, "metadata": base, "document": doc}


class TestAttributiDerivati(unittest.TestCase):
    def test_olive_in_salamoia(self):
        r = classifica_snack({"reparto": "DISPENSA", "sottocategoria": "OLIVE"},
                             "PRODOTTO: Olive Termite Di Bitetto\nINGREDIENTI: Olive, acqua, sale, correttore di acidita': E270.\nVALORI")
        self.assertEqual((r["snack_tipo"], r["conservazione"], r["denocciolate"]), ("olive_salamoia", "salamoia", False))

    def test_olive_denocciolate(self):
        r = classifica_snack({"reparto": "DISPENSA", "sottocategoria": "OLIVE"},
                             "PRODOTTO: Olive Bella Di Cerignola Denocciolate\nINGREDIENTI: Olive, acqua, sale.\nVALORI")
        self.assertTrue(r["denocciolate"])

    def test_olive_in_olio_evo_nel_titolo(self):
        r = classifica_snack({"reparto": "DISPENSA", "sottocategoria": "OLIVE"},
                             "PRODOTTO: Olio Anfosso - Olive Taggiasche denocciolate in olio extravergine di oliva 2,8 kg\nINGREDIENTI: Olive, olio\nVALORI")
        self.assertEqual(r["snack_tipo"], "olive_olio")
        self.assertTrue(r["denocciolate"])

    def test_non_sono_olive(self):
        for nome in ("I De Giorgi - Spalmabili - Patè Olive Nere 125g", "IZZICA MELANZANE IN OLIO OLIVA 210GR",
                     "Condimento a base di olio extra vergine di oliva", "FILETTI DI TONNO IN OLIO DI OLIVA"):
            r = classifica_snack({"reparto": "DISPENSA", "sottocategoria": "SOTTOLI"}, f"PRODOTTO: {nome}\nINGREDIENTI: x\n")
            self.assertEqual(r["snack_tipo"], "", nome)

    def test_altri_snack(self):
        self.assertEqual(classifica_snack({"reparto": "DISPENSA", "sottocategoria": "TARALLI"}, "PRODOTTO: FARINO - TARALLI CASERECCI")["snack_tipo"], "taralli")
        self.assertEqual(classifica_snack({"reparto": "DISPENSA", "sottocategoria": "PATATINE"}, "PRODOTTO: PATATINE DI MONTAGNA")["snack_tipo"], "patatine")
        self.assertEqual(classifica_snack({"reparto": "DISPENSA", "sottocategoria": "GRISSINI"}, "PRODOTTO: FARINO - GRISSINI")["snack_tipo"], "pane_croccante")
        self.assertEqual(classifica_snack({"reparto": "DISPENSA", "sottocategoria": "FRUTTA SECCA SENZA GUSCIO"}, "PRODOTTO: Anacardi tostati e salati")["snack_tipo"], "frutta_secca")
        self.assertEqual(classifica_snack({"reparto": "GELO", "sottocategoria": "SURG VEGETALI PREPARATI"}, "PRODOTTO: VERDORATE IN PASTELLA")["snack_tipo"], "finger_food_caldo")
        self.assertEqual(classifica_snack({"reparto": "GELO", "sottocategoria": "SURG SPECIALITA' SALATE"}, "PRODOTTO: PARMIGIANA DI MELANZANE")["snack_tipo"], "")

    def test_sezione_ingredienti(self):
        self.assertIn("acqua", sezione_ingredienti("PRODOTTO: x\nINGREDIENTI: Olive, acqua, sale.\nVALORI NUTRIZIONALI: ..."))


class TestRisolviSlot(unittest.TestCase):
    def test_olive_default_salamoia_non_denocciolate(self):
        s = ontologia.risolvi_slot("olive da tavola", "vorrei un tris per il mio bar")
        self.assertEqual(s["snack_tipo"], ["olive_salamoia"])
        self.assertFalse(s["denocciolate"])

    def test_olive_override_esplicito(self):
        self.assertEqual(ontologia.risolvi_slot("olive da tavola", "olive sott'olio")["snack_tipo"], ["olive_olio"])
        s = ontologia.risolvi_slot("olive da tavola", "voglio olive denocciolate")
        self.assertTrue(s["denocciolate"])

    def test_non_olive(self):
        self.assertIsNone(ontologia.risolvi_slot("crema di olive o formaggio spalmabile", ""))
        self.assertIsNone(ontologia.risolvi_slot("olio extravergine di oliva", ""))

    def test_altre_famiglie(self):
        self.assertEqual(ontologia.risolvi_slot("taralli o grissini pugliesi")["snack_tipo"], ["taralli", "pane_croccante"])
        self.assertEqual(ontologia.risolvi_slot("anacardi o nocciole")["snack_tipo"], ["frutta_secca"])
        self.assertEqual(ontologia.risolvi_slot("patatine")["snack_tipo"], ["patatine"])
        self.assertEqual(ontologia.risolvi_slot("finger food caldo")["snack_tipo"], ["finger_food_caldo"])
        self.assertIsNone(ontologia.risolvi_slot("mozzarella"))


class TestScegli(unittest.TestCase):
    def setUp(self):
        self.indice = [
            _p("brine_5kg", "OLIVE BARESANE (TERMITE DI BITETTO)", "Olive, acqua, sale", formato_valore=5000.0),
            _p("brine_3kg", "Olive Bella Di Cerignola Giganti", "Olive, acqua, sale", formato_valore=3100.0),
            _p("denocc", "Olive Bella Di Cerignola Denocciolate", "Olive, acqua, sale", formato_valore=720.0),
            _p("olio", "I De Giorgi - I Sott'olii - Olive Schiacciate", "Olive, olio EVO, sale", formato_valore=250.0),
            _p("pate", "Patè Olive Nere", "Olive nere, olio", formato_valore=110.0, sottocategoria="PATE' E SPALMABILI SALATI"),
        ]

    def test_default_solo_salamoia_intere(self):
        s = ontologia.risolvi_slot("olive da tavola", "")
        ids = [r["id"] for r in ontologia.scegli(s, self.indice, n=5)]
        self.assertEqual(set(ids), {"brine_5kg", "brine_3kg"})
        self.assertEqual(ids[0], "brine_3kg")  # formato ideale 500-3500 g prima del secchio da 5 kg

    def test_pate_mai(self):
        s = ontologia.risolvi_slot("olive da tavola", "sott'olio")
        ids = [r["id"] for r in ontologia.scegli(s, self.indice, n=5)]
        self.assertEqual(ids, ["olio"])

    def test_nessun_candidato(self):
        s = ontologia.risolvi_slot("olive da tavola", "")
        self.assertEqual(ontologia.scegli(s, [i for i in self.indice if i["id"] not in ("brine_5kg", "brine_3kg")]), [])

    def test_esclusi_ultimi(self):
        s = ontologia.risolvi_slot("olive da tavola", "")
        ids = [r["id"] for r in ontologia.scegli(s, self.indice, esclusi={"brine_3kg"}, n=2)]
        self.assertEqual(ids, ["brine_5kg", "brine_3kg"])

    def test_filtra_olive_in_ricerca_libera(self):
        risultati = [{"id": r["id"], "metadata": r["metadata"], "document": r["document"]} for r in self.indice]
        ids = [r["id"] for r in ontologia.filtra_olive_in_risultati("olive per il bar", risultati)]
        self.assertNotIn("denocc", ids)
        self.assertNotIn("olio", ids)
        self.assertIn("brine_3kg", ids)
        ids2 = [r["id"] for r in ontologia.filtra_olive_in_risultati("olive denocciolate", risultati)]
        self.assertIn("denocc", ids2)


class TestTemplatePreferiti(unittest.TestCase):
    def test_tris_bar(self):
        self.assertEqual(ontologia.template_preferiti("vorrei un tris per il mio bar", "bar")[0], "TRIS_BAR_01")
        self.assertEqual(ontologia.template_preferiti("un tris", "pub")[0], "TRIS_BAR_01")

    def test_tris_bar_vegano(self):
        self.assertEqual(ontologia.template_preferiti("un tris", "bar", "vegano")[0], "TRIS_BAR_05")
        self.assertEqual(ontologia.template_preferiti("un tris vegano", "bar")[0], "TRIS_BAR_05")

    def test_tris_ristorante_e_generico(self):
        self.assertEqual(ontologia.template_preferiti("un tris", "ristorante")[0], "APERITIVO_TRIS_CLASSICO")
        self.assertEqual(ontologia.template_preferiti("un tris", None)[0], "APERITIVO_TRIS_CLASSICO")

    def test_tris_di_salumi_per_bar_non_e_tris_bar(self):
        self.assertEqual(ontologia.template_preferiti("un tris di salumi e formaggi", "bar")[0], "APERITIVO_TRIS_CLASSICO")

    def test_finger_food(self):
        self.assertEqual(ontologia.template_preferiti("dei finger food caldi", "pub")[0], "FINGER_FOOD_BAR_01")

    def test_altre_richieste(self):
        self.assertEqual(ontologia.template_preferiti("un tagliere di salumi", "bar"), [])


# ----------------------------------------------------------------------
# Integrazione
# ----------------------------------------------------------------------
def _ambiente():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(RADICE, ".env"))
        chiave = os.getenv("GEMINI_API_KEY")
        import chromadb
        db = chromadb.PersistentClient(path=os.path.join(RADICE, "database_vettoriale"))
        nomi = {c.name for c in db.list_collections()}
        if not chiave or not {"catalogo_v2", "ricette_v2"} <= nomi:
            return None
        from google import genai
        from google.genai import types
        from core import retrieval_utils as ru

        class Emb:
            def __init__(self):
                self.c = genai.Client(api_key=chiave)

            def embed_query(self, t):
                r = self.c.models.embed_content(model="models/gemini-embedding-2", contents=[t],
                                                config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"))
                v = r.embeddings[0]
                return list(v.values if hasattr(v, "values") else v)

        col = db.get_collection("catalogo_v2")
        return {"ru": ru, "col": col, "rc": db.get_collection("ricette_v2"), "emb": Emb(),
                "ic": ru.costruisci_indice_codici(col), "inf": ru.costruisci_indice_fornitori(col),
                "it": ru.costruisci_indice_testuale(col)}
    except Exception:
        return None


_A = None


def _a():
    global _A
    if _A is None:
        _A = _ambiente() or False
    return _A


@unittest.skipUnless(_a(), "serve GEMINI_API_KEY + catalogo_v2 + ricette_v2")
class TestTrisIntegrazione(unittest.TestCase):
    def proposta(self, richiesta, locale, dieta=None, esclusi=None):
        a = _a()
        return a["ru"].componi_proposta_da_ricettario(
            richiesta, locale, a["rc"], a["col"], a["ic"], a["emb"], indice_fornitori=a["inf"],
            indice_testuale=a["it"], query_completa=richiesta, filtro_dieta=dieta, ricette_escluse=esclusi)

    def test_tris_bar_tre_ciotoline_di_snack(self):
        out = self.proposta("vorrei un tris per il mio bar", "bar")
        self.assertTrue(out["template"]["id_ricetta"].startswith("TRIS_BAR"))
        snack = [s["prodotto_trovato"]["metadata"]["snack_tipo"] for s in out["slot"] if s["prodotto_trovato"]]
        self.assertEqual(len(snack), 3)
        self.assertIn("olive_salamoia", snack)
        for s in out["slot"]:
            self.assertNotEqual(s["prodotto_trovato"]["metadata"].get("reparto"), "SALUMI")

    def test_olive_sempre_in_salamoia_e_non_denocciolate(self):
        for richiesta, locale in (("un tris per il mio bar", "bar"), ("un tris", "ristorante"), ("tris", "pub")):
            out = self.proposta(richiesta, locale)
            for s in out["slot"]:
                p = s.get("prodotto_trovato")
                if p and "olive da tavola" in s["ingrediente_richiesto"]:
                    self.assertEqual(p["metadata"]["snack_tipo"], "olive_salamoia")
                    self.assertFalse(p["metadata"]["denocciolate"])

    def test_altro_tris_cambia_template(self):
        a = self.proposta("vorrei un tris per il mio bar", "bar")
        b = self.proposta("dammi un altro tris", "bar", esclusi={a["template"]["id_ricetta"]})
        self.assertNotEqual(a["template"]["id_ricetta"], b["template"]["id_ricetta"])

    def test_finger_food_distinti(self):
        out = self.proposta("vorrei dei finger food caldi per il mio pub", "pub")
        ids = [s["prodotto_trovato"]["id"] for s in out["slot"] if s["prodotto_trovato"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 3)

    def test_olive_esplicite_sottolio_rispettate(self):
        out = self.proposta("tris con olive sott'olio per il bar", "bar")
        olive = [s["prodotto_trovato"] for s in out["slot"] if s["prodotto_trovato"] and "olive" in s["ingrediente_richiesto"]]
        self.assertTrue(olive)
        self.assertEqual(olive[0]["metadata"]["snack_tipo"], "olive_olio")

    def test_ricerca_libera_olive(self):
        a = _a()
        r = a["ru"].cerca_prodotti(a["col"], a["ic"], a["emb"], "olive per il bar", 8, indice_fornitori=a["inf"],
                                   indice_testuale=a["it"], canale_locale="horeca")
        olive = [x for x in r if str(x["metadata"].get("snack_tipo", "")).startswith("olive")]
        self.assertGreaterEqual(len(olive), 3)
        for x in olive:
            self.assertEqual(x["metadata"]["snack_tipo"], "olive_salamoia")
            self.assertFalse(x["metadata"]["denocciolate"])


class TestOlivePastellate(unittest.TestCase):
    def test_olive_calde_tengono_le_pastellate(self):
        # la regex conteneva un backspace al posto di \b: "olive calde" non riconosceva mai le pastellate
        r = [{"id": "x", "metadata": {"snack_tipo": "finger_food_caldo"}, "document": "PRODOTTO: Olive pastellate"}]
        self.assertEqual(len(ontologia.filtra_olive_in_risultati("olive calde per il pub", r)), 1)
        self.assertEqual(ontologia.filtra_olive_in_risultati("olive per il bar", r), [])


class TestNessunCarattereDiControllo(unittest.TestCase):
    def test_sorgenti_puliti(self):
        """Gli heredoc della shell trasformano \\b in backspace: nessun sorgente deve contenere caratteri di controllo."""
        import glob
        radice = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for f in glob.glob(os.path.join(radice, "core", "*.py")) + glob.glob(os.path.join(radice, "*.py")) \
                + glob.glob(os.path.join(radice, "tests", "*.py")) + glob.glob(os.path.join(radice, "core", "*.yaml")):
            testo = open(f, encoding="utf-8").read()
            self.assertFalse([c for c in testo if ord(c) < 32 and c not in "\n\r\t"], f)


if __name__ == "__main__":
    unittest.main(verbosity=2)
