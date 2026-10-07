# -*- coding: utf-8 -*-
"""Test della persistenza delle sessioni (core/sessioni_store.py).

    python -m unittest tests.test_sessioni_store -v
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = tempfile.mkdtemp()
os.environ["SESSIONI_DB"] = os.path.join(_TMP, "sessioni_test.db")

from google.genai import types

from core import sessioni_store as ss


def stato_esempio():
    return {
        "storico": [types.Content(role="user", parts=[types.Part.from_text(text="siamo un ristorante vegano")]),
                    types.Content(role="model", parts=[types.Part.from_text(text="Perfetto, solo prodotti vegetali.")])],
        "prodotti_mostrati": {"19010901_CAP00025", "19010843_FARINO4"},
        "prodotti_mostrati_ordinati": ["19010901_CAP00025", "19010843_FARINO4"],
        "ricette_mostrate": {"TRIS_BAR_01"},
        "log_chat": [{"ruolo": "utente", "testo": "ciao", "timestamp": "2026-10-05 10:00:00"}],
        "contatore_messaggi": 2,
        "tipo_locale": "ristorante", "filtro_dieta": "vegano", "senza_affettatrice": True, "citta": "Bari",
        "canale_locale": "horeca", "stile_cucina": "pugliese", "ultimo_piatto_proposto": "Tris Vegetale",
    }


class TestSessioniStore(unittest.TestCase):
    def test_roundtrip_profilo_e_storico(self):
        self.assertTrue(ss.salva("sid-1", stato_esempio()))
        r = ss.carica("sid-1")
        self.assertEqual(r["filtro_dieta"], "vegano")  # il vincolo dieta sopravvive al riavvio
        self.assertEqual(r["tipo_locale"], "ristorante")
        self.assertTrue(r["senza_affettatrice"])
        self.assertEqual(r["prodotti_mostrati"], {"19010901_CAP00025", "19010843_FARINO4"})
        self.assertEqual(r["ricette_mostrate"], {"TRIS_BAR_01"})
        self.assertEqual(r["contatore_messaggi"], 2)
        self.assertEqual([c.role for c in r["storico"]], ["user", "model"])
        self.assertEqual(r["storico"][0].parts[0].text, "siamo un ristorante vegano")
        self.assertEqual(r["log_chat"][0]["testo"], "ciao")

    def test_assente(self):
        self.assertIsNone(ss.carica("non-esiste"))

    def test_aggiornamento_sovrascrive(self):
        s = stato_esempio()
        ss.salva("sid-2", s)
        s["filtro_dieta"] = None
        ss.salva("sid-2", s)
        self.assertIsNone(ss.carica("sid-2")["filtro_dieta"])

    def test_scadute(self):
        ss.salva("sid-3", stato_esempio())
        vecchio = ss._TTL_SEC
        try:
            ss._TTL_SEC = -1
            self.assertIsNone(ss.carica("sid-3"))
        finally:
            ss._TTL_SEC = vecchio


if __name__ == "__main__":
    unittest.main(verbosity=2)
