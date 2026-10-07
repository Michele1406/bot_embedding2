# -*- coding: utf-8 -*-
"""Adattatore WhatsApp (core/whatsapp.py): solo logica pura e Flask test client, nessuna rete."""
import hashlib
import hmac
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import whatsapp as wa

PAYLOAD = {"entry": [{"changes": [{"value": {"messages": [
    {"id": "wamid.1", "from": "393331112222", "type": "text", "text": {"body": "  vorrei un tris  "}},
    {"id": "wamid.2", "from": "393331112222", "type": "audio", "audio": {"id": "x", "mime_type": "audio/ogg"}},
    {"id": "wamid.3", "from": "393331112222", "type": "interactive", "interactive": {"button_reply": {"title": "Si"}}},
], "statuses": [{"id": "s1"}]}}]}]}


class TestPure(unittest.TestCase):
    def test_estrai_messaggi(self):
        m = wa.estrai_messaggi(PAYLOAD)
        self.assertEqual([(x["id"], x["tipo"], x["testo"]) for x in m],
                         [("wamid.1", "text", "vorrei un tris"), ("wamid.2", "audio", ""), ("wamid.3", "text", "Si")])
        self.assertEqual(m[1]["media_id"], "x")
        self.assertEqual(wa.estrai_messaggi({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}), [])
        self.assertEqual(wa.estrai_messaggi(None), [])

    def test_firma(self):
        corpo = b'{"a":1}'
        buona = "sha256=" + hmac.new(b"segreto", corpo, hashlib.sha256).hexdigest()
        self.assertTrue(wa.verifica_firma("segreto", corpo, buona))
        self.assertFalse(wa.verifica_firma("segreto", corpo, "sha256=00"))
        self.assertFalse(wa.verifica_firma("segreto", corpo, None))
        self.assertFalse(wa.verifica_firma("segreto", b"altro", buona))
        self.assertTrue(wa.verifica_firma("", corpo, None))

    def test_markdown(self):
        t = "## Titolo\n\n**Taralli** (Forno): ottimi [IMG: c:/x.jpg]\n\n\n\n* **Olive**"
        self.assertEqual(wa.converti_markdown(t), "*Titolo*\n\n*Taralli* (Forno): ottimi\n\n* *Olive*")

    def test_spezza(self):
        self.assertEqual(wa.spezza_testo("ciao"), ["ciao"])
        self.assertEqual(wa.spezza_testo(""), [])
        testo = "\n\n".join(f"Paragrafo {i} " + "x" * 400 for i in range(20))
        parti = wa.spezza_testo(testo, 1000)
        self.assertTrue(all(len(p) <= 1000 for p in parti))
        self.assertEqual("\n\n".join(parti).replace("\n\n", " ").split(), testo.replace("\n\n", " ").split())
        lunga = "parola " * 1000
        self.assertTrue(all(len(p) <= 500 for p in wa.spezza_testo(lunga, 500)))

    def test_sid_stabile_e_anonimo(self):
        self.assertEqual(wa.sid_da_numero("393331112222"), wa.sid_da_numero("393331112222"))
        self.assertNotIn("3331112222", wa.sid_da_numero("393331112222"))

    def test_dedup(self):
        self.assertFalse(wa.gia_visto("id-unico-1"))
        self.assertTrue(wa.gia_visto("id-unico-1"))
        self.assertFalse(wa.gia_visto(None))


class TestGestisci(unittest.TestCase):
    def test_testo_ok_spezzato_e_markdown(self):
        inviati = []
        wa.gestisci_messaggio({"id": "1", "da": "39333", "tipo": "text", "testo": "ciao"},
                              lambda t, st, sid: {"reply": "**Ciao** " + "a" * 4000}, lambda sid: {}, lambda n, t: inviati.append((n, t)) or True)
        self.assertGreaterEqual(len(inviati), 2)
        self.assertTrue(inviati[0][1].startswith("*Ciao*"))
        self.assertTrue(all(len(t) <= wa.MAX_CARATTERI for _, t in inviati))

    def test_vocale_trascritto(self):
        arrivati, inviati = [], []
        msg = {"id": "9", "da": "39336", "tipo": "audio", "testo": "", "media_id": "m1", "mime": "audio/ogg"}
        wa.gestisci_messaggio(msg, lambda t, st, sid: arrivati.append(t) or {"reply": "ok"}, lambda sid: {},
                              lambda n, t: inviati.append(t), trascrivi=lambda m: "vorrei un tris")
        self.assertEqual(arrivati, ["vorrei un tris"])
        self.assertEqual(inviati, ["ok"])

    def test_vocale_non_trascrivibile(self):
        inviati = []
        msg = {"id": "9", "da": "39337", "tipo": "audio", "testo": "", "media_id": "m1"}
        wa.gestisci_messaggio(msg, lambda *a: self.fail("non va elaborato"), lambda sid: {}, lambda n, t: inviati.append(t),
                              trascrivi=lambda m: None)
        self.assertEqual(inviati, [wa.MSG_NON_SUPPORTATO])

    def test_non_testo(self):
        inviati = []
        wa.gestisci_messaggio({"id": "1", "da": "39333", "tipo": "audio", "testo": ""},
                              lambda *a: self.fail("non va elaborato"), lambda sid: {}, lambda n, t: inviati.append(t))
        self.assertEqual(inviati, [wa.MSG_NON_SUPPORTATO])

    def test_errore_di_elaborazione(self):
        inviati = []

        def boom(*a):
            raise RuntimeError("x")
        wa.gestisci_messaggio({"id": "1", "da": "39333", "tipo": "text", "testo": "ciao"}, boom, lambda sid: {},
                              lambda n, t: inviati.append(t))
        self.assertEqual(inviati, [wa.MSG_ERRORE])

    def test_stato_per_numero_riusato(self):
        visti = []
        for _ in range(2):
            wa.gestisci_messaggio({"id": "1", "da": "39334", "tipo": "text", "testo": "ciao"},
                                  lambda t, st, sid: {"reply": "ok"}, lambda sid: visti.append(sid) or {}, lambda n, t: True)
        self.assertEqual(visti[0], visti[1])


class TestFlask(unittest.TestCase):
    ENV = {"WHATSAPP_ENABLED": "1", "WHATSAPP_VERIFY_TOKEN": "vt", "WHATSAPP_ACCESS_TOKEN": "tok",
           "WHATSAPP_PHONE_NUMBER_ID": "123", "WHATSAPP_APP_SECRET": "segreto"}

    def _app(self, elabora=None):
        from flask import Flask
        app = Flask(__name__)
        with mock.patch.dict(os.environ, self.ENV):
            self.assertTrue(wa.registra_whatsapp(app, elabora or (lambda t, st, sid: {"reply": "ok"}), lambda sid: {}))
        return app

    def test_spento_di_default(self):
        from flask import Flask
        with mock.patch.dict(os.environ, {"WHATSAPP_ENABLED": "0"}):
            self.assertFalse(wa.registra_whatsapp(Flask(__name__), None, None))

    def test_config_incompleta_non_registra(self):
        from flask import Flask
        with mock.patch.dict(os.environ, {"WHATSAPP_ENABLED": "1", "WHATSAPP_VERIFY_TOKEN": "", "WHATSAPP_ACCESS_TOKEN": ""}):
            self.assertFalse(wa.registra_whatsapp(Flask(__name__), None, None))

    def test_verifica_webhook(self):
        c = self._app().test_client()
        r = c.get("/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=777")
        self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "777"))
        self.assertEqual(c.get("/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=sbagliato&hub.challenge=1").status_code, 403)

    def test_post_firma_e_elaborazione(self):
        arrivati, evento = [], threading.Event()

        def elabora(t, st, sid):
            arrivati.append(t)
            evento.set()
            return {"reply": "risposta"}

        app = self._app(elabora)
        c = app.test_client()
        payload = {"entry": [{"changes": [{"value": {"messages": [
            {"id": "wamid.firma-ok", "from": "39335", "type": "text", "text": {"body": "ciao nino"}}]}}]}]}
        corpo = json.dumps(payload).encode()
        # firma errata -> 403, nessuna elaborazione
        self.assertEqual(c.post("/webhook/whatsapp", data=corpo, headers={"X-Hub-Signature-256": "sha256=00"}).status_code, 403)
        firma = "sha256=" + hmac.new(b"segreto", corpo, hashlib.sha256).hexdigest()
        with mock.patch.object(wa, "invia_testo", return_value=True) as inv:
            r = c.post("/webhook/whatsapp", data=corpo, headers={"X-Hub-Signature-256": firma, "Content-Type": "application/json"})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(evento.wait(5))
            for _ in range(50):
                if inv.called:
                    break
                threading.Event().wait(0.05)
            self.assertTrue(inv.called)
            self.assertEqual(inv.call_args[0][1], "risposta")
        self.assertEqual(arrivati, ["ciao nino"])
        # lo stesso messaggio rinviato da Meta non si rielabora
        r2 = c.post("/webhook/whatsapp", data=corpo, headers={"X-Hub-Signature-256": firma, "Content-Type": "application/json"})
        self.assertEqual(r2.status_code, 200)
        threading.Event().wait(0.2)
        self.assertEqual(arrivati, ["ciao nino"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
