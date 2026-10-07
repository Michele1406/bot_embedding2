# -*- coding: utf-8 -*-
"""
Allergie del cliente (vincolo di SICUREZZA, persistente come la dieta).

Il catalogo ha i campi `allergeni` (es. "Glutine, Latte", "Frutta a guscio", "Nessuno rilevato", "Non specificato")
e `tracce_di` (es. "frutta a guscio", "soia e tracce di senape", "Nessuna"). Prima un "sono allergico alle noci"
diventava al massimo una parola esclusa dal NOME del prodotto.

  categorie_da_testo(testo)        -> {"frutta_a_guscio", ...} dalle parole del cliente ("noci", "glutine", "celiaco"...)
  rischio(meta, doc, allergie)     -> None se il prodotto e' sicuro, altrimenti il motivo ("contiene latte",
                                      "puo' contenere tracce di soia", "allergeni non dichiarati")
  ALLERGIE_CORRENTI                ContextVar per turno (come ESCLUSIONI_CORRENTI); usata da ontologia.prodotto_escluso_da_cliente

Regola prudente: con un'allergia dichiarata si escludono anche i prodotti con allergeni "Non specificato" (dato
mancante) e quelli con TRACCE dell'allergene. Il modello ricorda comunque di verificare l'etichetta.
"""
import contextvars
import re
import unicodedata

ALLERGIE_CORRENTI: "contextvars.ContextVar[list]" = contextvars.ContextVar("allergie_cliente", default=[])

# 14 allergeni del Reg. UE 1169/2011: categoria -> parole che la indicano (nel testo del cliente e nei campi del catalogo)
CATEGORIE = {
    "glutine": ["glutine", "celiac", "frumento", "grano", "orzo", "segale", "farro", "kamut", "avena"],
    "crostacei": ["crostace", "gamber", "scampi", "aragost", "astice", "granchi"],
    "uova": ["uova", "uovo", "albume", "tuorlo", "lisozima"],
    "pesce": ["pesce", "pesci", "acciug", "alici", "tonno", "merluzz", "salmon"],
    "arachidi": ["arachid", "noccioline"],
    "soia": ["soia", "soya"],
    "latte": ["latte", "lattosio", "latticin", "caseina", "siero di latte", "derivati del latte"],
    "frutta_a_guscio": ["frutta a guscio", "frutta secca", "noci", "noce", "nocciol", "mandorl", "pistacchi", "anacard",
                        "pecan", "macadamia", "anacardi"],
    "sedano": ["sedano"],
    "senape": ["senape"],
    "sesamo": ["sesamo"],
    "solfiti": ["solfiti", "anidride solforosa", "solforosa"],
    "lupini": ["lupin"],
    "molluschi": ["mollusch", "cozze", "vongole", "polpo", "calamar", "seppi", "ostriche"],
}
_NESSUNO = ("nessuno rilevato", "nessuna", "nessuno", "assenti", "non contiene")
_NON_DICHIARATO = ("non specificato", "non disponibile", "n/d", "")
# "nocciolo" (olive, frutta) non e' frutta a guscio; "latte di cocco" non e' latte animale
_FALSI = {"frutta_a_guscio": ("nocciolo", "noccioli", "aculei o gusci", "noce moscata"), "latte": ("latte di cocco", "latte di mandorla",
                                                                               "latte di soia", "latte di riso")}


def _norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _contiene(testo: str, categoria: str) -> bool:
    t = _norm(testo)
    for falso in _FALSI.get(categoria, ()):
        t = t.replace(falso, " ")
    return any(re.search(r"(?<![a-z])" + re.escape(p), t) for p in CATEGORIE[categoria])


def categorie_da_testo(testo: str) -> set:
    """Categorie di allergene nominate dal cliente ("allergico alle noci e al sesamo" -> frutta_a_guscio, sesamo)."""
    return {c for c in CATEGORIE if _contiene(testo, c)}


def etichetta(categoria: str) -> str:
    return categoria.replace("_", " ")


def rischio(meta: dict, doc: str = "", allergie: "list | set | None" = None) -> "str | None":
    allergie = [a for a in (allergie or []) if a in CATEGORIE]
    if not allergie:
        return None
    dich = _norm(meta.get("allergeni"))
    tracce = _norm(meta.get("tracce_di"))
    for a in allergie:
        if dich.strip() not in _NESSUNO and _contiene(dich, a):
            return f"contiene {etichetta(a)}"
        if tracce.strip() not in _NESSUNO and _contiene(tracce, a):
            return f"puo' contenere tracce di {etichetta(a)}"
    if dich.strip() in _NON_DICHIARATO:
        return "allergeni non dichiarati in scheda"
    # rete di sicurezza: ingredienti e frasi "puo' contenere" nel testo della scheda
    from core.attributi_derivati import sezione_ingredienti
    ingr = sezione_ingredienti(doc)
    tracce_doc = " ".join(re.findall(r"(?:puo|può)\s+contenere[^.\n]*|tracce\s+di[^.\n]*", _norm(doc)))
    for a in allergie:
        if ingr and _contiene(ingr, a):
            return f"ingredienti con {etichetta(a)}"
        if tracce_doc and _contiene(tracce_doc, a):
            return f"puo' contenere tracce di {etichetta(a)}"
    return None


def riga_contesto(meta: dict) -> str:
    """Riga 'Allergeni: ...' per il contesto del modello (solo se il dato e' informativo)."""
    dich = str(meta.get("allergeni") or "").strip()
    tracce = str(meta.get("tracce_di") or "").strip()
    if not dich or _norm(dich) in _NON_DICHIARATO:
        return "Allergeni: non dichiarati in scheda"
    parti = [f"Allergeni: {dich}"]
    if tracce and _norm(tracce) not in _NESSUNO:
        parti.append(f"puo' contenere tracce di {tracce.lstrip(': ')}")
    return " | ".join(parti)
