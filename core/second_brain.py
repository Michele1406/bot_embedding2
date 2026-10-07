# -*- coding: utf-8 -*-
"""
SECOND BRAIN - memoria di conoscenza (grafo leggero) di Nino.

Un grafo di relazioni costruito SOLO da dati verificati (catalogo_v2, ricettario, schede aziendali):
nessun fatto inventato dall'LLM. Serve a tre cose che la ricerca vettoriale da sola non sa fare:

  1. ABBINAMENTI  : "cosa va con questo prodotto" -> categorie che compaiono insieme nelle ricette
                    reali del ricettario, risolte in prodotti realmente a catalogo (dieta/canale/esclusioni ok).
  2. ALTERNATIVE  : stesso tipo/sottocategoria, altro fornitore (prodotto finito, esaurito, escluso dal cliente...).
  3. AZIENDE      : scheda storia/valori del fornitore dal file ABSTRACT (collezione `aziende_sofood`).

Nodi: prodotto, tipo, sottocategoria, reparto, fornitore, ricetta.   Relazioni (archi):
  prodotto -e_un-> tipo -in-> sottocategoria -in-> reparto ;  prodotto -di-> fornitore
  ricetta -usa-> sottocategoria ;  sottocategoria -abbina-> sottocategoria (peso = n. ricette in comune)

Tutto in memoria (si ricostruisce in <1 s all'avvio); nessuna dipendenza aggiuntiva.
    python -m core.second_brain        # statistiche e controllo a campione
"""
import json
import re
from collections import Counter, defaultdict

# Sottocategorie troppo generiche per esprimere un abbinamento
_SUB_GENERICHE = {"ALTRI PRODOTTI", "ALTRO", "VARIE", "", "NONE"}
_RE_SPAZI = re.compile(r"\s+")

# Parole che indicano "voglio sapere chi e' l'azienda"
_PAROLE_AZIENDA = ("storia", "chi e'", "chi è", "chi sono", "azienda", "produttore", "produce", "fondat", "famiglia",
                   "racconta", "parlami", "come lavora", "metodo di lavorazione", "valori", "conosci", "cosa sai di",
                   "presentami", "da dove viene", "dove si trova")


def _n(t) -> str:
    return _RE_SPAZI.sub(" ", str(t or "").strip().lower())


class SecondBrain:
    def __init__(self):
        self.pronto = False
        self.prodotti: dict = {}                  # id -> record indice (id, metadata, document)
        self.per_sub: dict = defaultdict(list)    # sottocategoria -> [id]
        self.per_tipo: dict = defaultdict(list)   # tipo_prodotto -> [id]
        self.per_fornitore: dict = defaultdict(list)
        self.abbina: dict = defaultdict(Counter)  # sottocategoria -> Counter(sottocategoria)
        self.ricette_per_sub: dict = defaultdict(set)
        self.schede_aziende: dict = {}            # nome fornitore normalizzato -> {nome, testo}
        self.per_formato: dict = defaultdict(list)  # (fornitore, nome senza formato) -> [id]: stesso prodotto, piu' formati
        self.regole_tipo: list = []               # abbinamenti/complementari per tipo (abbinamenti_horeca.yaml), risolti
        self.ingredienti_ricette: list = []       # (ingrediente, ruolo, nome piatto, categoria): dal prodotto al piatto
        self.n_ricette = 0

    # ------------------------------------------------------------------ costruzione
    def costruisci(self, indice_testuale: list, ricette: "list | None" = None, aziende: "list | None" = None) -> "SecondBrain":
        """indice_testuale: record {id, metadata, document}. ricette: [{id, metadata}] (ricettario).
        aziende: [(metadata, documento)] (collezione aziende_sofood)."""
        from core.retrieval_utils import trova_match_lessicale  # import tardivo: evita cicli
        self.__init__()
        for p in indice_testuale or []:
            m = p["metadata"]
            self.prodotti[p["id"]] = p
            self.per_sub[str(m.get("sottocategoria") or "")].append(p["id"])
            self.per_tipo[_n(m.get("tipo_prodotto"))].append(p["id"])
            self.per_fornitore[_n(m.get("nome_fornitore"))].append(p["id"])
            self.per_formato[chiave_formato(p)].append(p["id"])

        for r in ricette or []:
            try:
                ingredienti = json.loads(r["metadata"].get("ingredienti_json") or "[]")
            except (ValueError, TypeError):
                continue
            subs = set()
            nome_piatto = str(r["metadata"].get("nome_piatto") or "")
            for ing in ingredienti:
                nome = (ing or {}).get("INGREDIENTE_GENERICO") if isinstance(ing, dict) else None
                if not nome:
                    continue
                if nome_piatto:
                    self.ingredienti_ricette.append((_n(nome), str(ing.get("RUOLO") or "").lower(), nome_piatto,
                                                     str(r["metadata"].get("categoria") or "")))
                hit = trova_match_lessicale(str(nome), indice_testuale, max_risultati=1, max_per_fornitore=1)
                if hit:
                    sub = str(hit[0]["metadata"].get("sottocategoria") or "")
                    if _n(sub).upper() not in _SUB_GENERICHE and sub.upper() not in _SUB_GENERICHE:
                        subs.add(sub)
            if len(subs) >= 2:
                self.n_ricette += 1
            for a in subs:
                self.ricette_per_sub[a].add(r["id"])
                for b in subs:
                    if a != b:
                        self.abbina[a][b] += 1

        from core import famiglie_tagliere as ft
        for tipo, chiave in (("abbinamento", "abbinamenti_per_tipo"), ("complementare", "complementari")):
            for regola in ft._cfg().get(chiave) or []:
                con = [(sel, [p["id"] for p in self.prodotti.values() if combacia(p, sel, tutte=True)])
                       for sel in regola.get("con") or []]
                self.regole_tipo.append({"nome": regola.get("nome"), "tipo": tipo, "quando": regola.get("quando") or {},
                                         "con": [(sel, ids) for sel, ids in con if ids]})

        for meta, doc in aziende or []:
            nome = _n(meta.get("nome_fornitore"))
            if nome:
                self.schede_aziende[nome] = {"nome": meta.get("nome_fornitore"), "testo": doc}
        self.pronto = bool(self.prodotti)
        return self

    # ------------------------------------------------------------------ filtri comuni
    def _ammesso(self, p: dict, dieta=None, esclusi=None, canale=None, ammetti_ingredienti=False) -> bool:
        from core import ontologia
        from core.retrieval_utils import prodotto_compatibile_con_dieta
        m = p["metadata"]
        if p["id"] in (esclusi or ()):
            return False
        if m.get("modalita_uso") == "ingrediente" and not ammetti_ingredienti:
            return False  # non si propone come prodotto singolo (petali di tartufo, spezie...); in una ricetta si
        if str(m.get("snack_tipo") or "").startswith("olive") and (m.get("snack_tipo") != "olive_salamoia" or m.get("denocciolate")):
            return False  # regola di dominio: "olive" = intere in salamoia (le altre solo su richiesta esplicita)
        if dieta and not prodotto_compatibile_con_dieta(m, dieta):
            return False
        if ontologia.prodotto_escluso_da_cliente(m, p["document"]):
            return False
        return True

    @staticmethod
    def _chiave_canale(p: dict, canale) -> int:
        c = str(p["metadata"].get("canale_formato") or "").lower()
        return 0 if canale and c == str(canale).lower() else 1

    def _rappresentanti(self, ids, n, dieta, esclusi, canale, fornitori_usati, ammetti_ingredienti=False,
                        stesso_fornitore_ok=False) -> list:
        """Fino a n prodotti da `ids`, un fornitore ciascuno, canale giusto per primo, ordine stabile.
        Con stesso_fornitore_ok, se non bastano fornitori diversi si completa con altri prodotti degli stessi fornitori
        (tutti i Parmigiano a catalogo sono dello stesso caseificio: prima l'opzione B risultava "non disponibile")."""
        cand = [self.prodotti[i] for i in ids if self._ammesso(self.prodotti[i], dieta, esclusi, canale, ammetti_ingredienti)]
        cand.sort(key=lambda p: (self._chiave_canale(p, canale),
                                 _n(p["metadata"].get("nome_fornitore")) in fornitori_usati, p["id"]))
        out, visti = [], set(fornitori_usati)
        for p in cand:
            f = _n(p["metadata"].get("nome_fornitore"))
            if f in visti:
                continue
            visti.add(f)
            out.append(p)
            if len(out) >= n:
                break
        if stesso_fornitore_ok and len(out) < n:
            out += [p for p in cand if p not in out][:n - len(out)]
        return out

    # ------------------------------------------------------------------ interrogazioni
    def abbinamenti(self, prodotto: dict, n: int = 3, dieta=None, esclusi=None, canale=None, categorie_presenti=None) -> list:
        """Prodotti reali che 'vanno con' `prodotto` secondo le ricette del ricettario.
        Ritorna [{'prodotto': record, 'categoria': sottocategoria, 'ricette': n}], piu' frequenti prima."""
        if not self.pronto:
            return []
        from core import famiglie_tagliere as ft
        m0 = prodotto["metadata"]
        sub = str(m0.get("sottocategoria") or "")
        fam0 = ft.famiglia_prodotto(m0, prodotto.get("document", ""))
        out, usati = [], {_n(m0.get("nome_fornitore"))}
        # Ordine per "lift" (co-occorrenze / sqrt(frequenze)): senza, le categorie onnipresenti (pasta, spezie)
        # comparirebbero come abbinamento di tutto.
        na = max(len(self.ricette_per_sub.get(sub, ())), 1)
        graduatoria = sorted(
            ((c / (na * max(len(self.ricette_per_sub.get(b, ())), 1)) ** 0.5, b, c)
             for b, c in self.abbina.get(sub, Counter()).items()
             if c >= 3 and not b.upper().startswith(("SURG", "GELAT"))),
            reverse=True)
        # Prima gli abbinamenti CLASSICI della famiglia (formaggio stagionato -> confetture/mostarde/miele,
        # crudo -> taralli/grissini...: core/abbinamenti_horeca.yaml), poi quelli statistici del ricettario.
        classici = [b for b in ft.sottocategorie_classiche(fam0) if b in self.per_sub and b != sub]
        # 1) regole per tipo (tonno -> cipolla di Tropea, olive -> taralli, gelato -> cantucci...), 2) classici della
        # famiglia da tagliere, 3) statistici del ricettario (solo se nessuna regola riconosce il prodotto)
        per_tipo = [ids for r in self.regole_per(prodotto, "abbinamento") for _sel, ids in r["con"]]
        candidate = [(None, ids, 0, True) for ids in per_tipo]
        candidate += [(b, self.per_sub.get(b, []), self.abbina.get(sub, Counter()).get(b, 0), True) for b in classici]
        if not m0.get("snack_tipo") and not fam0 and not per_tipo and _e_accompagnamento(prodotto):
            # per gli snack da ciotolina le co-occorrenze del ricettario non hanno senso ("anacardi con carpaccio di
            # salmone"): solo abbinamenti classici
            candidate += [(b, self.per_sub.get(b, []), peso, False) for _l, b, peso in graduatoria[:12] if b not in classici]
        presenti = set(categorie_presenti or ())
        fam_usate = {fam0} if fam0 else set()
        for sub2, ids2, peso, classico in candidate:
            if sub2 in presenti:
                continue  # gia' nella proposta: non si "abbina" qualcosa che il cliente ha gia' davanti
            rep = self._rappresentanti([i for i in ids2 if i != prodotto["id"]], 1, dieta, esclusi, canale, set())
            if not rep:
                continue
            sub2 = sub2 or str(rep[0]["metadata"].get("sottocategoria") or "")
            if sub2 in presenti or sub2 == sub:
                continue
            if not classico and not _e_accompagnamento(rep[0]):
                continue  # statistico ma non da accompagnamento (pasta, riso, farine...): "crudo con fusilli" no
            fam2 = ft.famiglia_prodotto(rep[0]["metadata"], rep[0].get("document", ""))
            if fam2 and fam2 in fam_usate:
                continue  # niente "salame con salame": stessa famiglia del prodotto o di un altro abbinamento
            out.append({"prodotto": rep[0], "categoria": sub2, "ricette": peso, "classico": classico})
            usati.add(_n(rep[0]["metadata"].get("nome_fornitore")))
            if fam2:
                fam_usate.add(fam2)
            if len(out) >= n:
                break
        return out

    def alternative(self, prodotto: dict, n: int = 3, dieta=None, esclusi=None, canale=None,
                    ammetti_ingredienti: bool = False) -> list:
        """Stessa famiglia (tipo, poi sottocategoria): prima di altri fornitori, poi anche dello stesso fornitore
        (un Parmigiano 36 mesi e' un'ottima alternativa a un 24 mesi dello stesso caseificio).
        ammetti_ingredienti=True per le ricette (la passata e' "solo ingrediente" ma in un primo va benissimo)."""
        if not self.pronto:
            return []
        m = prodotto["metadata"]
        # l'alternativa a un ingrediente (olio, farina, aceto, passata) e' un altro ingrediente: prima ne restavano
        # senza nessuna 175 prodotti, tutti gli oli EVO compresi
        ammetti_ingredienti = ammetti_ingredienti or m.get("modalita_uso") == "ingrediente"
        esclusi = set(esclusi or ()) | {prodotto["id"]} | {x["id"] for x in self.altri_formati(prodotto)}
        usati = {_n(m.get("nome_fornitore"))}
        out = []
        for chiave_ids in (self.per_tipo.get(_n(m.get("tipo_prodotto")), []), self.per_sub.get(str(m.get("sottocategoria") or ""), [])):
            for altro_fornitore in (True, False):
                if len(out) >= n:
                    break
                gia = esclusi | {p["id"] for p in out}
                out += self._rappresentanti(chiave_ids, n - len(out), dieta, gia, canale,
                                            usati | {_n(p["metadata"].get("nome_fornitore")) for p in out} if altro_fornitore else set(),
                                            ammetti_ingredienti, stesso_fornitore_ok=not altro_fornitore)
        return out[:n]

    def scheda_azienda(self, testo_query: str, prodotti: "list | None" = None, max_char: int = 900) -> str:
        """Scheda storica del fornitore se la richiesta chiede dell'azienda e il fornitore e' nominato
        (nella domanda o tra i primi prodotti). Altrimenti stringa vuota."""
        from core.anagrafica_fornitori import ANAGRAFICA, alias_di, norm, stesso_fornitore
        q = _n(testo_query)
        if not self.schede_aziende or not any(k in q for k in _PAROLE_AZIENDA):
            return ""
        # 1) azienda nominata nella domanda (alias calcolati: "masciarelli", "italfish", "conserve gentile"...)
        nq = " " + norm(testo_query) + " "
        trovati = [k for k in self.schede_aziende if any(f" {a} " in nq for a in alias_di(self.schede_aziende[k]["nome"]))]
        if not trovati:
            for nome in ANAGRAFICA.fornitori_in_testo(testo_query):
                trovati += [k for k in self.schede_aziende if stesso_fornitore(self.schede_aziende[k]["nome"], nome)]
        # 2) altrimenti il produttore dei primi prodotti del contesto
        if not trovati:
            for p in (prodotti or [])[:5]:
                nome = str(p["metadata"].get("nome_fornitore") or "")
                trovati = [k for k in self.schede_aziende if stesso_fornitore(self.schede_aziende[k]["nome"], nome)]
                if trovati:
                    break
        if not trovati:
            return ""
        # piu' schede per lo stesso gruppo (Amodio, Conserve/Forni/Pastificio Gentile): vince quella nominata, poi la piu' ricca
        trovati.sort(key=lambda k: (norm(self.schede_aziende[k]["nome"]) not in nq, -len(self.schede_aziende[k]["testo"])))
        s = self.schede_aziende[trovati[0]]
        testo = _RE_SPAZI.sub(" ", re.sub(r"---[^-]+---", "", s["testo"])).strip()
        return f"Scheda azienda {s['nome']} (dati ufficiali): {testo[:max_char]}"

    # ------------------------------------------------------------------ contesto per il modello
    def blocco_contesto(self, record_prodotti: list, user_query: str = "", dieta=None, esclusi=None, canale=None,
                        max_principali: int = 3, con_abbinamenti: bool = True, dettaglio_prodotto: bool = False) -> str:
        """Righe da accodare al contesto RAG. Contengono SOLO prodotti realmente a catalogo.
        con_abbinamenti=False per i piatti composti (primi, secondi, pizze): il piatto e' gia' l'abbinamento e
        quelli per singolo ingrediente erano illogici (chat reale: "spaghetti allo scoglio con un tocco di pistacchio")."""
        if not self.pronto or not record_prodotti:
            return ""
        righe = []
        visti = set()
        # categorie e FAMIGLIE gia' presenti nella proposta: taralli e grissini sono la stessa cosa in un tris
        # 12: una proposta con extra (tagliere 3+3, sottoli, confettura) ha piu' di 8 prodotti (chat reale: composta
        # di cipolle suggerita accanto alla confettura di cipolle gia' in proposta)
        presenti = {str(p["metadata"].get("sottocategoria") or "") for p in record_prodotti[:12]}
        famiglie_presenti = {famiglia_abbinamento(p) for p in record_prodotti} - {""}
        for p in (record_prodotti if con_abbinamenti else []):
            if len(visti) >= max_principali:
                break
            sub = str(p["metadata"].get("sottocategoria") or "")
            if sub in visti or sub.upper() in _SUB_GENERICHE:
                continue
            visti.add(sub)
            abb = [a for a in self.abbinamenti(p, 3, dieta, esclusi, canale, presenti)
                   if famiglia_abbinamento(a["prodotto"]) not in famiglie_presenti][:2]
            for a in abb:
                q = a["prodotto"]
                linea = self.linea_produttore(q, dieta, esclusi, canale)
                righe.append(f"- Con {_nome(p)}: {'abbinamento classico' if a.get('classico') else 'abbinamento dalle ricette'} "
                             f"-> {_nome(q)} (Produttore: {nome_breve(q['metadata'].get('nome_fornitore'), q.get('document', ''))})"
                             + (f" | stessa linea del produttore: {', '.join(_nome(x) for x in linea)}" if linea else ""))
        scheda = self.scheda_azienda(user_query, record_prodotti)
        out = []
        if righe:
            out.append("[ABBINAMENTI VERIFICATI (ricettario + catalogo): se pertinente proponi UN abbinamento citando "
                       "il prodotto e il produttore; puoi accennare alla linea del produttore]")
            out += righe
        dettagli = self._righe_dettaglio(record_prodotti[:max_principali], user_query, dieta, esclusi, canale) \
            if dettaglio_prodotto else []
        if dettagli:
            out.append("[COLLEGAMENTI VERIFICATI DEL PRODOTTO: usali solo se utili alla domanda, uno o due al massimo]")
            out += dettagli
        if scheda:
            out.append("[SCHEDA AZIENDA]\n" + scheda)
        return "\n".join(out)

    def _righe_dettaglio(self, principali: list, user_query: str, dieta, esclusi, canale) -> list:
        """Per i prodotti di cui si parla: altri formati, cosa serve per prepararlo, piatti del ricettario e, se il
        cliente la chiede, un'alternativa (REPORT_COLLEGAMENTI.md: A2, A3, B2, B3)."""
        from core.parse_formato import formato_prodotto
        righe = []
        vuole_alt = bool(_RE_ALTERNATIVA.search(user_query or ""))
        for i, p in enumerate(principali):
            nome = _nome(p)
            if i < 2:  # scheda tecnica (tagli di carne, legumi, riso, farine...): core/guide_prodotto.py
                from core import guide_prodotto
                guida = guide_prodotto.riga(p, vuole_alt, self, dieta, esclusi, canale)
                if guida:
                    righe.append(f"- Guida tecnica per {nome} - {guida}")
            formati = []
            for q in self.altri_formati(p):
                f = formato_prodotto(q["metadata"], q.get("document", ""))
                if f.get("valore"):
                    v, u = f["valore"], f.get("unita") or "g"
                    txt = f"{v / 1000:g} {'kg' if u == 'g' else 'L'}" if v >= 1000 else f"{v:g} {u}"
                    if txt not in formati:
                        formati.append(txt)
            if formati:
                righe.append(f"- {nome}: stesso prodotto disponibile anche nei formati {', '.join(formati)}")
            if i == 0:
                comp = self.complementari(p, 2, dieta, esclusi, canale)
                if comp:
                    righe.append(f"- Per preparare/servire {nome} serve anche: "
                                 + "; ".join(f"{_nome(c)} (Produttore: {nome_breve(c['metadata'].get('nome_fornitore'), c.get('document', ''))})" for c in comp))
                piatti = self.piatti_con(p, 2)
                if piatti:
                    righe.append(f"- Piatti del ricettario So Food con {nome}: {'; '.join(piatti)}")
            if vuole_alt and i == 0:
                alt = self.alternative(p, 2, dieta, esclusi, canale)
                if alt:
                    righe.append(f"- Alternative verificate a {nome} (stessa tipologia): "
                                 + "; ".join(f"{_nome(a)} (Produttore: {nome_breve(a['metadata'].get('nome_fornitore'), a.get('document', ''))})" for a in alt))
        return righe

    def linea_produttore(self, prodotto: dict, dieta=None, esclusi=None, canale=None, n: int = 2) -> list:
        """Altri prodotti dello stesso produttore e della stessa sottocategoria ("Mongetto ha anche la confettura di...")."""
        m = prodotto["metadata"]
        stessi = [i for i in self.per_fornitore.get(_n(m.get("nome_fornitore")), [])
                  if i != prodotto["id"] and str(self.prodotti[i]["metadata"].get("sottocategoria")) == str(m.get("sottocategoria"))]
        cand = [self.prodotti[i] for i in stessi if self._ammesso(self.prodotti[i], dieta, esclusi, canale)]
        return sorted(cand, key=lambda q: (self._chiave_canale(q, canale), q["id"]))[:n]

    def regole_per(self, prodotto: dict, tipo: str) -> list:
        return [r for r in self.regole_tipo if r["tipo"] == tipo and combacia(prodotto, r["quando"], tutte=False)]

    def complementari(self, prodotto: dict, n: int = 2, dieta=None, esclusi=None, canale=None) -> list:
        """Cosa serve anche per preparare/servire il prodotto (pinsa -> pelati, mozzarella, olio): un prodotto per voce."""
        out = []
        for r in self.regole_per(prodotto, "complementare"):
            for _sel, ids in r["con"]:
                out += self._rappresentanti([i for i in ids if i != prodotto["id"]], 1, dieta,
                                            set(esclusi or ()) | {p["id"] for p in out}, canale, set(),
                                            ammetti_ingredienti=True)
                if len(out) >= n:
                    return out[:n]
        return out

    def piatti_con(self, prodotto: dict, n: int = 2) -> list:
        """Piatti del ricettario in cui il prodotto e' un ingrediente (bottarga -> spaghetti alla bottarga), prima quelli
        in cui e' protagonista. Si confronta il tipo del prodotto, mai il nome commerciale."""
        from core import famiglie_tagliere as ft
        base = ft.tipo_base(prodotto["metadata"], prodotto.get("document", ""))
        if len(base) < 4:
            return []
        trovati = sorted((ruolo != "protagonista", piatto) for ing, ruolo, piatto, _cat in self.ingredienti_ricette
                         if re.search(r"(?<![a-zà-ù'])" + re.escape(base), ing))  # "sott'aceto" non e' aceto
        out = []
        for _p, piatto in trovati:
            if piatto not in out:
                out.append(piatto)
            if len(out) >= n:
                break
        return out

    def altri_formati(self, prodotto: dict) -> list:
        """Lo stesso prodotto in altre pezzature (Marmellata di arance Mongetto 40 g / 230 g / 1,2 kg), dal piu' piccolo."""
        ids = [i for i in self.per_formato.get(chiave_formato(prodotto), []) if i != prodotto["id"]]
        return sorted((self.prodotti[i] for i in ids), key=lambda q: (q["metadata"].get("formato_valore") or 0, q["id"]))

    def statistiche(self) -> dict:
        return {"prodotti": len(self.prodotti), "tipi": len(self.per_tipo), "sottocategorie": len(self.per_sub),
                "fornitori": len(self.per_fornitore), "ricette_con_abbinamenti": self.n_ricette,
                "coppie_abbinamento": sum(len(c) for c in self.abbina.values()) // 2,
                "schede_aziende": len(self.schede_aziende)}


_RE_ALTERNATIVA = re.compile(r"alternativ|simile|invece|al posto|sostitu|un altr|un'altr|qualcos'altro", re.IGNORECASE)

_GRUPPI_SNACK = {"taralli": "croccanti", "pane_croccante": "croccanti", "olive_salamoia": "olive", "olive_olio": "olive",
                 "olive_altro": "olive", "frutta_secca": "frutta_secca", "patatine": "patatine", "snack_riso": "snack_riso",
                 "finger_food_caldo": "finger_food"}


from core.anagrafica_fornitori import nome_breve  # noqa: E402


def _e_accompagnamento(p: dict) -> bool:
    """Prodotto che si serve accanto ad altri (tagliere, aperitivo, antipasto), non un ingrediente di base."""
    m = p.get("metadata") or {}
    if str(m.get("sottocategoria") or "").upper().startswith(("PASTA", "RISO", "FARIN", "SEMOL", "OLIO", "ACETO", "SALE", "AROMI")):
        return False
    return bool(m.get("uso_tagliere") or m.get("uso_aperitivo") or m.get("uso_antipasto") or m.get("ingr_accompagnamento_tagliere"))


def famiglia_abbinamento(p: dict) -> str:
    """Famiglia per confrontare un abbinamento con cio' che e' gia' nella proposta: gruppo snack (taralli = grissini),
    famiglia da tagliere (crudo, erborinato...), altrimenti stringa vuota (si confronta la sottocategoria)."""
    m = p.get("metadata") or {}
    st = str(m.get("snack_tipo") or "")
    if st:
        return "snack:" + _GRUPPI_SNACK.get(st, st)
    from core import famiglie_tagliere as ft
    f = ft.famiglia_prodotto(m, p.get("document", ""))
    if f:
        return f"tagliere:{f}"
    # confetture, marmellate, mostarde, composte e miele fanno la stessa cosa sul tagliere: una basta
    testo = (str(m.get("tipo_prodotto") or "") + " " + (p.get("document") or "").split("\n")[0]).lower()
    return "dolce_da_formaggi" if any(k in testo for k in ("confettur", "marmellat", "mostard", "compost", "miele")) else ""


_RE_FORMATO = re.compile(r"\b\d+([.,]\d+)?\s*(kg|gr|g|ml|cl|lt|l|pz|pezzi)\b|\b(kg|gr)\s*\d+([.,]\d+)?\b|\bx\s*\d+\b")
_PAROLE_CONFEZIONE = {"vaso", "vasetto", "vetro", "bottiglia", "bott", "bottquadra", "quadra", "satin", "busta", "secchio",
                      "latta", "sottovuoto", "conf", "confezione", "vaschetta", "pz", "bag", "box", "in", "da", "formato"}


def combacia(p: dict, sel: dict, tutte: bool) -> bool:
    """Il prodotto rispetta il selettore (abbinamenti_horeca.yaml). reparti/escludi sono sempre vincoli; parole,
    sottocategorie e snack_tipo: basta una (tutte=False, per "quando") o servono tutte (tutte=True, per "con")."""
    m = p.get("metadata") or {}
    testo = (str(m.get("tipo_prodotto") or "") + " " + (p.get("document") or "").split("\n")[0]).lower()
    if sel.get("reparti") and str(m.get("reparto") or "").upper() not in sel["reparti"]:
        return False
    if any(e in testo for e in sel.get("escludi") or []):
        return False
    prove = []
    if sel.get("parole"):
        prove.append(any(w in testo for w in sel["parole"]))
    if sel.get("sottocategorie"):
        prove.append(str(m.get("sottocategoria") or "") in sel["sottocategorie"])
    if sel.get("snack_tipo"):
        prove.append(str(m.get("snack_tipo") or "") in sel["snack_tipo"])
    if sel.get("famiglie"):
        from core import famiglie_tagliere as ft
        prove.append(ft.famiglia_prodotto(m, p.get("document", "")) in sel["famiglie"])
    if not prove:
        return bool(sel.get("reparti"))
    return all(prove) if (tutte or sel.get("tutte")) else any(prove)


def chiave_formato(p: dict) -> tuple:
    """(produttore, nome senza peso e confezione): due prodotti con la stessa chiave sono lo stesso prodotto in formati diversi."""
    from core.testo_prodotto import nome_senza_produttore
    m = p.get("metadata") or {}
    nome = nome_senza_produttore(p.get("document", ""), str(m.get("nome_fornitore") or "")).lower()
    nome = _RE_FORMATO.sub(" ", nome)
    # i numeri che non sono formati restano: "Parmigiano 24 mesi" e "36 mesi" sono prodotti diversi
    parole = [w for w in re.findall(r"[a-zàèéìòù]+|[0-9]+", nome) if w not in _PAROLE_CONFEZIONE]
    return (_n(m.get("nome_fornitore")), " ".join(parole))


def _nome(p: dict) -> str:
    from core.testo_prodotto import nome_senza_produttore
    from core.contesto_prodotti import pulisci_nome_commerciale
    # stesso nome pulito del resto del contesto (chat reale: "COMPOSTA DI CIPOLLE DI MONTORO 250GR")
    nome = nome_senza_produttore(p["document"], str(p["metadata"].get("nome_fornitore") or ""))
    return (pulisci_nome_commerciale(nome) if nome else "") or str(p["metadata"].get("tipo_prodotto") or "")


BRAIN = SecondBrain()


def costruisci_da_db(client_db, indice_testuale: list, nome_ricette: str = "ricette_v2",
                     nome_aziende: str = "aziende_sofood") -> SecondBrain:
    """Costruisce il singleton BRAIN dalle collezioni Chroma. Errori sulle collezioni opzionali non bloccano l'avvio."""
    ricette, aziende = [], []
    try:
        d = client_db.get_collection(nome_ricette).get(include=["metadatas"])
        ricette = [{"id": i, "metadata": m} for i, m in zip(d["ids"], d["metadatas"])]
    except Exception as e:
        print(f"[SECOND BRAIN] ricettario non disponibile: {e}")
    try:
        d = client_db.get_collection(nome_aziende).get(include=["metadatas", "documents"])
        aziende = list(zip(d["metadatas"], d["documents"]))
    except Exception as e:
        print(f"[SECOND BRAIN] schede aziende non disponibili: {e}")
    BRAIN.costruisci(indice_testuale, ricette, aziende)
    print(f"[SECOND BRAIN] {BRAIN.statistiche()}")
    return BRAIN


if __name__ == "__main__":
    import sys
    import chromadb
    from core.retrieval_utils import costruisci_indice_testuale
    db = chromadb.PersistentClient("database_vettoriale")
    idx = costruisci_indice_testuale(db.get_collection("catalogo_v2"))
    b = costruisci_da_db(db, idx)
    for q in ("taralli", "prosciutto crudo", "mozzarella", "salmone affumicato"):
        from core.retrieval_utils import trova_match_lessicale
        h = trova_match_lessicale(q, idx, 1)
        if h:
            print("\n", q, "->", _nome(h[0]))
            for a in b.abbinamenti(h[0], 3):
                print("   abbina:", a["categoria"], "|", _nome(a["prodotto"]), a["ricette"])
            for a in b.alternative(h[0], 2):
                print("   alt   :", _nome(a))
    sys.exit(0)
