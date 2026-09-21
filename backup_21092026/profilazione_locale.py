# -*- coding: utf-8 -*-
"""
profilazione_locale.py
=======================
Risolve due esigenze richieste:

1. Sapere se il cliente (ristorante/bar/pub/pizzeria... vs bottega/negozio/
   gastronomia/supermercato...) va servito con pezzature HORECA (grandi,
   pensate per il consumo/lavorazione in cucina o al banco) o RETAIL
   (piccole, pensate per la rivendita al consumatore finale).

2. Stimare, per ogni prodotto del catalogo, se il suo formato è più adatto
   a un canale HORECA o RETAIL, leggendo la pezzatura dal testo già
   indicizzato (nessuna nuova classificazione via LLM richiesta: il
   vocabolario di packaging è generico nel food e non cambia da fornitore a
   fornitore, quindi un parser deterministico è più robusto e non necessita
   di rielaborare il catalogo esistente).

Questa è una preferenza (boost), MAI un'esclusione secca: se per un dato
prodotto esiste solo il formato HORECA e il cliente è una bottega, meglio
comunque proporglielo (magari con nota "disponibile anche in pezzatura
inferiore su richiesta") piuttosto che non mostrare nulla.
"""

import re

# ----------------------------------------------------------------------
# 1. Tipo di locale -> canale
# ----------------------------------------------------------------------
TIPI_LOCALE_HORECA = [
    "ristorante", "trattoria", "osteria", "pizzeria", "bistrot", "bar",
    "pub", "hamburgeria", "hamburgheria", "paninoteca", "wine bar",
    "enoteca con cucina", "catering", "mensa", "agriturismo", "hotel",
    "b&b", "resort", "street food", "food truck", "cucina", "chef",
]
TIPI_LOCALE_RETAIL = [
    "bottega", "negozio", "gastronomia", "salumeria", "macelleria",
    "drogheria", "supermercato", "minimarket", "market", "rivendita",
    "alimentari", "punto vendita", "gastronomia da asporto",
]


def rileva_canale_locale(tipo_locale: "str | None") -> str:
    """Da una descrizione libera del tipo di locale (come profilata da
    analizza_richiesta_e_profila in app.py), ritorna 'horeca', 'retail' o
    'misto' (se non determinabile / locale con entrambe le anime, es. una
    'gastronomia con cucina e somministrazione')."""
    if not tipo_locale:
        return "misto"
    t = tipo_locale.strip().lower()
    is_horeca = any(k in t for k in TIPI_LOCALE_HORECA)
    is_retail = any(k in t for k in TIPI_LOCALE_RETAIL)
    if is_horeca and is_retail:
        return "misto"
    if is_horeca:
        return "horeca"
    if is_retail:
        return "retail"
    return "misto"


# ----------------------------------------------------------------------
# 2. Testo prodotto -> formato canale
# ----------------------------------------------------------------------
# Pattern per pezzature tipicamente HORECA: confezioni grandi, "da banco",
# pensate per essere porzionate/lavorate in cucina o affettate al momento.
_PATTERN_HORECA = re.compile(
    r"\b("
    r"\d+([.,]\d+)?\s*kg\b"          # es. "3 kg", "1,5kg"
    r"|secchiell\w*"
    r"|busta\s+da\s+banco"
    r"|pezzatur\w*\s+intera"
    r"|al\s+tagli\w*"
    r"|forma\s+intera"
    r"|filone"
    r"|\d+\s*x\s*\d+([.,]\d+)?\s*kg"  # es. "6 x 1 kg"
    r")\b",
    re.IGNORECASE,
)
# Pattern per pezzature tipicamente RETAIL: piccole, pronte per lo scaffale
# o la vendita diretta al pubblico.
_PATTERN_RETAIL = re.compile(
    r"\b("
    r"\d+([.,]\d+)?\s*g\b(?!r)"      # es. "100 g" (non confonde con "gr" già gestito sotto)
    r"|\d+([.,]\d+)?\s*gr\b"
    r"|vaschett\w*"
    r"|vasett\w*"
    r"|vaso\b"
    r"|skin\b"
    r"|monodos\w*"
    r"|monoporzion\w*"
    r"|astuccio"
    r"|confezione\s+singola"
    r"|ATM\b"
    r")\b",
    re.IGNORECASE,
)


def rileva_formato_prodotto(testo_prodotto: str) -> str:
    """Ritorna 'horeca', 'retail' o 'misto' in base alla pezzatura citata nel
    testo del prodotto già indicizzato in ChromaDB (documento completo).
    'misto' significa: formato non riconosciuto o disponibile in entrambe le
    varianti -> nessuna preferenza, il prodotto resta comunque proponibile a
    qualunque canale.

    FALLBACK: da usare solo se il prodotto non ha ancora il campo strutturato
    "formato_variante_liv5" nei metadati (vedi rileva_canale_da_formato_liv5
    sotto, che è la versione preferita e affidabile perché legge un campo
    già estratto puntualmente dalla scheda tecnica invece di indovinarlo dal
    testo libero)."""
    if not testo_prodotto:
        return "misto"
    ha_horeca = bool(_PATTERN_HORECA.search(testo_prodotto))
    ha_retail = bool(_PATTERN_RETAIL.search(testo_prodotto))
    if ha_horeca and ha_retail:
        return "misto"
    if ha_horeca:
        return "horeca"
    if ha_retail:
        return "retail"
    return "misto"


def rileva_canale_da_formato_liv5(formato_variante: "str | None") -> str:
    """Versione PREFERITA di rileva_formato_prodotto: legge il campo
    strutturato 'Formato/Variante (Liv 5)' di Tassonomia.xlsx (già propagato
    nei metadati ChromaDB come 'formato_variante_liv5' dalla migrazione, vedi
    rigenera_tassonomia_db.py), invece di indovinare da testo libero. Più
    corto e più pulito del documento intero -> meno falsi positivi."""
    return rileva_formato_prodotto(formato_variante or "")


def formato_metadata_o_testo(metadata: dict, documento: str = "") -> str:
    """Punto di ingresso comodo: usa il campo strutturato se presente,
    altrimenti torna al parsing sul testo intero del prodotto."""
    formato_liv5 = (metadata or {}).get("formato_variante_liv5")
    if formato_liv5:
        return rileva_canale_da_formato_liv5(formato_liv5)
    return rileva_formato_prodotto(documento)


def punteggio_coerenza_canale(canale_locale: str, formato_prodotto: str) -> int:
    """Piccolo bonus/malus da sommare al punteggio di un prodotto in fase di
    ordinamento dei risultati. Non azzera mai un prodotto: è un boost, non un
    filtro, perché un formato 'sbagliato' è comunque meglio di nessun prodotto."""
    if canale_locale == "misto" or formato_prodotto == "misto":
        return 0
    if canale_locale == canale_locale and canale_locale == formato_prodotto:
        return 2
    return -1
