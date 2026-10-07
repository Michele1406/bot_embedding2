# -*- coding: utf-8 -*-
"""Guardrail su formati e canale dichiarati nel testo (guardrail_output.verifica_formati)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import guardrail_output as g


def prod(id_, nome, forn, peso, **meta):
    m = {"reparto": "DISPENSA", "sottocategoria": "X", "tipo_prodotto": nome.lower(), "nome_fornitore": forn}
    m.update(meta)
    doc = f"PRODOTTO: {forn} - {nome}\nPESO/FORMATO: {peso}\nINGREDIENTI: x"
    return {"id": id_, "metadata": m, "document": doc, "titolo_lower": nome.lower(), "sottocat_lower": ""}


MOUSSE = prod("m1", "Mousse di Tartufo Nero", "Casa Tartufi", "Vasetto da 300 g")
RISO = prod("r1", "Riso Carnaroli Superfino", "Riseria Bianca", "Sacco da 5 kg", formato_variante_liv5="1 kg, 5 kg")
INDICE = [MOUSSE, RISO]


class TestFormati(unittest.TestCase):
    def test_horeca_su_retail(self):
        t = "* **Mousse di Tartufo Nero** (Casa Tartufi): disponibile in cartoni HORECA."
        r = g.verifica_formati(t, INDICE)
        self.assertEqual(len(r), 1)
        self.assertIn("300 g", r[0][2])

    def test_quantita_sbagliata(self):
        t = "* **Mousse di Tartufo Nero**: confezione da 1 kg per il tuo locale."
        r = g.verifica_formati(t, INDICE)
        self.assertEqual(len(r), 1)

    def test_quantita_corretta_e_varianti_ok(self):
        self.assertEqual(g.verifica_formati("* **Mousse di Tartufo Nero**: vasetto da 300 g, formato retail.", INDICE), [])
        self.assertEqual(g.verifica_formati("* **Riso Carnaroli Superfino**: sacco da 5 kg, ideale HORECA, anche da 1 kg.", INDICE), [])

    def test_retail_su_horeca(self):
        r = g.verifica_formati("* **Riso Carnaroli Superfino**: pezzatura retail.", INDICE)
        self.assertEqual(len(r), 1)

    def test_nome_in_grassetto_con_peso_non_conta(self):
        self.assertEqual(g.verifica_formati("* **Mousse di Tartufo Nero 300g**: ottima.", INDICE), [])

    def test_porzione_a_persona_non_e_il_formato(self):
        t = "* **Riso Carnaroli Superfino**: servono circa 80 grammi a persona; il sacco e' da 5 kg."
        self.assertEqual(g.verifica_formati(t, INDICE), [])

    def test_prodotto_ignoto_o_ambiguo_non_si_tocca(self):
        self.assertEqual(g.verifica_formati("* **Pasta Fantasma**: cartoni HORECA da 9 kg.", INDICE), [])

    def test_nota(self):
        self.assertEqual(g.nota_formati([]), "")
        self.assertIn("Precisazione", g.nota_formati([("r", "c", "msg")]))


class TestProduttori(unittest.TestCase):
    def test_inesistente(self):
        r = g.verifica_produttori("Ti propongo arancini prodotti da Arancileria Siciliana, molto croccanti.", INDICE)
        self.assertEqual([c for _, c in r], ["Arancileria Siciliana"])

    def test_esistente_anche_parziale(self):
        self.assertEqual(g.verifica_produttori("Una mousse firmata Casa Tartufi.", INDICE), [])
        self.assertEqual(g.verifica_produttori("Riso prodotto da Riseria Bianca.", INDICE), [])

    def test_frasi_generiche(self):
        self.assertEqual(g.verifica_produttori("Tutto prodotto da So Food in Puglia, della casa nostra.", INDICE), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
