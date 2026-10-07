# -*- coding: utf-8 -*-
"""
Test di regressione per i bug di retrieval emersi dalle query reali
(vedi implementationplan.md, sezione 1.4 e Fase 0b).

Esecuzione:   python -m unittest tests.test_fix_retrieval -v

- Test "offline": nessuna chiamata di rete, usano un catalogo finto.
- Test "integrazione": usano ChromaDB reale + API embedding; vengono saltati se
  GEMINI_API_KEY non e' impostata (o se il DB non c'e').
"""
import os
import sys
import unittest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

from core.testo_prodotto import prima_riga, nome_prodotto, nome_senza_produttore
from core import retrieval_utils as ru


class FakeCollezione:
    def __init__(self, righe):
        self.righe = righe

    def get(self, include=None, **kw):
        return {
            "ids": [r["id"] for r in self.righe],
            "metadatas": [r["meta"] for r in self.righe],
            "documents": [r["doc"] for r in self.righe],
        }


def _riga(id_, doc, **meta):
    base = {"reparto": "SALUMI", "categoria_prodotto": "Salumi", "sottocategoria": "", "specifiche_liv4": "",
            "nome_fornitore": ""}
    base.update(meta)
    return {"id": id_, "doc": doc, "meta": base}


class TestNomeProdotto(unittest.TestCase):
    def test_riga_vuota_e_bom(self):
        self.assertEqual(prima_riga("﻿\nPRODOTTO: COPPA\nTesto"), "PRODOTTO: COPPA")
        self.assertEqual(prima_riga(""), "")
        self.assertEqual(prima_riga(None), "")

    def test_toglie_etichetta(self):
        self.assertEqual(nome_prodotto("﻿PRODOTTO: Coppa Stagionata"), "Coppa Stagionata")

    def test_toglie_produttore_solo_se_e_il_brand(self):
        self.assertEqual(
            nome_senza_produttore("PRODOTTO: FRANCHI SALUMI - WÜRSTEL SOTTOVUOTO", "FRANCHI SALUMI"),
            "WÜRSTEL SOTTOVUOTO")
        self.assertEqual(
            nome_senza_produttore("PRODOTTO: Olio Anfosso - Olive Taggiasche", "Anfosso"), "Olive Taggiasche")
        # il trattino fa parte del nome vero: non va toccato
        self.assertEqual(
            nome_senza_produttore("PRODOTTO: Prosciutto di Parma - Stagionato 24 mesi", "Franchi Salumi"),
            "Prosciutto di Parma - Stagionato 24 mesi")


class TestLessicaleSenzaProduttore(unittest.TestCase):
    def setUp(self):
        self.indice = ru.costruisci_indice_testuale(FakeCollezione([
            _riga("w", "PRODOTTO: FRANCHI SALUMI - WÜRSTEL SOTTOVUOTO", nome_fornitore="FRANCHI SALUMI",
                  sottocategoria="ALTRI", specifiche_liv4="WÜRSTEL"),
            _riga("s", "PRODOTTO: FRANCHI SALUMI - SALAME NOSTRANO", nome_fornitore="FRANCHI SALUMI",
                  sottocategoria="SALAMI", specifiche_liv4="SALAME"),
            _riga("o", "PRODOTTO: Olio Anfosso - Olive Taggiasche", nome_fornitore="Anfosso", reparto="DISPENSA",
                  sottocategoria="OLIVE"),
            _riga("e", "PRODOTTO: Guglielmi - Olio EVO IGP Bio 500ml", nome_fornitore="Guglielmi", reparto="DISPENSA",
                  sottocategoria="OLIO EXTRAVERGINE DI OLIVA"),
        ]))

    def test_marchio_non_genera_match(self):
        # R2: "salumi" non deve trovare i prodotti solo perche' il produttore si chiama Franchi Salumi
        self.assertEqual(ru.trova_match_lessicale("salumi", self.indice), [])

    def test_nome_prodotto_si_trova(self):
        ids = [r["id"] for r in ru.trova_match_lessicale("würstel", self.indice)]
        self.assertEqual(ids, ["w"])

    def test_olio_preferisce_il_vero_olio(self):
        # R9: il marchio "Olio Anfosso" non deve battere il vero olio extravergine
        ids = [r["id"] for r in ru.trova_match_lessicale("olio extravergine per ristorante", self.indice)]
        self.assertEqual(ids[0], "e")
        self.assertNotIn("o", ids)


class TestDieta(unittest.TestCase):
    def test_vegano_richiede_flag_pieno_e_reparto_ok(self):
        ok = {"reparto": "DISPENSA", "sottocategoria": "SOTTOLI", "vegano": "SI"}
        self.assertTrue(ru.prodotto_compatibile_con_dieta(ok, "vegano"))
        self.assertFalse(ru.prodotto_compatibile_con_dieta({**ok, "vegano": "SI*"}, "vegano"))
        self.assertFalse(ru.prodotto_compatibile_con_dieta({**ok, "vegano": None}, "vegano"))
        self.assertFalse(ru.prodotto_compatibile_con_dieta({**ok, "reparto": "SALUMI"}, "vegano"))
        self.assertFalse(ru.prodotto_compatibile_con_dieta({**ok, "reparto": "FORMAGGI"}, "vegano"))

    def test_vegetariano_accetta_dedotto(self):
        m = {"reparto": "FORMAGGI", "sottocategoria": "BUFALA", "vegetariano": "SI*"}
        self.assertTrue(ru.prodotto_compatibile_con_dieta(m, "vegetariano"))
        self.assertFalse(ru.prodotto_compatibile_con_dieta({**m, "reparto": "MARE"}, "vegetariano"))
        self.assertFalse(ru.prodotto_compatibile_con_dieta({**m, "vegetariano": "NO"}, "vegetariano"))

    def test_senza_dieta_tutto_ok(self):
        self.assertTrue(ru.prodotto_compatibile_con_dieta({"reparto": "SALUMI"}, None))

    def test_template_vegano(self):
        self.assertTrue(ru._template_ok_dieta("vegano, vegetariano", "vegano"))
        self.assertFalse(ru._template_ok_dieta("onnivoro", "vegano"))
        self.assertFalse(ru._template_ok_dieta(None, "vegetariano"))
        self.assertTrue(ru._template_ok_dieta("onnivoro", None))

    def test_clausole_chroma(self):
        cl = ru._clausole_dieta_chroma("vegano")
        # con il vegano dedotto attivo (regole_cliente.yaml) la clausola e' un $or: SI oppure ingredienti verificati
        self.assertTrue({"vegano": "SI"} in cl or any({"vegano": "SI"} in c.get("$or", []) for c in cl))
        self.assertTrue(any("reparto" in c for c in cl))
        self.assertEqual(ru._clausole_dieta_chroma(None), [])


class TestSalumiDiTerra(unittest.TestCase):
    def test_query(self):
        self.assertTrue(ru._query_vuole_salumi_di_terra("salumi da tagliere"))
        self.assertTrue(ru._query_vuole_salumi_di_terra("culatello"))
        self.assertFalse(ru._query_vuole_salumi_di_terra("bresaola di tonno"))
        self.assertFalse(ru._query_vuole_salumi_di_terra("tagliere di mare"))
        self.assertFalse(ru._query_vuole_salumi_di_terra("pasta per ristorante"))


# ----------------------------------------------------------------------
# Integrazione (DB reale + embedding): saltati senza chiave API
# ----------------------------------------------------------------------
def _carica_ambiente():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(RADICE, ".env"))
    except Exception:
        pass
    chiave = os.getenv("GEMINI_API_KEY")
    percorso_db = os.path.join(RADICE, "database_vettoriale")
    if not chiave or not os.path.isdir(percorso_db):
        return None
    import chromadb
    from google import genai
    from google.genai import types

    from tests.ambiente import EmbedderCache
    emb = EmbedderCache(genai.Client(api_key=chiave))

    db = chromadb.PersistentClient(path=percorso_db)
    col = db.get_collection(os.getenv("CATALOGO_COLLECTION", "catalogo_sofood"))
    return {
        "db": db, "col": col, "emb": emb,
        "ic": ru.costruisci_indice_codici(col),
        "inf": ru.costruisci_indice_fornitori(col),
        "it": ru.costruisci_indice_testuale(col),
    }


_AMB = None


def _amb():
    global _AMB
    if _AMB is None:
        _AMB = _carica_ambiente() or False
    return _AMB


@unittest.skipUnless(_amb(), "serve GEMINI_API_KEY e database_vettoriale")
class TestIntegrazione(unittest.TestCase):
    def cerca(self, q, n=10, **kw):
        a = _amb()
        return ru.cerca_prodotti(a["col"], a["ic"], a["emb"], q, n, indice_fornitori=a["inf"],
                                 indice_testuale=a["it"], **kw)

    def test_salumi_niente_wurstel_ne_pesce(self):
        for q in ("salumi", "salumi da tagliere", "antipasto di salumi per ristorante"):
            r = self.cerca(q)
            self.assertTrue(r, q)
            for x in r:
                self.assertNotIn("WURSTEL", str(x["metadata"].get("specifiche_liv4", "")).upper().replace("Ü", "U"), q)
                self.assertNotEqual(x["metadata"].get("reparto"), "MARE", q)

    def test_burro_trova_il_burro(self):
        nomi = [nome_prodotto(x["document"]).lower() for x in self.cerca("burro per ristorante", 8)]
        self.assertTrue(any("burro" in n for n in nomi[:3]), nomi)

    def test_menu_vegano_non_vuoto_e_solo_vegano(self):
        e0 = _amb()["emb"].errori
        r = self.cerca("proposta menu vegano antipasto", 8, filtro_dieta="vegano", intento="composizione_piatto")
        if _amb()["emb"].errori > e0:
            self.skipTest("embedding non disponibile (quota/rete)")
        self.assertGreaterEqual(len(r), 3)
        for x in r:
            self.assertTrue(ru.prodotto_compatibile_con_dieta(x["metadata"], "vegano"))  # SI, o SI* con ingredienti vegetali
            self.assertNotIn(x["metadata"].get("reparto"), ("SALUMI", "CARNE", "MARE", "FORMAGGI"))

    def test_tagliere_vegano_senza_carne_ne_formaggi(self):
        a = _amb()
        e0 = a["emb"].errori
        rc = a["db"].get_collection("ricette_sofood")
        out = ru.componi_proposta_da_ricettario(
            "tagliere vegano", "ristorante", rc, a["col"], a["ic"], a["emb"],
            indice_fornitori=a["inf"], indice_testuale=a["it"], query_completa="tagliere vegano",
            filtro_dieta="vegano")
        if a["emb"].errori > e0:
            self.skipTest("embedding non disponibile (quota/rete)")
        self.assertTrue(out)
        prodotti = [s["prodotto_trovato"] for s in out["slot"] if s.get("prodotto_trovato")]
        self.assertTrue(prodotti)
        for p in prodotti:
            self.assertEqual(p["metadata"].get("vegano"), "SI", p["id"])
            self.assertNotIn(p["metadata"].get("reparto"), ("SALUMI", "CARNE", "MARE", "FORMAGGI"), p["id"])

    def test_tagliere_normale_invariato(self):
        a = _amb()
        e0 = a["emb"].errori
        rc = a["db"].get_collection("ricette_sofood")
        out = ru.componi_proposta_da_ricettario(
            "vorrei un tagliere di salumi e formaggi", "ristorante", rc, a["col"], a["ic"], a["emb"],
            indice_fornitori=a["inf"], indice_testuale=a["it"], query_completa="tagliere")
        if a["emb"].errori > e0:
            self.skipTest("embedding non disponibile (quota/rete)")
        reparti = [s["prodotto_trovato"]["metadata"].get("reparto") for s in out["slot"] if s.get("prodotto_trovato")]
        self.assertIn("SALUMI", reparti)
        self.assertIn("FORMAGGI", reparti)


@unittest.skipUnless(_amb() and os.getenv("CATALOGO_COLLECTION") == "catalogo_v2", "serve CATALOGO_COLLECTION=catalogo_v2")
class TestCatalogoV2(unittest.TestCase):
    """Regole basate sugli attributi arricchiti (scripts/estrai_attributi.py + costruisci_catalogo_v2.py)."""

    def test_wurstel_non_e_da_tagliere_ma_da_panino(self):
        a = _amb()
        d = a["col"].get(include=["metadatas"])
        wurstel = [m for m in d["metadatas"] if "wurstel" in str(m.get("tipo_prodotto", "")).lower()]
        self.assertGreaterEqual(len(wurstel), 3)
        for m in wurstel:
            self.assertFalse(m["uso_tagliere"])
            self.assertTrue(m["richiede_cottura"])
            self.assertTrue(m["uso_panino_fast_food"])

    def test_tagliere_senza_prodotti_da_cuocere(self):
        a = _amb()
        r = ru.cerca_prodotti(a["col"], a["ic"], a["emb"], "salumi da tagliere", 10, indice_fornitori=a["inf"],
                              indice_testuale=a["it"], intento="tagliere_o_ricetta", canale_locale="horeca")
        self.assertTrue(r)
        for x in r:
            self.assertFalse(x["metadata"].get("richiede_cottura"), x["id"])

    def test_formato_preferito_per_horeca(self):
        a = _amb()
        r = ru.cerca_prodotti(a["col"], a["ic"], a["emb"], "pelati pomodoro", 8, indice_fornitori=a["inf"],
                              indice_testuale=a["it"], canale_locale="horeca")
        pel = [x for x in r if x["metadata"].get("tipo_prodotto") == "pomodoro pelato in succo"]
        self.assertGreaterEqual(len(pel), 2)
        self.assertEqual(pel[0]["metadata"]["canale_formato"], "horeca")

    def test_catalogo_senza_documenti_non_prodotto(self):
        a = _amb()
        d = a["col"].get(include=["metadatas"])
        self.assertFalse([i for i, m in zip(d["ids"], d["metadatas"]) if str(i).startswith("CALENDARIO_")])
        self.assertTrue(all(m.get("attributi_ok") for m in d["metadatas"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
