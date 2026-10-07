# -*- coding: utf-8 -*-
"""Informazioni aziendali da sofood/consegne.xlsx e cache degli embedding (core/info_azienda.py, core/embedder.py)."""
import os
import tempfile
import unittest

from core import info_azienda
from core.embedder import CacheEmbedding, EmbedderGemini


class TestInfoAzienda(unittest.TestCase):
    def test_solo_per_domande_aziendali(self):
        self.assertTrue(info_azienda.domanda_aziendale("qual e' l'ordine minimo per la consegna?"))
        self.assertTrue(info_azienda.domanda_aziendale("come si paga?"))
        self.assertFalse(info_azienda.domanda_aziendale("quali taralli avete?"))
        self.assertFalse(info_azienda.domanda_aziendale("tempi di cottura della pasta"))
        self.assertEqual(info_azienda.blocco_contesto("che salumi avete?"), "")

    def test_dati_reali_e_zona_cliente(self):
        b = info_azienda.blocco_contesto("quando consegnate e qual e' il minimo d'ordine?", "Lecce")
        self.assertIn("INFO SO FOOD", b)
        self.assertIn("Lecce", b)
        self.assertNotIn("Foggia:", b)       # solo la zona del cliente
        self.assertIn("24/48", b)
        self.assertNotIn("1234567", b)       # niente contatti segnaposto
        self.assertIn("commerciale", b)


class _Risposta:
    def __init__(self, v):
        self.embeddings = [type("E", (), {"values": v})()]


class _ClientFinto:
    def __init__(self):
        self.chiamate = 0
        self.models = self

    def embed_content(self, model, contents, config):
        self.chiamate += 1
        return _Risposta([0.1, 0.2, float(len(contents[0]))])


class TestCacheEmbedding(unittest.TestCase):
    def test_stessa_query_una_sola_chiamata(self):
        with tempfile.TemporaryDirectory() as d:
            client = _ClientFinto()
            cache = CacheEmbedding(os.path.join(d, "c.sqlite"))
            e = EmbedderGemini("x", "models/m", client=client, cache=cache)
            v1 = e.embed_query("Olive  baresane")
            v2 = e.embed_query("olive baresane")  # stessa query a meno di maiuscole/spazi
            self.assertEqual(v1, v2)
            self.assertEqual(client.chiamate, 1)
            # cache persistente: un nuovo processo (nuova cache in memoria) non richiama l'API
            e2 = EmbedderGemini("x", "models/m", client=client, cache=CacheEmbedding(os.path.join(d, "c.sqlite")))
            self.assertEqual(e2.embed_query("olive baresane"), v1)
            self.assertEqual(client.chiamate, 1)

    def test_quota_non_ritentata(self):
        class Rotto(_ClientFinto):
            def embed_content(self, model, contents, config):
                self.chiamate += 1
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
        with tempfile.TemporaryDirectory() as d:
            c = Rotto()
            e = EmbedderGemini("x", "m", client=c, cache=CacheEmbedding(os.path.join(d, "c.sqlite")))
            with self.assertRaises(RuntimeError):
                e.embed_query("salumi")
            self.assertEqual(c.chiamate, 1)


class TestRicettarioPertinenza(unittest.TestCase):
    def test_pertinenza_slot(self):
        from core.ricettario import _prodotto_pertinente, proponibile
        P = lambda nome, tipo="": {"metadata": {"tipo_prodotto": tipo}, "document": f"PRODOTTO: {nome}"}
        self.assertTrue(_prodotto_pertinente("spaghetti trafilati al bronzo", P("Spaghettini 500g")))
        self.assertFalse(_prodotto_pertinente("bacon affumicato", P("Capocollo affumicato")))
        self.assertFalse(_prodotto_pertinente("manzo macinato per burger", P("Hamburger di maiale nero", "hamburger di maiale")))
        self.assertTrue(_prodotto_pertinente("manzo macinato per burger", P("Fassona coscia trita", "carne macinata")))
        self.assertFalse(proponibile([{"ruolo": "protagonista", "esito": "NON_TROVATO"}]))
        self.assertTrue(proponibile([{"ruolo": "protagonista", "esito": "TROVATO"}, {"ruolo": "opzionale", "esito": "NON_TROVATO"}]))

    def test_regioni(self):
        from core.ricettario import rileva_cluster_regionale
        self.assertEqual(rileva_cluster_regionale("tagliere spagnolo"), "spagna")
        self.assertEqual(rileva_cluster_regionale("tagliere toscano"), "toscana")
        self.assertIsNone(rileva_cluster_regionale("pasta con le sarde e parmigiano"))


if __name__ == "__main__":
    unittest.main()
