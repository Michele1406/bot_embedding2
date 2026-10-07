# -*- coding: utf-8 -*-
"""Test del second brain (core/second_brain.py) su un mini-catalogo sintetico."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import second_brain


def prod(id_, nome, sub, tipo, forn, **meta):
    m = {"reparto": "DISPENSA", "sottocategoria": sub, "tipo_prodotto": tipo, "nome_fornitore": forn,
         "modalita_uso": "singolo", "vegano": "NO", "canale_formato": "horeca", "uso_aperitivo": True}
    m.update(meta)
    doc = f"PRODOTTO: {nome}\nINGREDIENTI: x"
    return {"id": id_, "metadata": m, "document": doc, "titolo_lower": nome.lower(), "sottocat_lower": f"{sub} {tipo}".lower()}


TARALLI = prod("t1", "Taralli pugliesi", "SPECIALITA CROCCANTI", "taralli", "Forno A")
OLIVE = prod("o1", "Olive baresane", "OLIVE", "olive", "Oliva B", snack_tipo="olive_salamoia", denocciolate=False)
OLIVE_OLIO = prod("o2", "Olive sott'olio", "OLIVE", "olive", "Oliva C", snack_tipo="olive_olio")
PECORINO1 = prod("p1", "Pecorino stagionato", "FORMAGGI", "pecorino", "Casa C")
PECORINO2 = prod("p2", "Pecorino fresco", "FORMAGGI", "pecorino", "Casa D")
PETALI = prod("x1", "Petali di tartufo", "OLIVE", "petali", "Tartufi E", modalita_uso="ingrediente")


def ricetta(i, ingredienti):
    return {"id": i, "metadata": {"ingredienti_json": json.dumps([{"INGREDIENTE_GENERICO": x} for x in ingredienti])}}


RICETTE = [ricetta(f"r{i}", ["taralli pugliesi", "olive baresane", "pecorino stagionato"]) for i in range(4)]
AZIENDE = [({"nome_fornitore": "Forno A"}, "--- STORIA ---\n[IDENTITA]\nForno storico di Bari dal 1900.\n")]


class TestSecondBrain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = second_brain.SecondBrain().costruisci([TARALLI, OLIVE, OLIVE_OLIO, PECORINO1, PECORINO2, PETALI], RICETTE, AZIENDE)

    def test_abbinamenti_da_ricettario(self):
        subs = {a["categoria"] for a in self.b.abbinamenti(TARALLI, 5)}
        self.assertEqual(subs, {"OLIVE", "FORMAGGI"})

    def test_olive_solo_salamoia_e_mai_solo_ingredienti(self):
        for a in self.b.abbinamenti(TARALLI, 5):
            self.assertNotIn(a["prodotto"]["id"], ("o2", "x1"))

    def test_dieta_ed_esclusi(self):
        self.assertEqual([a["categoria"] for a in self.b.abbinamenti(TARALLI, 5, dieta="vegano")], [])
        ids = [a["prodotto"]["id"] for a in self.b.abbinamenti(TARALLI, 5, esclusi={"o1"})]
        self.assertNotIn("o1", ids)

    def test_alternative_stesso_tipo_altro_fornitore(self):
        self.assertEqual([p["id"] for p in self.b.alternative(PECORINO1, 3)], ["p2"])

    def test_scheda_azienda_solo_se_richiesta(self):
        self.assertIn("Bari", self.b.scheda_azienda("raccontami la storia di Forno A"))
        self.assertEqual(self.b.scheda_azienda("quali taralli avete?", [TARALLI]), "")
        self.assertIn("Bari", self.b.scheda_azienda("chi e' questo produttore?", [TARALLI]))

    def test_blocco_contesto(self):
        blocco = self.b.blocco_contesto([TARALLI], "taralli per aperitivo")
        self.assertIn("ABBINAMENTI VERIFICATI", blocco)
        self.assertIn("Olive Baresane", blocco)
        self.assertEqual(second_brain.SecondBrain().blocco_contesto([TARALLI]), "")

    def test_non_riabbina_categorie_gia_presenti(self):
        subs = {a["categoria"] for a in self.b.abbinamenti(TARALLI, 5, categorie_presenti={"OLIVE"})}
        self.assertEqual(subs, {"FORMAGGI"})
        blocco = self.b.blocco_contesto([TARALLI, OLIVE], "tris")
        self.assertNotIn("Olive baresane", blocco.split("si abbina:")[-1] if "si abbina:" in blocco else "")

    def test_vuoto_non_esplode(self):
        v = second_brain.SecondBrain().costruisci([], [], [])
        self.assertFalse(v.pronto)
        self.assertEqual(v.abbinamenti(TARALLI), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
