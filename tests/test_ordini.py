# -*- coding: utf-8 -*-
"""Inoltro ordine (core/cart_manager.py): senza ERP configurato l'ordine si salva in locale e non va a nessun indirizzo esterno."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.cart_manager import CartManager


class TestOrdini(unittest.TestCase):
    def _carrello(self):
        c = CartManager()
        c.init_cart("sess-1", "So Food")
        c.update_cart("sess-1", [{"codice_prodotto": "TAR001", "nome": "Taralli", "quantita": 2}],
                      {"ragione_sociale": "Bar Prova", "partita_iva": "00743110157"})
        return c

    def test_senza_erp_salva_in_locale_e_non_chiama_la_rete(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"ORDINI_DIR": d, "ERP_WEBHOOK_URL": ""}), \
                mock.patch("requests.post", side_effect=AssertionError("nessuna chiamata di rete attesa")):
            c = self._carrello()
            ok, msg = c.inoltra_ordine_erp("sess-1")
            self.assertTrue(ok)
            files = os.listdir(d)
            self.assertEqual(len(files), 1)
            dati = json.load(open(os.path.join(d, files[0]), encoding="utf-8"))
            self.assertEqual(dati["cliente"]["partita_iva"], "00743110157")
            self.assertEqual(dati["righe_ordine"][0]["quantita"], 2)
            self.assertIsNone(c.get_cart("sess-1"))  # carrello svuotato

    def test_con_erp_invia_al_webhook_configurato(self):
        with mock.patch.dict(os.environ, {"ERP_WEBHOOK_URL": "https://erp.esempio.test/ordini"}), mock.patch("requests.post") as post:
            post.return_value.raise_for_status = lambda: None
            ok, _ = self._carrello().inoltra_ordine_erp("sess-1")
            self.assertTrue(ok)
            self.assertEqual(post.call_args[0][0], "https://erp.esempio.test/ordini")

    def test_carrello_vuoto(self):
        self.assertFalse(CartManager().inoltra_ordine_erp("inesistente")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
