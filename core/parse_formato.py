# -*- coding: utf-8 -*-
"""
parse_formato.py
================
Estrae la pezzatura di un prodotto come NUMERO (grammi o millilitri) dalle
stringhe reali del catalogo ("VASETTO VETRO 200 GR", "BOTTIGLIA IN VETRO 10CL",
"BAFFA INTERA ASTUCCIO 0,8/1,3 KG", "BUSTA 250G (5X50G)", "VASCHETTA DA 1,8KG - 2,5LT").

Sostituisce le regex di profilazione_locale.rileva_formato_prodotto, che
guardavano il testo libero e non riconoscevano litri/centilitri. Il canale
(HORECA/RETAIL) e' derivato dal numero con soglie dichiarate qui, non indovinato.
"""

import re

# Soglie generiche (g o ml). Sono una prima proposta: per famiglia di prodotto
# possono servire soglie diverse (vedi implementationplan.md, T2.2).
SOGLIA_RETAIL_MAX = 500      # <= 500 g/ml  -> formato da rivendita/scaffale
SOGLIA_HORECA_MIN = 1000     # >= 1000 g/ml -> formato da cucina/banco

_NUM = r"(\d+(?:[.,]\d+)?)"
# quantita' + unita'. L'ordine delle alternative conta: "kg" prima di "g", "cl/ml" prima di "l".
_RE_QTA = re.compile(
    _NUM + r"(?:\s*/\s*" + _NUM + r")?\+?\s*(kg|gr|g|lt|litri|litro|l|ml|cl)(?![a-z])",
    re.IGNORECASE,
)
_RE_MOLTIPLICATORE = re.compile(r"(\d+)\s*x\s*" + _NUM + r"\s*(kg|gr|g|lt|l|ml|cl)(?![a-z])", re.IGNORECASE)
_RE_PEZZI = re.compile(r"(\d+)\s*(?:pz|pezzi|pz\.|porzioni|buste|vasetti|bottiglie)\b", re.IGNORECASE)
_RE_VARIABILE = re.compile(r"peso\s+variabile|circa|ca\.|\bc\.a\b|variabile", re.IGNORECASE)
_RE_INTERO = re.compile(r"\b(intero|intera|forma|mezza forma|mezzo|disossat\w*|trancio|siluro|baffa)\b", re.IGNORECASE)

_A_BASE = {"kg": 1000.0, "g": 1.0, "gr": 1.0, "lt": 1000.0, "l": 1000.0, "litro": 1000.0, "litri": 1000.0,
           "ml": 1.0, "cl": 10.0}


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def _unita_base(u: str) -> str:
    return "ml" if u.lower() in ("lt", "l", "litro", "litri", "ml", "cl") else "g"


def parse_formato(testo: "str | None") -> dict:
    """Ritorna {valore, unita ('g'|'ml'|None), pezzi, peso_variabile, intero, canale_formato}.

    `valore` e' la quantita' della singola unita' di vendita (primo numero
    trovato; per intervalli "0,8/1,3 KG" il primo estremo; per "6 x 1 kg" il
    peso totale). Se non si trova nessuna quantita' valore=None."""
    t = (testo or "").strip()
    out = {"valore": None, "unita": None, "pezzi": None, "peso_variabile": False, "intero": False,
           "canale_formato": "sconosciuto"}
    if not t:
        return out

    out["peso_variabile"] = bool(_RE_VARIABILE.search(t))
    out["intero"] = bool(_RE_INTERO.search(t))
    pz = _RE_PEZZI.search(t)
    if pz:
        out["pezzi"] = int(pz.group(1))

    m_mol = _RE_MOLTIPLICATORE.search(t)
    m = _RE_QTA.search(t)
    if m_mol and (not m or m_mol.start() <= m.start()):
        out["pezzi"] = int(m_mol.group(1))
        out["valore"] = int(m_mol.group(1)) * _f(m_mol.group(2)) * _A_BASE[m_mol.group(3).lower()]
        out["unita"] = _unita_base(m_mol.group(3))
    elif m:
        out["valore"] = _f(m.group(1)) * _A_BASE[m.group(3).lower()]
        out["unita"] = _unita_base(m.group(3))

    out["canale_formato"] = canale_da_formato(out)
    return out


def canale_da_formato(f: dict) -> str:
    """'horeca' | 'retail' | 'misto' | 'sconosciuto' a partire dal formato parsato."""
    v = f.get("valore")
    if v is None:
        # nessun peso: pezzo intero / forma = formato da banco (horeca) solo se dichiarato tale
        return "horeca" if f.get("intero") else "sconosciuto"
    if v >= SOGLIA_HORECA_MIN:
        return "horeca"
    if v <= SOGLIA_RETAIL_MAX:
        return "retail"
    return "misto"


def formato_prodotto(meta: dict, documento: str = "") -> dict:
    """Formato di un prodotto: prima il campo strutturato 'formato_variante_liv5',
    poi la riga FORMATO/PESO del testo, poi il titolo. Si ferma alla prima fonte
    che contiene un numero."""
    from core.testo_prodotto import prima_riga

    candidati = [(meta or {}).get("formato_variante_liv5")]
    mm = re.search(r"(?:PESO/FORMATO|FORMATO|PESO)\s*:\s*(.+)", documento or "", re.IGNORECASE)
    if mm:
        candidati.append(mm.group(1))
    candidati.append(prima_riga(documento))
    ultimo = parse_formato(None)
    for c in candidati:
        r = parse_formato(c)
        if r["valore"] is not None:
            return r
        if r["intero"] and not ultimo["intero"]:
            ultimo = r
    return ultimo
