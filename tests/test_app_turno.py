# -*- coding: utf-8 -*-
"""Turno completo di app.stream_messaggio_nino con un modello FINTO (nessuna chiamata API, nessuna quota):
ordine in due passi, ripiego dell'analisi sul 429, guardrail sui titoli, storico senza duplicati."""
import json
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp()
os.environ.setdefault("ORDINI_DIR", os.path.join(_TMP, "ordini"))
os.environ.setdefault("AUDIT_DIR", os.path.join(_TMP, "audit"))
os.environ.setdefault("SESSIONI_DB", os.path.join(_TMP, "sessioni.db"))
os.environ.setdefault("EMBEDDING_CACHE", "0")
os.environ.setdefault("LOG_CHAT_DIR", os.path.join(_TMP, "chat"))  # mai nei log reali


class _Testo:
    def __init__(self, t):
        self.text = t


class _ChatFinta:
    def __init__(self, padre, history):
        self.padre, self.history = padre, history

    def send_message_stream(self, prompt):
        self.padre.storie.append(list(self.history))
        self.padre.prompt.append(prompt)
        yield _Testo(self.padre.risposta)


class _ClientFinto:
    """generate_content: analisi (JSON) o estrazione ordine; chats: risposta fissa. errori_analisi: 429 simulati."""
    def __init__(self, analisi: dict, ordine: "dict | None" = None, risposta="Ecco **Taralli Caserecci**.", errori_analisi=0):
        self.analisi, self.ordine, self.risposta, self.errori_analisi = analisi, ordine, risposta, errori_analisi
        self.models, self.chats = self, self
        self.storie, self.prompt, self.modelli = [], [], []

    def generate_content(self, model, contents, config=None):
        self.modelli.append(model)
        if "CRONOLOGIA CHAT" in str(contents):
            return _Testo(json.dumps(self.ordine or {"prodotti": []}))
        if self.errori_analisi:
            self.errori_analisi -= 1
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return _Testo(json.dumps(self.analisi))

    def create(self, model, config, history):
        return _ChatFinta(self, history)


class _EmbedderRotto:
    def embed_query(self, t):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")


def _analisi(tipo="ricerca_specifica", **profilo):
    return {"tipo_richiesta": tipo, "richiede_composizione": False, "riferimento_precedente": False,
            "argomento_riferito": None, "elementi_richiesti": [{"dominio": "dispensa", "query_ricerca": "taralli"}],
            "profilo": profilo}


try:
    import app
except Exception as _e:  # database non disponibile: test saltati
    app = None


@unittest.skipIf(app is None, "app non importabile (database vettoriale mancante)")
class TestTurno(unittest.TestCase):
    def setUp(self):
        self._client, self._emb = app.client_genai, app.embedder
        app.embedder = _EmbedderRotto()

    def tearDown(self):
        app.client_genai, app.embedder = self._client, self._emb

    def _turno(self, client, testo, stato, sid="t1"):
        app.client_genai = client
        return app.elabora_messaggio_nino(testo, stato, sid)["reply"]

    def _stato(self, sid):
        app.sessioni.pop(sid, None)
        return app.stato_per_sid(sid)

    def test_storico_senza_messaggio_duplicato(self):
        stato = self._stato("t_storico")
        c = _ClientFinto(_analisi(citta="Bari", tipo_locale="bar"))
        self._turno(c, "avete i taralli?", stato, "t_storico")
        # la history passata al modello NON contiene il messaggio attuale (arriva gia' dentro il prompt)
        self.assertEqual(c.storie[-1], [])
        self.assertEqual([m.role for m in stato["storico"]], ["user", "model"])

    def test_ordine_in_due_passi(self):
        stato = self._stato("t_ordine")
        ordine = {"ragione_sociale": "Bar Rossi srl", "partita_iva": "12345678903",
                  "prodotti": [{"nome_prodotto": "Taralli caserecci Farino", "fornitore": "Farino", "quantita": 2, "unita": "cartoni"}]}
        c = _ClientFinto(_analisi("chiusura_ordine"), ordine)
        r1 = self._turno(c, "voglio ordinare 2 cartoni di taralli, P.IVA 12345678903", stato, "t_ordine")
        self.assertIn("CONFERMO", r1)
        self.assertIsNotNone(stato["ordine_in_attesa"])
        cartella = os.environ["ORDINI_DIR"]
        prima = set(os.listdir(cartella)) if os.path.isdir(cartella) else set()
        n_chiamate = len(c.modelli)
        r2 = self._turno(c, "CONFERMO", stato, "t_ordine")
        self.assertIn("Ordine registrato", r2)
        self.assertEqual(len(c.modelli), n_chiamate)  # la conferma non chiama il modello
        self.assertEqual(len(set(os.listdir(cartella)) - prima), 1)
        self.assertIsNone(stato["ordine_in_attesa"])
        self.assertEqual(stato["storico"][-1].role, "model")  # anche le risposte fisse entrano nello storico

    def test_analisi_ripiega_sul_fallback_e_poi_sulle_regole(self):
        stato = self._stato("t_429")
        c = _ClientFinto(_analisi(citta="Bari"), errori_analisi=1)  # 429 sul principale, ok sul fallback
        self._turno(c, "ciao", stato, "t_429")
        self.assertIn(app.MODELLO_FALLBACK, c.modelli)
        stato2 = self._stato("t_429b")
        c2 = _ClientFinto(_analisi(), {"prodotti": []}, errori_analisi=5)  # entrambi in 429: regole
        r = self._turno(c2, "Bar a Lecce: voglio ordinare dei taralli, P.IVA 12345678903", stato2, "t_429b")
        self.assertEqual(stato2.get("citta"), "Lecce")
        self.assertIn("prodotti da ordinare", r)  # e' andato nel flusso ordine, non ha "registrato" nulla


class TestGuardrailTitoli(unittest.TestCase):
    def test_titoli_logistici_non_sono_prodotti(self):
        from core import guardrail_output as g
        testo = "**Ordine minimo e spedizione**\n**Modalità di pagamento**\nScrivi **CONFERMO** per inviare."
        self.assertEqual(g.estrai_citazioni(testo), [])


if __name__ == "__main__":
    unittest.main()
