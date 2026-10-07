# -*- coding: utf-8 -*-
"""Costruzione del tagliere insieme, passo passo (core/costruzione_tagliere.py): richiesta del cliente 2026-10-06."""
import unittest

from core import costruzione_tagliere as ct


def S(i, nome, rep="SALUMI", forn=None):
    return {"id": i, "metadata": {"reparto": rep, "nome_fornitore": forn or f"F{i}", "tipo_prodotto": nome.lower(),
                                  "uso_tagliere": True, "attributi_ok": True},
            "document": f"PRODOTTO: {nome}"}


SALUMI = [S("s1", "Prosciutto crudo"), S("s2", "Salame felino"), S("s3", "Coppa"), S("s4", "Mortadella"),
          S("s5", "Bresaola"), S("s6", "Lardo"), S("s7", "Prosciutto cotto"), S("s8", "Finocchiona"),
          S("s9", "Speck"), S("s10", "Capocollo"), S("s11", "Culatello"), S("s12", "Soppressata")]
FORMAGGI = [S(f"f{k}", n, "FORMAGGI") for k, n in enumerate(["Parmigiano 24 mesi", "Gorgonzola", "Taleggio",
                                                               "Pecorino stagionato", "Caciocavallo"], 1)]
INDICE = SALUMI + FORMAGGI


def stato(*messaggi):
    return {"log_chat": [{"ruolo": "utente", "testo": m} for m in messaggi]}


TAGLIERE = {"categoria": "tagliere", "nome_piatto": "Tagliere"}


class TestParsing(unittest.TestCase):
    def test_numeri(self):
        self.assertEqual(ct.conteggi_espliciti("4 salumi e 2 formaggi"), (4, 2))
        self.assertEqual(ct.conteggi_espliciti("tagliere di salumi e formaggi, 2 e 5"), (2, 5))
        self.assertEqual(ct.conteggi_espliciti("3 e 3", attesa_coppia=True), (3, 3))
        self.assertEqual(ct.conteggi_espliciti("fammi un tagliere"), (None, None))  # mai numeri inventati

    def test_scelte(self):
        lista = ["a", "b", "c", "d"]
        self.assertEqual(ct.scelte("prendo 1, 3 e 4", lista, {}), ["a", "c", "d"])
        self.assertEqual(ct.scelte("il secondo e l'ultimo", lista, {}), ["b", "d"])
        self.assertEqual(ct.scelte("la coppa", lista, {"a": "Coppa piacentina", "b": "Salame"}), ["a"])

    def test_domanda_non_e_scelta(self):
        self.assertTrue(ct.e_domanda("com'e' il terzo?"))
        self.assertFalse(ct.e_domanda("prendo il terzo"))


class TestFlusso(unittest.TestCase):
    def test_passo_passo(self):
        st = stato("fammi un tagliere di salumi e formaggi, 3 e 2", "non mi convince")
        r = ct.turno(st, "non mi convince", INDICE, TAGLIERE)
        self.assertEqual(r["fase"], "proposta_insieme")
        self.assertIn("3 salumi e 2 formaggi", r["testo_fisso"])           # numeri gia' detti: si confermano
        r = ct.turno(st, "si", INDICE, TAGLIERE)
        self.assertEqual(r["fase"], "salumi")
        prima = list(st["costruzione"]["lista"])
        self.assertEqual(len(prima), 10)
        r = ct.turno(st, "prendo 1", INDICE, TAGLIERE)                    # ne mancano 2: nuove proposte
        self.assertEqual(st["costruzione"]["scelti"]["salumi"], [prima[0]])
        self.assertTrue(set(st["costruzione"]["lista"]).isdisjoint(prima))  # mai le stesse
        self.assertIn("Perfetto, segno", r["testo_fisso"])
        self.assertIn("1. **", r["testo_fisso"])                           # lista numerata esatta
        r = ct.turno(st, "1 e 2", INDICE, TAGLIERE)
        self.assertEqual(r["fase"], "formaggi")                            # salumi completati
        self.assertEqual(len(st["costruzione"]["scelti"]["salumi"]), 3)
        self.assertIsNone(ct.turno(st, "com'e' il primo?", INDICE, TAGLIERE))  # domanda: la costruzione resta
        self.assertIsNotNone(st["costruzione"])
        r = ct.turno(st, "1 e 3", INDICE, TAGLIERE)
        self.assertEqual(r["fase"], "fatto")
        self.assertEqual(len(r["prodotti"]), 5)
        self.assertIsNone(st["costruzione"])

    def test_chiede_i_numeri_se_mancano(self):
        st = stato("fammi un tagliere", "non mi piace")
        r = ct.turno(st, "non mi piace", INDICE, TAGLIERE)
        self.assertIn("Quanti salumi e quanti formaggi", r["testo_fisso"])
        r = ct.turno(st, "2 salumi e 1 formaggio", INDICE, TAGLIERE)
        self.assertEqual(r["fase"], "salumi")

    def test_fuori_tema(self):
        self.assertIsNone(ct.turno(stato("ciao"), "non mi piace", INDICE, None))  # nessun tagliere in ballo
        st = stato("fammi un tagliere", "non mi piace")
        ct.turno(st, "non mi piace", INDICE, TAGLIERE)
        self.assertIsNone(ct.turno(st, "passiamo a un primo di mare", INDICE, TAGLIERE))
        self.assertIsNone(st["costruzione"])


if __name__ == "__main__":
    unittest.main()
