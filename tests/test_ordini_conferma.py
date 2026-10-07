# -*- coding: utf-8 -*-
"""Ordine in due passi: riepilogo -> CONFERMO -> JSON completo, senza duplicati (core/ordini.py)."""
import json
import os
import tempfile
import unittest

from core import ordini
from core.order_extractor import CheckoutOrdine, ProdottoOrdine

IDX = [{"id": "P1", "metadata": {"codice_prodotto": "TAR01", "nome_fornitore": "Farino", "reparto": "DISPENSA",
                                 "formato_variante_liv5": "BUSTA 1 KG"},
        "document": "PRODOTTO: Taralli caserecci\nCONSERVAZIONE: luogo fresco e asciutto"}]
PIVA_OK = "12345678903"


def estratto(**kw):
    base = dict(ragione_sociale="Bar Rossi srl", partita_iva=PIVA_OK, telefono="333 1234567",
                prodotti=[ProdottoOrdine(codice_articolo="TAR01", nome_prodotto="Taralli caserecci", quantita=2, unita="cartoni")])
    base.update(kw)
    return CheckoutOrdine(**base)


class TestOrdini(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ORDINI_DIR"] = self.tmp.name
        os.environ.pop("ERP_WEBHOOK_URL", None)

    def tearDown(self):
        os.environ.pop("ORDINI_DIR", None)
        self.tmp.cleanup()

    def test_conferma_e_annullamento(self):
        for t in ("CONFERMO", "confermo", "sì, confermo", "ok confermo", "procedi"):
            self.assertTrue(ordini.e_conferma(t), t)
        for t in ("confermo che voglio altri taralli e anche le olive", "non confermo", "ciao"):
            self.assertFalse(ordini.e_conferma(t), t)
        self.assertTrue(ordini.e_annullamento("annulla tutto"))

    def test_riepilogo_poi_registrazione(self):
        stato = {"citta": "Bari", "zona_consegna": "coperta"}
        bozza, msg = ordini.prepara(estratto(), IDX, stato)
        self.assertIsNotNone(bozza)
        self.assertIn("CONFERMO", msg)
        self.assertIn("2 cartoni", msg)
        self.assertEqual(os.listdir(self.tmp.name), [])  # nulla salvato prima della conferma
        ok, msg2, id_ordine = ordini.registra(bozza, stato, "sid123", [{"ruolo": "utente", "testo": "2 cartoni di taralli"}])
        self.assertTrue(ok)
        file = os.listdir(self.tmp.name)
        self.assertEqual(len(file), 1)
        dati = json.load(open(os.path.join(self.tmp.name, file[0]), encoding="utf-8"))
        self.assertEqual(dati["cliente"]["partita_iva"], PIVA_OK)
        self.assertEqual(dati["cliente"]["telefono"], "333 1234567")
        self.assertEqual(dati["righe"][0]["unita"], "cartoni")
        self.assertEqual(dati["righe"][0]["codice_prodotto"], "TAR01")
        self.assertTrue(dati["conversazione"])
        # lo stesso ordine non si registra due volte
        bozza2, msg3 = ordini.prepara(estratto(), IDX, stato)
        self.assertIsNone(bozza2)
        self.assertIn(id_ordine, msg3)

    def test_dati_mancanti(self):
        self.assertIsNone(ordini.prepara(estratto(partita_iva=None), IDX, {})[0])
        self.assertIn("non risulta valida", ordini.prepara(estratto(partita_iva="12345678901"), IDX, {})[1])
        b, msg = ordini.prepara(estratto(prodotti=[ProdottoOrdine(nome_prodotto="Caviale Beluga", quantita=1)]), IDX, {})
        self.assertIsNone(b)
        self.assertIn("Caviale Beluga", msg)

    def test_unita_mancante_segnalata(self):
        e = estratto(prodotti=[ProdottoOrdine(codice_articolo="TAR01", nome_prodotto="Taralli caserecci", quantita=3)])
        bozza, msg = ordini.prepara(e, IDX, {})
        self.assertEqual(bozza["righe"][0]["unita"], "da definire")
        self.assertTrue(bozza["avvisi"])


if __name__ == "__main__":
    unittest.main()
