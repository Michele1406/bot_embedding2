"""
Vincoli del cliente sulla ricerca: dieta, contesto del turno (ContextVar), salumi di terra/mare.
(Estratto da retrieval_utils.py: i nomi restano importabili anche da li').
"""
from core.domain_rules import check_board_violations, prodotto_appartiene_a_famiglia, INCOMPATIBILITY_MATRIX
from core.testo_prodotto import prima_riga, nome_senza_produttore
from core.parse_formato import formato_prodotto
from core import logistica, ontologia

import os
import re
import json
from core.fornitori_config import (
    FORNITORI,
    TEMI_REGIONALI,
    elenco_fornitori_per_ruolo_regione,
    match_fornitore,
)
from core.profilazione_locale import (
    rileva_canale_locale,
    rileva_formato_prodotto,
    punteggio_coerenza_canale,
)
from core.tassonomia_sofood import classifica_terra_mare


# ====================================================================
# DIETA, SALUMI DI TERRA/DI MARE, CONTESTO DIETA CORRENTE
# ====================================================================
import contextvars

# Dieta attiva per l'intera composizione in corso (tagliere/menu): la leggono
# tutte le ricerche annidate, cosi' un vincolo "vegano" non si perde passando
# da una funzione all'altra (test reale R6: tagliere vegano con salumi).
_DIETA_CORRENTE: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("dieta_corrente", default=None)
# Canale del cliente (horeca/retail) per la composizione in corso: serve alle regole dell'ontologia
_CANALE_CORRENTE: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("canale_corrente", default=None)
# Messaggio ORIGINALE del cliente: `richiesta_cliente` arriva dall'analisi LLM gia' espansa con parole chiave
# ("aperitivo salumi formaggi ..."), che non va usata per capire cosa il cliente ha chiesto ESPLICITAMENTE
# (es. "sott'olio", "denocciolate") ne' per scegliere il template (E2E reale: "aperitivo" -> tagliere).
_QUERY_ORIGINALE: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("query_originale", default=None)


def prodotto_compatibile_con_dieta(meta: dict, dieta: "str | None") -> bool:
    """Unica fonte di verita' sulla compatibilita' prodotto/dieta: reparti e
    sottocategorie escluse dal YAML + flag del catalogo. Per 'vegetariano'
    accetta SI e SI* (dedotto); per le altre diete usa il flag assoluto
    richiesto da regole_cliente.yaml (es. vegano -> SI pieno)."""
    if not dieta:
        return True
    from core.config_manager import get_regole_dieta
    d = dieta.lower()
    regole = get_regole_dieta(d) or {}
    rep = str(meta.get("reparto", "")).upper()
    sc = str(meta.get("sottocategoria", "")).upper()
    if rep in [x.upper() for x in regole.get("esclude_reparti", [])]:
        return False
    if sc in [x.upper() for x in regole.get("esclude_sottocategorie", [])]:
        return False
    if d not in ("vegano", "vegetariano", "senza_glutine", "senza_lattosio"):
        return True
    valore = str(meta.get(d, "")).strip().upper()
    if d == "vegetariano":
        return valore in ("SI", "SI*")
    assoluto = str(regole.get("richiede_flag_assoluto", "")).strip().upper()
    if not assoluto:
        return True
    if valore == assoluto:
        return True
    # Opzione di config (default spenta): flag dedotto SI* accettato se gli ingredienti sono verificati vegetali
    return bool(d == "vegano" and valore == "SI*" and regole.get("accetta_dedotto_se_ingredienti_vegetali")
                and meta.get("vegano_ingredienti_ok"))


def _clausole_dieta_chroma(dieta: "str | None") -> list:
    """Stesse regole di prodotto_compatibile_con_dieta, ma come clausole `where`
    di ChromaDB, applicate PRIMA del ranking (altrimenti il top-N vettoriale
    si riempie di carne/pesce, i filtri a valle lo svuotano e il bot dice che
    non c'e' nulla: test reale R8)."""
    if not dieta:
        return []
    from core.config_manager import get_regole_dieta
    d = dieta.lower()
    regole = get_regole_dieta(d) or {}
    clausole = []
    rep = [x.upper() for x in regole.get("esclude_reparti", [])]
    if rep:
        clausole.append({"reparto": {"$nin": rep}})
    if d == "vegetariano":
        clausole.append({"vegetariano": {"$in": ["SI", "SI*"]}})
    elif d in ("vegano", "senza_glutine", "senza_lattosio"):
        assoluto = str(regole.get("richiede_flag_assoluto", "")).strip().upper()
        if assoluto:
            if d == "vegano" and regole.get("accetta_dedotto_se_ingredienti_vegetali"):
                clausole.append({"$or": [{d: assoluto}, {"vegano_ingredienti_ok": True}]})
            else:
                clausole.append({d: assoluto})
    return clausole


_TERMINI_SALUMI_TERRA = (
    "salum", "salame", "salami", "affettat", "culatello", "prosciutt", "bresaola", "mortadell",
    "speck", "pancett", "guanciale", "coppa", "soppressat", "finocchiona", "nduja", "tagliere", "taglieri",
)
_TERMINI_MARE = (
    "mare", "pesc", "ittic", "tonno", "salmone", "polpo", "spada", "gamber", "alici", "acciug", "baccal",
    "bottarga", "cernia", "ricciola", "calamar", "seppia", "totan", "caviale", "crostace", "mollusch",
)


def _query_vuole_salumi_di_terra(query: str) -> bool:
    """True se la query parla di salumi/affettati/tagliere SENZA citare il mare.
    Nel catalogo un fornitore di pesce ha ~40 prodotti con nomi da salume
    ("prosciutto di tonno", "mortadella di mare"): senza questa distinzione
    "salumi" restituisce solo pesce (test reale R3)."""
    q = (query or "").lower()
    return any(t in q for t in _TERMINI_SALUMI_TERRA) and not any(t in q for t in _TERMINI_MARE)


_PAROLE_NEGAZIONE = ("senza", "privo", "prive", "assente", "assenti", "0%", "zero", "no ")


def _termine_presente_non_negato(testo_lower: str, termine: str) -> bool:
    """Come 'termine in testo_lower', ma ignora i casi in cui il termine è
    preceduto da una negazione a breve distanza (es. 'senza carne', '0%
    lattosio'), per evitare di escludere per errore prodotti che dichiarano
    esplicitamente l'ASSENZA di un ingrediente invece della sua presenza."""
    for m in re.finditer(re.escape(termine), testo_lower):
        contesto_prima = testo_lower[max(0, m.start() - 25):m.start()]
        if any(neg in contesto_prima for neg in _PAROLE_NEGAZIONE):
            continue
        return True
    return False


def _template_ok_dieta(tag_dieta, dieta: "str | None") -> bool:
    if not dieta or dieta.lower() not in ("vegano", "vegetariano"):
        return True
    return dieta.lower() in str(tag_dieta or "").lower()
