# -*- coding: utf-8 -*-
"""Debug 2026-10-06: formato di pasta giusto per il piatto; panoramica per le domande d'insieme."""
import unittest

from core import formati_pasta as fp
from core import panoramica


def P(i, nome, sub="PASTA DI SEMOLA", forn="Pastificio A", **m):
    meta = {"sottocategoria": sub, "nome_fornitore": forn, "reparto": "DISPENSA", "tipo_prodotto": nome.lower()}
    meta.update(m)
    return {"id": i, "metadata": meta, "document": f"PRODOTTO: {nome}"}


INDICE = [P("s1", "Spaghettini 500g"), P("s2", "Spaghetti 12 Min. 500g"), P("s3", "Spaghetti Integrale Bio"),
          P("r1", "Tortiglioni 500g"), P("r2", "Rigatoni 500g"), P("t1", "Tagliatelle Tartufo", "PASTA ALL'UOVO", "B"),
          P("t2", "Tagliatelle 500g"), P("x1", "Preparato aromatico per patate", "AROMI E SPEZIE"),
          P("g1", "Sugo pronto per spaghetti", "SUGHI PRONTI E BASI")]


class TestFormatiPasta(unittest.TestCase):
    def test_formato_esatto(self):
        self.assertEqual(fp.scegli("spaghetti trafilati al bronzo", "", INDICE)[0]["id"], "s2")   # non spaghettini
        self.assertEqual(fp.scegli("rigatoni", "", INDICE)[0]["id"], "r2")                       # non tortiglioni
        self.assertEqual(fp.scegli("tagliatelle", "", INDICE)[0]["id"], "t2")                    # senza tartufo
        self.assertEqual(fp.scegli("spaghetti", "li voglio integrali", INDICE)[0]["id"], "s3")   # chiesti: si'

    def test_il_cliente_sceglie_il_formato(self):
        self.assertEqual(fp.scegli("spaghetti di grano duro", "la carbonara con i rigatoni", INDICE)[0]["id"], "r2")
        self.assertIn("carbonara", fp.senza_formati("fammi una carbonara con i rigatoni"))
        self.assertNotIn("rigatoni", fp.senza_formati("fammi una carbonara con i rigatoni"))

    def test_mancante(self):
        self.assertEqual(fp.scegli("gnocchi di patate", "", INDICE), (None, False))  # mai il preparato per patate
        self.assertEqual(fp.scegli("pasta ripiena", "", INDICE), (None, False))
        prod, esatto = fp.scegli("linguine", "", INDICE)                               # vicino dichiarato
        self.assertFalse(esatto)
        self.assertEqual(prod["id"], "s2")
        self.assertIsNone(fp.scegli("pomodoro", "", INDICE))                           # non e' pasta

    def test_sugo_non_e_pasta(self):
        self.assertFalse(fp.e_pasta(INDICE[-1]))


class TestIngredientePerNome(unittest.TestCase):
    """Le uova Scudellaro stanno in "Carne > Altre carni": la carbonara (che le cerca in Dispensa) diceva che non c'erano."""
    def test_parola_principale(self):
        from core.ricettario import _per_parola_principale
        uova = P("u", "Scudellaro - Uova Biologiche Categoria A", "ALTRE CARNI", "Scudellaro", reparto="CARNE")
        indice = [uova, P("o", "Orecchiette Fresche Bio")]
        self.assertEqual([r["id"] for r in _per_parola_principale("uova fresche", indice)], ["u"])   # non le orecchiette fresche
        self.assertEqual([r["id"] for r in _per_parola_principale("tuorli d'uovo freschi", indice)], ["u"])
        self.assertEqual(_per_parola_principale("crema di uova e pecorino", indice), [])           # preparazione dello chef


class TestPanoramica(unittest.TestCase):
    def test_domande_d_insieme(self):
        for q in ("che prodotti avete di Mongetto?", "che pasta avete?", "avete olio extravergine?", "mi fai l'elenco"):
            self.assertTrue(panoramica.e_panoramica(q), q)
        self.assertFalse(panoramica.e_panoramica("fammi una carbonara"))

    def test_quadro_per_produttore(self):
        record = INDICE[:3]  # la ricerca ne ha trovati 3, il catalogo ne ha di piu'
        b = panoramica.blocco("che pasta avete?", record, INDICE)
        self.assertIn("PANORAMICA DAL CATALOGO", b)
        self.assertIn("Pastificio A", b)
        self.assertTrue(panoramica.rappresentanti("che pasta avete?", record, INDICE))


if __name__ == "__main__":
    unittest.main()
