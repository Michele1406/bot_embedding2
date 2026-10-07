# -*- coding: utf-8 -*-
"""
ontologia.py
============
Applica core/ontologia_horeca.yaml: trasforma il testo libero di uno slot
("olive da tavola", "anacardi o nocciole") o di una richiesta in un FILTRO
sugli attributi del catalogo_v2, e sceglie i prodotti che lo rispettano.

Perche' esiste: la ricerca vettoriale per "olive da tavola" restituiva un patè di
olive o olive sott'olio. Con le regole sugli attributi (snack_tipo, denocciolate)
un risultato sbagliato non puo' uscire per costruzione, e il motivo di ogni
scelta e' tracciabile.
"""

import os
import re

import yaml

from core.testo_prodotto import nome_prodotto

_PERCORSO = os.path.join(os.path.dirname(__file__), "ontologia_horeca.yaml")
_CACHE: dict = {}


def _carica() -> dict:
    if "dati" not in _CACHE:
        with open(_PERCORSO, encoding="utf-8") as f:
            _CACHE["dati"] = yaml.safe_load(f) or {}
    return _CACHE["dati"]


def ricarica() -> None:
    _CACHE.clear()


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower().replace("’", "'").replace("`", "'")).strip()


def _contiene_parola(testo: str, parola: str) -> bool:
    """Match su parola intera (o inizio di parola per le radici tipo 'denocciolat')."""
    return re.search(r"(?<![a-zà-ù])" + re.escape(parola), testo) is not None


# parole della richiesta che non aiutano a ordinare i prodotti
_PAROLE_NON_RANK = {"topping", "finitura", "guarnizione", "vorrei", "dammi", "dimmi", "fammi", "antipasto", "antipasti",
                    "aperitivo", "ristorante", "tagliere", "tagliere", "locale", "siamo", "avete", "qualche", "alcuni",
                    "consigli", "proponi", "cosa", "come", "senza", "della", "delle", "sotto", "olio"}


_RE_SEPARATORI = re.compile(r"[,;.!?]| e | ed | con | poi | oppure | o ")


def risolvi_slot(nome_ingrediente: str, query_utente: str = "") -> "dict | None":
    """Spec di ricerca per uno slot, o None se nessuna famiglia lo riconosce.

    Ritorna {"famiglia", "snack_tipo": [..], "denocciolate": bool|None, "motivo": str}.
    Le parole ESPLICITE del cliente (query_utente) possono cambiare il filtro di default.
    """
    ing = _norm(nome_ingrediente)
    q = _norm(query_utente)
    for nome, fam in (_carica().get("slot_ingredienti") or {}).items():
        if not any(_contiene_parola(ing, p) for p in fam.get("riconosci", [])):
            continue
        if any(_norm(e) in ing for e in fam.get("escludi_testo", [])):
            continue
        filtro = dict(fam.get("filtro") or {})
        motivo = f"famiglia '{nome}' (default)"
        # la parola del cliente cambia il filtro solo se riferita a questa famiglia: nello slot o nello stesso pezzo di
        # frase ("olive sott'olio"), non in "aggiungi sottoli, taralli e olive" (chat reale: olive condite sott'olio)
        pezzi = [x for x in _RE_SEPARATORI.split(q) if any(_contiene_parola(x, _norm(r)) for r in fam.get("riconosci", []))] or [q]
        for ov in fam.get("override_query") or []:
            if any(_contiene_parola(x, _norm(p)) for p in ov.get("parole", []) for x in pezzi):
                filtro = dict(ov.get("filtro") or {})
                motivo = f"famiglia '{nome}' con richiesta esplicita del cliente ({ov['parole'][0]})"
                break
        extra = {k: v for k, v in filtro.items() if k not in ("snack_tipo", "denocciolate")}
        # parole dello slot che NON appartengono alla famiglia (es. "carciofi" in "carciofi sott'olio")
        testo_famiglia = " ".join(_norm(p) for p in fam.get("riconosci", []))
        specifiche = sorted({w[:6] for w in re.findall(r"[a-zà-ù]+", ing)
                             if len(w) >= 5 and w not in testo_famiglia and w[:5] not in testo_famiglia})
        return {
            "famiglia": nome,
            "snack_tipo": list(filtro.get("snack_tipo") or []),
            "denocciolate": filtro.get("denocciolate"),
            "filtro_extra": extra,
            "parole_specifiche": specifiche if fam.get("richiedi_parole_specifiche") else [],
            "parole_rank": sorted({w[:6] for w in re.findall(r"[a-zà-ù]+", ing + " " + q)
                                   if len(w) >= 5 and w not in _PAROLE_NON_RANK}),
            "inietta_da_query": bool(fam.get("inietta_da_query", False)),
            "preferisci_parole": [_norm(x) for x in fam.get("preferisci_parole", [])],
            "penalizza_parole": [_norm(x) for x in fam.get("penalizza_parole", [])],
            "formato_ideale": fam.get("formato_ideale"),
            "dieta_accetta_dedotto": bool(fam.get("dieta_accetta_dedotto", False)),
            "ammetti_solo_ingredienti": bool(fam.get("ammetti_solo_ingredienti", False)),
            "motivo": motivo,
        }
    return None


def indice_ha_attributi(indice_testuale) -> bool:
    """True se l'indice proviene da catalogo_v2 (ha gli attributi derivati)."""
    for p in (indice_testuale or [])[:50]:
        if "snack_tipo" in p["metadata"]:
            return True
    return False


def prodotto_rispetta(meta: dict, spec: dict) -> bool:
    if spec["snack_tipo"] and meta.get("snack_tipo") not in spec["snack_tipo"]:
        return False
    for chiave, atteso in (spec.get("filtro_extra") or {}).items():
        if meta.get(chiave) != atteso:
            return False
    if spec.get("denocciolate") is not None and bool(meta.get("denocciolate")) != bool(spec["denocciolate"]):
        return False
    return True


def scegli(spec: dict, indice_testuale: list, canale: "str | None" = None, esclusi: "set | None" = None,
           dieta: "str | None" = None, n: int = 1, fornitori_usati: "set | None" = None) -> list:
    """Prodotti che rispettano `spec`, in ordine di preferenza:
    1) non gia' proposti, 2) ordine delle famiglie nel filtro (es. taralli prima dei grissini),
    3) formato adatto al canale (horeca: grande; retail: piccolo), 4) un prodotto per
    produttore prima di ripeterne uno, 5) id (deterministico).
    Ogni record porta `motivo_scelta` per l'audit."""
    from core.parse_formato import formato_prodotto
    from core.retrieval_utils import prodotto_compatibile_con_dieta

    esclusi = esclusi or set()
    cand = []
    for p in indice_testuale:
        meta = p["metadata"]
        if not prodotto_rispetta(meta, spec):
            continue
        if prodotto_escluso_da_cliente(meta, p["document"]):
            continue
        if meta.get("modalita_uso") == "ingrediente" and not spec.get("ammetti_solo_ingredienti"):
            continue  # petali di tartufo, spezie, granelle... non si propongono come prodotto singolo
        if dieta:
            ok = prodotto_compatibile_con_dieta(meta, dieta)
            if (not ok and spec.get("dieta_accetta_dedotto") and dieta == "vegano"
                    and str(meta.get("vegano", "")).upper() == "SI*"):
                # opt-in per famiglia (YAML): prodotti con flag vegano dedotto (SI*) ma ingredienti
                # banali (olive, acqua, sale): la regola globale resta "solo SI pieno".
                ok = prodotto_compatibile_con_dieta({**meta, "vegano": "SI"}, dieta)
            if not ok:
                continue
        cand.append(p)

    def valore_canale(p):
        c = p["metadata"].get("canale_formato") or formato_prodotto(p["metadata"], p["document"])["canale_formato"]
        if (canale or "").lower() in ("horeca", "retail"):
            if c == canale.lower():
                return 0
            return 1 if c in ("misto", "sconosciuto") else 2
        return 0

    if spec.get("parole_specifiche"):
        # lo slot nomina un prodotto preciso ("carciofi sott'olio"): niente ripiego su altri ortaggi
        cand = [p for p in cand if any(w in _norm(nome_prodotto(p["document"]) + " " + str(p["metadata"].get("tipo_prodotto") or ""))
                                       for w in spec["parole_specifiche"])]

    ordine_tipo = {t: i for i, t in enumerate(spec["snack_tipo"])}

    def testo_p(p):
        return _norm(nome_prodotto(p["document"]) + " " + str(p["metadata"].get("tipo_prodotto") or ""))

    def fuori_formato(p):
        ideale = spec.get("formato_ideale")
        v = p["metadata"].get("formato_valore")
        if not ideale or v is None:
            return 0
        return 0 if ideale[0] <= v <= ideale[1] else 1

    def n_specifiche(p):
        rank = spec.get("parole_rank") or []
        if not rank:
            return 0
        t = _norm(nome_prodotto(p["document"]) + " " + str(p["metadata"].get("tipo_prodotto") or ""))
        return -sum(1 for w in rank if w in t)

    cand.sort(key=lambda p: (
        p["id"] in esclusi,
        ordine_tipo.get(p["metadata"].get("snack_tipo"), 99),   # "taralli o grissini": prima i taralli
        bool(p["metadata"].get("denocciolate")) if spec.get("denocciolate") is None else False,  # non chieste: prima con nocciolo
        n_specifiche(p),
        str(p["metadata"].get("nome_fornitore") or "") in (fornitori_usati or ()),  # varieta' tra slot della stessa ricetta
        any(w in testo_p(p) for w in spec.get("penalizza_parole", [])),
        not any(w in testo_p(p) for w in spec.get("preferisci_parole", [])),
        fuori_formato(p),
        valore_canale(p),
        p["id"],
    ))
    scelti, forn_visti = [], set()
    # primo giro: un prodotto per produttore; poi si completa
    for giro in (0, 1):
        for p in cand:
            if len(scelti) >= n:
                break
            if p in scelti:
                continue
            f = str(p["metadata"].get("nome_fornitore") or "")
            if giro == 0 and f in forn_visti:
                continue
            forn_visti.add(f)
            scelti.append(p)
    out = []
    for p in scelti:
        r = {"id": p["id"], "metadata": p["metadata"], "document": p["document"],
             "motivo_scelta": f"{spec['motivo']}; snack_tipo={p['metadata'].get('snack_tipo')}"}
        out.append(r)
    return out


def filtra_olive_in_risultati(query: str, risultati: list) -> list:
    """Nella ricerca libera ("olive per il mio bar"): applica la regola 'olive = salamoia,
    intere' ai soli prodotti-oliva dei risultati, salvo richiesta esplicita del cliente."""
    if not re.search(r"\boliv", _norm(query)):
        return risultati
    spec = risolvi_slot("olive da tavola", query)
    if not spec:
        return risultati
    out = []
    for r in risultati:
        st = r["metadata"].get("snack_tipo", "")
        if str(st).startswith("olive") and not prodotto_rispetta(r["metadata"], spec):
            continue
        # "olive pastellate" (surgelato da friggere) non e' una oliva da tavola
        if st == "finger_food_caldo" and not re.search(r"pastell|fritt|finger|calde?i?\b", _norm(query)):
            continue
        out.append(r)
    return out


def template_preferiti(richiesta: str, tipo_locale: "str | None", dieta: "str | None" = None) -> list:
    """Id dei template del ricettario da provare PER PRIMI (es. tris + bar -> TRIS_BAR_01...)."""
    q = _norm(richiesta)
    loc = _norm(tipo_locale or "")
    d = (dieta or "").lower() or ("vegano" if "vegan" in q else "")
    for regola in _carica().get("template_preferiti") or []:
        if not any(_contiene_parola(q, _norm(p)) for p in regola.get("quando_parole", [])):
            continue
        if regola.get("quando_locale") and not any(_contiene_parola(loc, _norm(x)) for x in regola["quando_locale"]):
            continue
        if regola.get("quando_dieta") and d not in regola["quando_dieta"]:
            continue
        if d and d in (regola.get("non_se_dieta") or []):
            continue
        if any(_contiene_parola(q, _norm(x)) for x in regola.get("non_se_parole", [])):
            continue
        return list(regola.get("preferiti", []))
    return []


def spec_olive_da_query(query: str) -> "dict | None":
    """Spec 'olive' se la query nomina le olive (default salamoia, intere; override se esplicito)."""
    if not re.search(r"\boliv", _norm(query)):
        return None
    return risolvi_slot("olive da tavola", query)


# ----------------------------------------------------------------------
# Modalita' d'uso: prodotto singolo / solo ingrediente / entrambi
# ----------------------------------------------------------------------
_MARCATORI_INGREDIENTE = (
    "ingredient", "topping", "finitur", "guarnir", "guarnizion", "decorar", "decorazion", "spolver", "aromatizz",
    "per condire", "condimento", "per cucinare", "farcire", "farcitura", "impast", "spezie", "spezia", "aromi",
    "per preparare", "da cucina", "base per",
)


def _parole_significative(t: str) -> set:
    return {w[:6] for w in re.findall(r"[a-zà-ù]+", _norm(t)) if len(w) >= 4}


def query_cerca_ingredienti(query: str) -> bool:
    q = _norm(query)
    return any(m in q for m in _MARCATORI_INGREDIENTE)


def escludi_solo_ingredienti(query: str, risultati: list) -> list:
    """Nelle proposte generiche (es. "dimmi 3 antipasti", "cosa mi consigli per l'aperitivo") i prodotti
    che sono SOLO ingredienti (petali di tartufo, spezie, granelle, farine...) non si propongono da soli.
    Restano se il cliente li nomina ("avete i petali di tartufo?") o chiede esplicitamente ingredienti/topping."""
    if query_cerca_ingredienti(query):
        return risultati
    parole_q = _parole_significative(query)
    out = []
    for r in risultati:
        m = r["metadata"]
        if m.get("modalita_uso") == "ingrediente":
            nome = _parole_significative(nome_prodotto(r["document"]) + " " + str(m.get("tipo_prodotto") or ""))
            if not (parole_q & nome) and not (r.get("match_esatto") or r.get("match_fornitore")):
                continue
        out.append(r)
    return out


def descrizione_uso(meta: dict) -> str:
    """Riga per il contesto del modello: come puo' essere proposto il prodotto."""
    mu = meta.get("modalita_uso")
    usi = [u for u in str(meta.get("usi_ingr") or "").split("|") if u]
    elenco = ", ".join(u.replace("_", " ") for u in usi)
    if mu == "ingrediente":
        return "Modalita d'uso: SOLO INGREDIENTE (non proporlo come prodotto singolo; si usa come " + (elenco or "ingrediente") + ")"
    if mu == "entrambi":
        return "Modalita d'uso: prodotto singolo E ingrediente" + (f" (come ingrediente: {elenco})" if elenco else "")
    return ""


# ----------------------------------------------------------------------
# Esclusioni espresse dal cliente ("niente tonno", "non voglio il prosciutto cotto"):
# vincolo PERSISTENTE per tutta la sessione, applicato a ogni ricerca.
# ----------------------------------------------------------------------
import contextvars

ESCLUSIONI_CORRENTI: "contextvars.ContextVar[list]" = contextvars.ContextVar("esclusioni_cliente", default=[])


def prodotto_escluso_da_cliente(meta: dict, documento: str, esclusioni: "list | None" = None) -> bool:
    """True se il NOME/TIPO del prodotto contiene una parola che il cliente ha escluso.
    Si guarda solo nome, tipo e sottocategoria (non tutta la scheda): "niente tonno" non deve togliere
    una senape che nella descrizione dice "ottima con il tonno"."""
    from core import logistica, allergeni  # import tardivo: evita cicli
    if logistica.non_consegnabile(meta, documento):
        return True  # cliente fuori zona refrigerata: solo temperatura ambiente
    if allergeni.ALLERGIE_CORRENTI.get() and allergeni.rischio(meta, documento, allergeni.ALLERGIE_CORRENTI.get()):
        return True  # allergia dichiarata dal cliente (contiene, tracce o allergeni non dichiarati)
    esclusioni = ESCLUSIONI_CORRENTI.get() if esclusioni is None else esclusioni
    if not esclusioni:
        return False
    testo = _norm(nome_prodotto(documento) + " " + str(meta.get("tipo_prodotto") or "") + " "
                  + str(meta.get("sottocategoria") or "") + " " + str(meta.get("specifiche_liv4") or ""))
    for e in esclusioni:
        e = _norm(e)
        if len(e) >= 3 and re.search(r"(?<![a-zà-ù])" + re.escape(e), testo):
            return True
    return False


def spec_da_query(query: str) -> "dict | None":
    """Prima famiglia con `inietta_da_query` nominata esplicitamente nella richiesta del cliente
    (olive, taralli, patatine, frutta secca, topping, finger food...). Il retrieval vettoriale e'
    debole su questi termini generici: i prodotti giusti vengono messi in testa direttamente."""
    q = _norm(query)
    for nome, fam in (_carica().get("slot_ingredienti") or {}).items():
        if not fam.get("inietta_da_query"):
            continue
        if not any(_contiene_parola(q, _norm(p)) for p in fam.get("riconosci", [])):
            continue
        if any(_norm(e) in q for e in fam.get("escludi_testo", [])):
            continue
        return risolvi_slot(" ".join(fam.get("riconosci", [])[:1]), query) if nome == "olive_da_tavola" else risolvi_slot(q, query)
    return None


_RE_ELENCO = re.compile(r"\b(lista|elenc\w*|tutt[ie] (i|le|gli)|che \w+( \w+)? (avete|hai|tenete)|quali \w+( \w+)? (avete|hai|tenete)|"
                        r"cosa avete|che tipi|quali tipi|assortimento)\b", re.IGNORECASE)


def vuole_elenco(query: str) -> bool:
    """Il cliente chiede l'elenco di una famiglia ("mi fai una lista dei finger food", "che olive avete?")."""
    return bool(_RE_ELENCO.search(query or ""))


_RUOLI_QUERY = {
    "aperitivo": ["aperitiv", "stuzzichin", "spritz"],
    "tagliere": ["tagliere", "taglieri"],
    "dolce": ["dessert", "dolce ", "dolci "],
    "colazione": ["colazione", "brioche"],
    "panino_fast_food": ["panino", "panini", "hamburger", "burger", "hot dog"],
}


def ruolo_da_query(query: str) -> "str | None":
    """Ruolo d'uso (aperitivo, tagliere...) nominato nella richiesta: permette di filtrare PRIMA
    del ranking sui prodotti che hanno davvero quel ruolo (attributo uso_<ruolo> di catalogo_v2)."""
    q = _norm(query) + " "
    for ruolo, parole in _RUOLI_QUERY.items():
        if any(p in q for p in parole):
            return ruolo
    return None


def famiglia_nominata(nome: str, query: str) -> bool:
    """La richiesta nomina la famiglia (una delle sue parole `riconosci`)."""
    fam = (_carica().get("slot_ingredienti") or {}).get(nome) or {}
    q = _norm(query)
    return any(_contiene_parola(q, _norm(p)) for p in fam.get("riconosci", []))


def spec_famiglia(nome: str, query: str = "") -> "dict | None":
    """Spec di una famiglia dell'ontologia per NOME (es. 'taralli_o_grissini'), con gli override del cliente."""
    fam = (_carica().get("slot_ingredienti") or {}).get(nome)
    if not fam or not fam.get("riconosci"):
        return None
    return risolvi_slot(fam["riconosci"][0], query)


# Famiglie di una "mix da aperitivo/ciotoline" generica (richiesta "aperitivo" senza altro dettaglio)
FAMIGLIE_APERITIVO = ["olive_da_tavola", "taralli_o_grissini", "frutta_secca", "patatine", "finger_food_caldo"]


def descrizione_conservazione(meta: dict) -> str:
    """Per le olive: conservazione e nocciolo, scritti nel contesto cosi' il modello non puo' descriverle
    male (E2E reale: olive sott'olio presentate come "in salamoia")."""
    st = str(meta.get("snack_tipo") or "")
    if not st.startswith("olive"):
        return ""
    cons = {"salamoia": "in salamoia", "olio": "SOTT'OLIO", "candite": "candite", "secche": "essiccate"}.get(
        str(meta.get("conservazione") or ""), "conservazione non indicata")
    nocciolo = "denocciolate" if meta.get("denocciolate") else "intere con nocciolo"
    return f"Olive: {cons}, {nocciolo}"


def righe_extra_contesto(meta: dict) -> list:
    """Righe aggiuntive (modalita' d'uso, conservazione) per il contesto del modello."""
    from core import logistica, allergeni
    return ([r for r in (descrizione_uso(meta), descrizione_conservazione(meta), descrizione_vegano_dedotto(meta),
                         allergeni.riga_contesto(meta)) if r]
            + logistica.righe_logistica(meta))


def descrizione_vegano_dedotto(meta: dict) -> str:
    """Flag vegano "SI*" (dedotto) accettato perche' gli ingredienti sono tutti vegetali: lo si dice nel contesto."""
    if str(meta.get("vegano") or "").upper() == "SI*" and meta.get("vegano_ingredienti_ok"):
        return "Vegano: dedotto dagli ingredienti (tutti vegetali, nessuna traccia di latte/uova/pesce)"
    return ""
