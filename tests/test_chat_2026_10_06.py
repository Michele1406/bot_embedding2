# -*- coding: utf-8 -*-
"""Regressione sulla chat reale del 2026-10-06 (REPORT_CHAT_2026-10-06.md): ogni problema ha il suo test, sulla
regola generale e non sul singolo prodotto."""
import os
import unittest

from core import foto
from core.formato_testo import pulisci_markdown
from core.guardrail_output import estrai_citazioni
from core.contesto_prodotti import pulisci_nome_commerciale
from core.anagrafica_fornitori import nome_breve


class TestTesto(unittest.TestCase):
    def test_grassetti_su_righe_diverse_non_si_fondono(self):  # P2 / punto 5
        t = "1. **Taralli Caserecci**\n**2. Olive**\n- **Olive Taggiasche**"
        self.assertEqual(pulisci_markdown(t), t)
        self.assertEqual(pulisci_markdown("Ecco ** ** il tris"), "Ecco  il tris")

    def test_etichette_e_markdown_rotto_non_sono_prodotti(self):  # P3 / punto 5
        t = ("- **Temperatura**: refrigerato\n- **Descrizione**: morbida\n"
             "1. **Taralli Caserecci2. Sfiziosita' / Olive**\n- **Pancetta Cotta Dello Zio**: morbida")
        self.assertEqual([c for _r, c in estrai_citazioni(t)], ["Pancetta Cotta Dello Zio"])

    def test_nomi_puliti(self):  # P9
        self.assertEqual(pulisci_nome_commerciale("PRODOTTO: La Casera - Blu di Bufala 3kg (Mezza Forma)", "LA CASERA"), "Blu di Bufala")
        self.assertEqual(pulisci_nome_commerciale("PRODOTTO: La Casera - Taleggio DOP 2kg (Forma Intera)", "LA CASERA"), "Taleggio DOP")

    def test_nome_breve_del_gruppo(self):  # punto 1: "Amodio | Conserve Gentile | ..." illeggibile
        g = "Amodio | Conserve Gentile | Forni Gentile | Pastificio Gentile"
        self.assertEqual(nome_breve(g, "PRODOTTO: Composta di cipolle\nPRODUTTORE: Conserve Gentile srl"), "Conserve Gentile")
        self.assertEqual(nome_breve("Mongetto", "x"), "Mongetto")


class TestFoto(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        self.tmp.close()
        self.per_id = {"P": {"id": "P", "metadata": {"ha_immagine_primaria": True, "percorso_immagine": self.tmp.name}},
                       "N": {"id": "N", "metadata": {"ha_immagine_primaria": False}}}

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_quando_mostrarla(self):
        certi = [("Pancetta Cotta Dello Zio", "P")]
        self.assertEqual(foto.scegli("mi fai vedere questa pancetta?", certi, self.per_id), ["P"])   # la chiede
        self.assertEqual(foto.scegli("non sono sicuro, com'e'?", certi, self.per_id), ["P"])        # e' incerto
        self.assertEqual(foto.scegli("che formato ha?", certi, self.per_id, info_su_prodotto=True), ["P"])
        self.assertEqual(foto.scegli("ok grazie", certi, self.per_id), [])                         # non serve
        molti = [(f"Prodotto {i}", "P" if i == 0 else f"X{i}") for i in range(5)]
        self.assertEqual(foto.scegli("dammi dei salumi", molti, self.per_id, info_su_prodotto=True), [])

    def test_solo_se_certa(self):
        self.assertEqual(foto.scegli("hai una foto?", [("Prodotto senza foto", "N")], self.per_id), [])
        self.assertEqual(foto.scegli("hai una foto?", [], self.per_id), [])   # prodotto non identificato: mai

    def test_inserita_sotto_il_prodotto(self):
        t = foto.inserisci("Ecco la **Pancetta Cotta Dello Zio**: morbida.\nAltro.", [("Pancetta Cotta Dello Zio", "P")], ["P"], self.per_id)
        self.assertEqual(t.split("\n")[1], f"[IMG: {self.tmp.name}]")

    def test_anafora(self):
        for q in ("hai un'immagine?", "mi fai vedere questa pancetta cotta dello zio di cui parli", "cos'e' quello?"):
            self.assertTrue(foto.anaforico(q), q)
        self.assertFalse(foto.anaforico("fammi un tagliere toscano per 6 persone con salumi e formaggi"))


class TestRicettarioEAbbinamenti(unittest.TestCase):
    def test_forma_diversa(self):  # punto 2: crema di scampi al posto dei gamberi
        from core.ricettario import forma_diversa
        P = lambda nome, tipo="": {"metadata": {"tipo_prodotto": tipo}, "document": f"PRODOTTO: {nome}"}
        self.assertTrue(forma_diversa("gamberi o crostacei", P("Crema di scampi", "crema di scampi")))
        self.assertTrue(forma_diversa("frutti di mare misti", P("Sughetto di mare", "sugo di mare")))
        self.assertFalse(forma_diversa("sugo di pomodoro", P("Sugo di datterini", "sugo")))
        self.assertFalse(forma_diversa("gamberi", P("Gamberi rossi", "gamberi")))

    def test_famiglia_abbinamento_snack(self):  # punto/P6: grissini a un tris che ha gia' i taralli
        from core.second_brain import famiglia_abbinamento
        self.assertEqual(famiglia_abbinamento({"metadata": {"snack_tipo": "taralli"}}),
                         famiglia_abbinamento({"metadata": {"snack_tipo": "pane_croccante"}}))

    def test_opzione_b_dello_stesso_produttore(self):  # punto 4: "Opzione B non disponibile"
        from core.second_brain import SecondBrain
        prodotti = [{"id": f"pr{i}", "metadata": {"tipo_prodotto": "parmigiano reggiano", "sottocategoria": "GRANA E SIMILI",
                                                  "nome_fornitore": "Montanari & Gruzza", "reparto": "FORMAGGI"},
                     "document": f"PRODOTTO: Parmigiano {24 + 6 * i} mesi"} for i in range(3)]
        b = SecondBrain().costruisci(prodotti)
        self.assertEqual(len(b.alternative(prodotti[0], 2)), 2)  # prima: [] perche' tutti dello stesso caseificio
        passata = {"id": "ps", "metadata": {"tipo_prodotto": "passata di pomodoro", "sottocategoria": "PASSATA",
                                            "modalita_uso": "ingrediente", "nome_fornitore": "A"}, "document": "PRODOTTO: Passata"}
        passata2 = dict(passata, id="ps2", metadata=dict(passata["metadata"], nome_fornitore="B"))
        b2 = SecondBrain().costruisci([passata, passata2])
        # l'alternativa a un ingrediente e' un ingrediente (REPORT_COLLEGAMENTI A2: 175 prodotti senza alternative)
        self.assertEqual(len(b2.alternative(passata, 1)), 1)
        # ma un prodotto che si serve da solo non riceve un "solo ingrediente" come alternativa
        sugo = dict(passata, id="sg", metadata=dict(passata["metadata"], modalita_uso="singolo", nome_fornitore="C"))
        b3 = SecondBrain().costruisci([sugo, passata])
        self.assertEqual(b3.alternative(sugo, 1), [])


class TestChatAperitivo(unittest.TestCase):
    """Chat reale "idee per un aperitivo": lista di finger food con sugo di tonno e tartare, nomi in maiuscolo."""
    def test_richiesta_di_elenco(self):
        from core.ontologia import vuole_elenco
        for q in ("mi fai una lista dei finger food che avete", "che olive avete?", "quali taralli avete", "elencami i fritti"):
            self.assertTrue(vuole_elenco(q), q)
        for q in ("e qualcosa da sgranocchiare, e del finger food?", "fammi un tris per il bar"):
            self.assertFalse(vuole_elenco(q), q)

    def test_nome_abbinamento_pulito(self):
        from core.second_brain import _nome
        p = {"document": "PRODOTTO: COMPOSTA DI CIPOLLE DI MONTORO 250GR", "metadata": {"nome_fornitore": "Gentile"}}
        self.assertEqual(_nome(p), "Composta di Cipolle di Montoro")


class TestExtraTagliere(unittest.TestCase):
    """Chat reale: "tagliere 3 e 3, un paio di sottoli e una marmellatina piccola" -> sottoli spariti e
    "marmellatina" dichiarata non a catalogo."""
    def test_extra_con_quantita(self):
        from core.ricettario import extra_richiesti
        e = extra_richiesti("fammi un tagliere di salumi e formaggi , 3 e tre, e aggiungimi anche un paio di sottoli e una marmellatina piccola")
        self.assertEqual([(r, n, pic) for r, n, _p, pic in e], [("sottoli", 2, False), ("mostarde_confetture", 1, True)])
        self.assertIn("confettur", e[1][2])
        self.assertEqual(extra_richiesti("tagliere con 3 confetture"), [("mostarde_confetture", 3, ["confettur", "marmellat"], False)])
        self.assertEqual(extra_richiesti("fammi un tagliere toscano"), [])
        # il numero dei formaggi non e' quello dei sottoli (chat reale: 5 sottoli)
        e = extra_richiesti("dimmi un tagliere di salumi e formaggi 4 e 5; aggiungi sottoli, taralli e olive")
        self.assertEqual(e[0][:2], ("sottoli", 2))

    def test_sottoli_non_cambia_le_olive(self):  # "aggiungi sottoli, taralli e olive" -> olive in salamoia
        from core import ontologia
        self.assertEqual(ontologia.risolvi_slot("olive da tavola", "aggiungi sottoli, taralli e olive")["snack_tipo"], ["olive_salamoia"])
        self.assertEqual(ontologia.risolvi_slot("olive da tavola", "olive sott'olio per il bar")["snack_tipo"], ["olive_olio"])

    def test_capperi_non_sono_un_extra(self):
        from core import famiglie_tagliere as ft
        self.assertIn("capperi", ft._cfg().get("condimenti_non_da_tagliere"))

    def test_diminutivi_non_sono_assenti(self):
        from core.copertura_richiesta import termini_senza_riscontro
        indice = [{"id": "1", "metadata": {}, "document": "PRODOTTO: Marmellata di arance"},
                  {"id": "2", "metadata": {}, "document": "PRODOTTO: Olive baresane"}]
        self.assertEqual(termini_senza_riscontro("una marmellatina e delle olivette", indice), [])
        self.assertEqual(termini_senza_riscontro("voglio della bottarga", indice), ["bottarga"])


class TestTagliereRegionale(unittest.TestCase):
    """"Tagliere pugliese": prima la regione, le tipologie che la regione non ha (il crudo) dal resto del catalogo,
    dichiarate come non regionali. "Soli salumi pugliesi": solo la regione."""
    def _scegli(self, richiesta):
        from unittest import mock
        from core import ricettario
        from core.vincoli_dieta import _QUERY_ORIGINALE
        S = lambda i, nome, forn: {"id": i, "metadata": {"reparto": "SALUMI", "nome_fornitore": forn, "tipo_prodotto": nome.lower()},
                                   "document": f"PRODOTTO: {nome}"}
        indice = [S("cap", "Capocollo di Martina Franca", "Salumi Martina Franca"),
                  S("sal", "Salame pugliese", "Salumi Martina Franca"),
                  S("pro", "Prosciutto crudo Coratino", "Altro Salumificio")]
        tok = _QUERY_ORIGINALE.set(richiesta)
        try:
            with mock.patch.object(ricettario, "cerca_prodotti", return_value=[]):
                return ricettario._seleziona_componente_tagliere("salumi", "puglia", None, {}, None, {}, indice,
                                                                 n_target=3, query_utente=richiesta)
        finally:
            _QUERY_ORIGINALE.reset(tok)

    def test_generale_diversifica(self):
        sel = self._scegli("fammi un tagliere pugliese")
        self.assertEqual({r["id"] for r in sel}, {"cap", "sal", "pro"})
        self.assertEqual([r["id"] for r in sel if r.get("fuori_regione")], ["pro"])

    def test_solo_regionali(self):
        self.assertEqual({r["id"] for r in self._scegli("tagliere di soli salumi pugliesi")}, {"cap", "sal"})


try:
    import app
except Exception:
    app = None


@unittest.skipIf(app is None, "app non importabile")
class TestProposteAttive(unittest.TestCase):
    def test_messaggi_sulla_proposta(self):  # P1 / punto 4: "facciamolo insieme" dopo un tris
        tris = {"categoria": "aperitivo", "nome_piatto": "Tris da Bar Classico per Aperitivo"}
        for q in ("facciamolo insieme", "no intendo il tris", "metti l'opzione B delle olive", "fai tu"):
            self.assertTrue(app.messaggio_su_proposta_attiva(q, tris), q)
        for q in ("fammi un primo di mare", "costruiamo insieme un tagliere", "che taralli avete?"):
            self.assertFalse(app.messaggio_su_proposta_attiva(q, tris), q)

    def test_alternativa_non_e_lo_stesso_prodotto(self):  # "Finocchiona IGP Gigante" -> "Finocchiona IGP"
        F = lambda nome, forn: {"metadata": {"nome_fornitore": forn}, "document": f"PRODOTTO: {nome}"}
        self.assertTrue(app._stesso_prodotto(F("Finocchiona IGP Gigante", "FRANCHI"), F("Finocchiona IGP", "FRANCHI")))
        self.assertFalse(app._stesso_prodotto(F("Finocchiona IGP Gigante", "FRANCHI"), F("Finocchiona IGP", "ALTRO")))
        self.assertFalse(app._stesso_prodotto(F("Salame Felino", "X"), F("Salame Napoli", "X")))

    def test_ricostruzione_dagli_id(self):
        p = app.indice_testuale[0]
        piatto = {"id_ricetta": "T1", "nome_piatto": "Tris", "categoria": "aperitivo",
                  "slot": [{"ingrediente_richiesto": "olive", "esito": "TROVATO", "ruolo": "protagonista", "prodotto_id": p["id"]},
                           {"ingrediente_richiesto": "x", "esito": "TROVATO", "ruolo": "secondario", "prodotto_id": "inesistente"}]}
        c = app.ricostruisci_proposta(piatto)
        self.assertEqual(c["slot"][0]["prodotto_trovato"]["id"], p["id"])
        self.assertEqual(c["slot"][1]["esito"], "NON_TROVATO")


if __name__ == "__main__":
    unittest.main()
