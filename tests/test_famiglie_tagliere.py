# -*- coding: utf-8 -*-
"""Varieta' del tagliere: niente due salami, due pecorini, due crudi (core/famiglie_tagliere.py, docs/STUDIO_ABBINAMENTI.md)."""
import unittest

from core import famiglie_tagliere as ft


def P(pid, rep, tipo, nome, **kw):
    m = {"reparto": rep, "tipo_prodotto": tipo, "sottocategoria": kw.pop("sc", "X"), "nome_fornitore": kw.pop("forn", pid), **kw}
    return {"id": pid, "metadata": m, "document": f"PRODOTTO: {nome}\nDESCRIZIONE: ..."}


class TestFamiglie(unittest.TestCase):
    def test_salumi(self):
        casi = {"salame toscano": "insaccato", "finocchiona": "insaccato", "prosciutto crudo": "crudo",
                "prosciutto cotto": "cotto", "culatta cotta": "cotto", "mortadella": "cotto", "coppa stagionata": "muscolo_stagionato",
                "capocollo": "muscolo_stagionato", "bresaola": "manzo", "lardo stagionato": "grasso", "speck": "crudo"}
        for tipo, fam in casi.items():
            self.assertEqual(ft.famiglia_prodotto({"reparto": "SALUMI", "tipo_prodotto": tipo}, ""), fam, tipo)

    def test_formaggi_e_latte(self):
        self.assertEqual(ft.famiglia_prodotto({"reparto": "FORMAGGI", "tipo_prodotto": "parmigiano reggiano"}, ""), "duro")
        self.assertEqual(ft.famiglia_prodotto({"reparto": "FORMAGGI", "tipo_prodotto": "gorgonzola"}, ""), "erborinato")
        # erborinato aromatizzato resta erborinato; pecorino al pistacchio e' aromatizzato
        self.assertEqual(ft.famiglia_prodotto({"reparto": "FORMAGGI", "tipo_prodotto": "formaggio erborinato"},
                                              "PRODOTTO: Erborinato affinato ai mirti"), "erborinato")
        self.assertEqual(ft.famiglia_prodotto({"reparto": "FORMAGGI", "tipo_prodotto": "pecorino al pistacchio"}, ""), "aromatizzato")
        self.assertEqual(ft.latte({"reparto": "FORMAGGI", "tipo_prodotto": "pecorino"}, ""), "ovino")
        self.assertEqual(ft.latte({"reparto": "FORMAGGI", "tipo_prodotto": "formaggio di capra"}, ""), "caprino")

    def test_doppioni(self):
        due_salami = [P("a", "SALUMI", "salame", "Salame toscano"), P("b", "SALUMI", "finocchiona", "Finocchiona IGP")]
        self.assertTrue(ft.doppioni(due_salami))
        due_pecorini = [P("a", "FORMAGGI", "pecorino stagionato", "Pecorino riserva"), P("b", "FORMAGGI", "pecorino", "Pecorino classico")]
        self.assertTrue(any("stesso tipo" in m for m, _ in ft.doppioni(due_pecorini)))
        crudo_e_cotto = [P("a", "SALUMI", "prosciutto crudo", "Prosciutto di Parma"), P("b", "SALUMI", "prosciutto cotto", "Prosciutto cotto")]
        self.assertEqual(ft.doppioni(crudo_e_cotto), [])

    def test_scelta_varia(self):
        from core.ricettario import _scegli_vari
        pool = [P("s1", "SALUMI", "salame", "Salame 1"), P("s2", "SALUMI", "salame toscano", "Salame 2"),
                P("s3", "SALUMI", "finocchiona", "Finocchiona"), P("c1", "SALUMI", "prosciutto crudo", "Crudo"),
                P("m1", "SALUMI", "coppa", "Coppa")]
        scelti = _scegli_vari(pool, 3, "salumi", "tagliere misto", cap_forn=3, forzata=False)
        fam = [ft.famiglia_prodotto(p["metadata"], p["document"]) for p in scelti]
        self.assertEqual(len(set(fam)), 3, fam)  # crudo + insaccato + muscolo, non tre salami
        self.assertEqual(fam[0], "crudo")
        # piu' prodotti che famiglie: si ripete la famiglia
        self.assertEqual(len(_scegli_vari(pool, 5, "salumi", "", cap_forn=5, forzata=False)), 5)

    def test_formaggi_latti_diversi_e_niente_due_pecorini(self):
        from core.ricettario import _scegli_vari
        pool = [P("f1", "FORMAGGI", "pecorino stagionato", "Pecorino riserva"), P("f2", "FORMAGGI", "pecorino", "Pecorino giovane"),
                P("f3", "FORMAGGI", "parmigiano reggiano", "Parmigiano 24 mesi"), P("f4", "FORMAGGI", "caciotta", "Caciotta"),
                P("f5", "FORMAGGI", "gorgonzola", "Gorgonzola")]
        scelti = _scegli_vari(pool, 3, "formaggi", "", cap_forn=3, forzata=False)
        tipi = [p["metadata"]["tipo_prodotto"] for p in scelti]
        self.assertLessEqual(sum("pecorino" in t for t in tipi), 1, tipi)

    def test_abbinamenti_classici_e_niente_stessa_famiglia(self):
        from core.second_brain import SecondBrain
        crudo = P("c", "SALUMI", "prosciutto crudo", "Prosciutto di Parma", sc="PROSC CRUDO", forn="A")
        salame = P("s", "SALUMI", "salame", "Salame", sc="SALAME", forn="B")
        crudo2 = P("c2", "SALUMI", "prosciutto crudo", "San Daniele", sc="SALUMI INTERI", forn="C")
        taralli = {"id": "t", "metadata": {"reparto": "DISPENSA", "sottocategoria": "TARALLI", "nome_fornitore": "D", "tipo_prodotto": "taralli"},
                   "document": "PRODOTTO: Taralli"}
        b = SecondBrain().costruisci([crudo, salame, crudo2, taralli])
        b.abbina["PROSC CRUDO"].update({"SALUMI INTERI": 9, "SALAME": 5})
        b.ricette_per_sub.update({"PROSC CRUDO": {1, 2, 3}, "SALUMI INTERI": {1, 2}, "SALAME": {1}})
        abb = b.abbinamenti(crudo, 3)
        self.assertEqual(abb[0]["categoria"], "TARALLI")  # classico prima dello statistico
        self.assertTrue(abb[0]["classico"])
        self.assertNotIn("c2", [a["prodotto"]["id"] for a in abb])  # un altro crudo non e' un abbinamento


if __name__ == "__main__":
    unittest.main()
