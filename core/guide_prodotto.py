# -*- coding: utf-8 -*-
"""
Guida tecnica ai prodotti: tagli di carne, legumi e cereali, riso, farine, pomodoro, uova, formaggi da cucina.

Due usi (second brain, REPORT_COLLEGAMENTI.md):
  1. SCHEDA TECNICA del prodotto di cui si parla (`riga`): carattere, cotture, ammollo, piatti, porzione indicativa e,
     dalla scheda, linea Oberto / tempi di cottura / formato di servizio. La conoscenza generale sta in
     guide_prodotto.yaml (mai nel prompt); i dati della scheda hanno la precedenza.
  2. CONSIGLIO per uso (`consiglio`): "che taglio per la griglia?", "che legumi per una zuppa?", "che riso per il
     risotto?" -> per ogni guida pertinente un prodotto realmente a catalogo (dieta/canale/esclusioni ok) e, se il
     cliente dice per quante persone, il fabbisogno indicativo. Nessuna chiamata API.

Il testo prodotto dalla guida entra nel contesto: i prodotti citati sono sempre quelli del catalogo.
"""
import os
import re
from functools import lru_cache

import yaml

_PERCORSO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guide_prodotto.yaml")

_RE_CONSIGLIO = re.compile(r"\b(che|quale|quali|cosa|consigli\w*|suggerisc\w*|propon\w*|adatt\w*|ideale|ideali|miglior\w*|"
                           r"serv\w*|meglio|per|cucin\w*)\b", re.IGNORECASE)
_RE_FAMIGLIA = {
    "carne": re.compile(r"\b(tagli\w*|carne|carni|manzo|bovino|vitello|fassona|bistecc\w*|costat\w*|fiorentin\w*|"
                        r"hamburger|macinato|agnello|maiale|suino|pollo|bollit\w*|brasat\w*|stracott\w*|tagliat\w*|arrost\w*|"
                        r"spezzatin\w*|carpaccio|tartare|grigli\w*)\b", re.IGNORECASE),
    "legume": re.compile(r"\b(legum\w*|lenticch\w*|ceci|fagiol\w*|cicerchi\w*|fave|zupp\w*|minestr\w*|orzo|farro|"
                         r"cereal\w*|quinoa)\b", re.IGNORECASE),
    "riso": re.compile(r"\b(riso|risi|risott\w*)\b", re.IGNORECASE),
    "farina": re.compile(r"\b(farin\w*|semola|impast\w*)\b", re.IGNORECASE),
    "pomodoro": re.compile(r"\b(pomodor\w*|pelati|passata|polpa|sugh\w*|sugo)\b", re.IGNORECASE),
    "uova": re.compile(r"\b(uova|uovo|tuorl\w*)\b", re.IGNORECASE),
    "mare": re.compile(r"\b(pesce|pesci|tonno|salmone|spada|bottarga|acciugh\w*|alici|caviale|ricci|baccal\w*|mare|crudi)\b",
                       re.IGNORECASE),
    "olio": re.compile(r"\b(olio|oli|extravergine|evo)\b", re.IGNORECASE),
    "aceto": re.compile(r"\b(acet\w*|balsamic\w*)\b", re.IGNORECASE),
}
_RE_ORIENTAMENTO = re.compile(r"\b(che|quale|quali)\s+(tagli|taglio|tipo|tipi|tipologie|legumi|legume|riso|risi|farina|"
                              r"farine|pomodor\w+|carne|carni|olio|oli|aceto|aceti|pesce|pesci)\b", re.IGNORECASE)
_RE_PERSONE = re.compile(r"\b(\d{1,4})\s*(persone|coperti|ospiti|commensali|pax|clienti)\b", re.IGNORECASE)
_RE_GRAMMI = re.compile(r"(\d{2,4})(?:\s*-\s*(\d{2,4}))?\s*g\b")
_RE_LINEA = re.compile(r'Linea\s+"([^"]+)"')
_RE_COTTURA = re.compile(r"tempi?\s+di\s+cottura\s*:?\s*([^\n]+)", re.IGNORECASE)
_RE_FRUTTATO = re.compile(r"tipo di fruttato\s*-\s*([^\n(]+)", re.IGNORECASE)
_RE_FORMATO = re.compile(r"^(?:PESO/)?FORMATO:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
MAX_GUIDE_CONSIGLIO = 5


@lru_cache(maxsize=1)
def _cfg() -> dict:
    with open(_PERCORSO, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _testo(p: dict) -> str:
    m = p.get("metadata") or {}
    return (str(m.get("tipo_prodotto") or "") + " " + (p.get("document") or "").lstrip("﻿").split("\n")[0]).lower()


def _combacia(g: dict, p: dict) -> bool:
    from core.second_brain import combacia
    m = p.get("metadata") or {}
    sel = dict(g.get("quando") or {})
    if g.get("famiglia") == "carne" and not sel.get("reparti"):
        sel["reparti"] = ["CARNE"]
    if not combacia(p, sel, tutte=False):
        return False
    if g.get("scheda") and not any(w in (p.get("document") or "").lower() for w in g["scheda"]):
        return False
    t = _testo(p)
    return all(any(w in t for w in gruppo) for gruppo in g.get("e_anche") or [])


def guide_per(p: dict) -> tuple:
    """(guida base | None, [note modificatrici]). Vince la prima guida base che combacia."""
    base, modifiche = None, []
    for g in _cfg().get("guide") or []:
        if not _combacia(g, p):
            continue
        if g.get("modifica"):
            modifiche.append(g)
        elif base is None:
            base = g
    return base, modifiche


def _guida_per_id(gid: str) -> "dict | None":
    return next((g for g in _cfg().get("guide") or [] if g.get("id") == gid), None)


def dati_scheda(p: dict) -> dict:
    """Cio' che la scheda dice davvero: linea Oberto, tempi di cottura, formato."""
    doc = p.get("document") or ""
    out = {}
    m = _RE_LINEA.search(doc)
    if m:
        out["linea"] = m.group(1)
    m = _RE_COTTURA.search(doc)
    if m:
        out["cottura"] = m.group(1).strip().rstrip(".")
    m = _RE_FRUTTATO.search(doc)
    if m:
        out["fruttato"] = m.group(1).strip()
    m = _RE_FORMATO.search(doc)
    if m:
        out["formato"] = m.group(1).strip()
    return out


def servizio(p: dict) -> str:
    """Pezzo pronto o da porzionare, dal nome e dal formato (mai inventato)."""
    t = _testo(p) + " " + str(dati_scheda(p).get("formato") or "").lower()
    if re.search(r"monoporzion|porzionat|singol", t):
        return "gia' porzionato, pronto da cuocere"
    if re.search(r"\bintero\b|\bintera\b|\d\s*/\s*\d+\s*kg|oltre \d+ kg", t):
        return "pezzo intero, da porzionare in cucina"
    return ""


def riga(p: dict, con_simili: bool = False, brain=None, dieta=None, esclusi=None, canale=None) -> str:
    """Riga di guida tecnica per il prodotto, "" se non c'e' nulla di verificato da dire."""
    base, modifiche = guide_per(p)
    dati = dati_scheda(p)
    if not base and not modifiche and not dati.get("linea") and not dati.get("cottura") and not dati.get("fruttato"):
        return ""
    pezzi = []
    if base:
        pezzi.append(f"{base.get('nome')}: {base.get('carattere')}" if base.get("carattere") else str(base.get("nome")))
        if base.get("cotture"):
            pezzi.append("cotture: " + ", ".join(base["cotture"]))
        if base.get("ammollo"):
            pezzi.append("ammollo: " + base["ammollo"])
    for g in modifiche:
        pezzi.append(g["nota"])
    if dati.get("cottura"):
        pezzi.append("dalla scheda, tempi di cottura: " + dati["cottura"])
    if dati.get("fruttato"):
        pezzi.append("dalla scheda, fruttato: " + dati["fruttato"])
    if dati.get("linea"):
        pezzi.append(f'dalla scheda, linea "{dati["linea"]}"')
    s = servizio(p) if base and base.get("famiglia") == "carne" else ""
    if s:
        pezzi.append("formato: " + s)
    if base:
        if base.get("piatti"):
            pezzi.append("piatti tipici: " + ", ".join(base["piatti"][:3]))
        if base.get("porzione"):
            pezzi.append("porzione indicativa: " + base["porzione"])
    if con_simili and base and base.get("simili") and brain is not None:
        sim = _prodotti_per_guide(brain, base["simili"], 2, dieta, esclusi, canale, escludi_id={p["id"]})
        if sim:
            from core.second_brain import _nome, nome_breve
            pezzi.append("tagli simili a catalogo: " + "; ".join(
                f"{_nome(q)} (Produttore: {nome_breve(q['metadata'].get('nome_fornitore'), q.get('document', ''))})"
                for q in sim))
    return "; ".join(pezzi)


# ---------------------------------------------------------------- consiglio per uso
_INDICE: dict = {}


def _indice(brain) -> dict:
    """guida id -> [id prodotto]: si costruisce una volta per second brain (1500 prodotti x ~70 guide)."""
    chiave = id(brain.prodotti)
    if _INDICE.get("chiave") != chiave:
        per_guida = {}
        for p in brain.prodotti.values():
            base = guide_per(p)[0]
            if base:
                per_guida.setdefault(base["id"], []).append(p["id"])
        _INDICE.clear()
        _INDICE.update({"chiave": chiave, "dati": per_guida})
    return _INDICE["dati"]


def _prodotti_per_guide(brain, ids_guide: list, n: int, dieta, esclusi, canale, escludi_id=frozenset()) -> list:
    """Un prodotto per guida (formati diversi dello stesso prodotto contano uno), canale giusto per primo."""
    from core.second_brain import chiave_formato
    out, visti = [], set()
    for gid in ids_guide:
        g = _guida_per_id(gid)
        if not g:
            continue
        cand = [brain.prodotti[i] for i in _indice(brain).get(g["id"], [])
                if i not in escludi_id and brain._ammesso(brain.prodotti[i], dieta, esclusi, canale, ammetti_ingredienti=True)]
        cand.sort(key=lambda p: (brain._chiave_canale(p, canale), not servizio(p).startswith("gia' porzionato"), p["id"]))
        for p in cand:
            k = chiave_formato(p)
            if k not in visti:
                visti.add(k)
                out.append(p)
                break
        if len(out) >= n:
            break
    return out


def usi_nel_testo(testo: str) -> list:
    t = (testo or "").lower()
    return [uso for uso, parole in (_cfg().get("usi") or {}).items() if any(w in t for w in parole)]


def famiglie_nel_testo(testo: str) -> list:
    return [f for f, rx in _RE_FAMIGLIA.items() if rx.search(testo or "")]


def fabbisogno(testo: str, guida: dict) -> str:
    """"per 20 persone" + porzione della guida -> "20 x 200 g = circa 4 kg"."""
    mp = _RE_PERSONE.search(testo or "")
    mg = _RE_GRAMMI.search((guida or {}).get("porzione") or "")
    if not mp or not mg:
        return ""
    n, lo = int(mp.group(1)), int(mg.group(1))
    hi = int(mg.group(2) or lo)
    kg = lambda g: f"{g / 1000:g}".replace(".", ",")
    return f"{n} persone x {lo}{'-' + str(hi) if hi != lo else ''} g = circa {kg(n * lo)}{'-' + kg(n * hi) if hi != lo else ''} kg"


def consiglio(testo: str, brain, dieta=None, esclusi=None, canale=None) -> "dict | None":
    """{"prodotti": [...], "blocco": str} per le domande "che taglio/legume/riso... per ...?"; None altrimenti."""
    if not brain or not getattr(brain, "pronto", False) or not _RE_CONSIGLIO.search(testo or ""):
        return None
    fam = famiglie_nel_testo(testo)
    usi = usi_nel_testo(testo)
    if not fam or not (usi or _RE_ORIENTAMENTO.search(testo)):
        return None
    guide = [g for g in _cfg().get("guide") or []
             if not g.get("modifica") and g.get("famiglia") in fam and g.get("usi")
             and (not usi or set(usi) & set(g["usi"]))]
    if usi and not guide:
        return None
    if not usi:  # orientamento generale: una guida per ciascun uso principale
        viste, scelte = set(), []
        for g in guide:
            if g["usi"][0] not in viste:
                viste.add(g["usi"][0])
                scelte.append(g)
        guide = scelte
    prodotti, righe = [], []
    from core.second_brain import _nome, nome_breve
    for g in guide:
        if len(righe) >= MAX_GUIDE_CONSIGLIO:
            break
        trovati = _prodotti_per_guide(brain, [g["id"]], 1, dieta, esclusi, canale)
        if not trovati:
            continue
        p = trovati[0]
        prodotti.append(p)
        riga_g = f"- {g['nome']}: {g.get('carattere', '')}"
        if g.get("piatti"):
            riga_g += f". Piatti: {', '.join(g['piatti'][:3])}"
        if g.get("porzione"):
            riga_g += f". Porzione indicativa: {g['porzione']}"
        fab = fabbisogno(testo, g)
        if fab:
            riga_g += f". Fabbisogno: {fab}"
        s = servizio(p) if g.get("famiglia") == "carne" else ""
        riga_g += (f" -> a catalogo: {_nome(p)} (Produttore: "
                   f"{nome_breve(p['metadata'].get('nome_fornitore'), p.get('document', ''))})" + (f", {s}" if s else ""))
        righe.append(riga_g)
    if not righe:
        return None
    blocco = ("[GUIDA ALLA SCELTA (conoscenza So Food + catalogo): proponi 2-3 di queste opzioni spiegando perche' "
              "vanno bene per l'uso richiesto, citando prodotto e produttore. Non aggiungere cotture, tempi o "
              "temperature che non sono scritti qui o nella scheda; le porzioni sono indicative. Se c'e' la voce Fabbisogno, riportala al cliente]\n" + "\n".join(righe))
    return {"prodotti": prodotti, "blocco": blocco}
