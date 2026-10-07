# -*- coding: utf-8 -*-
"""
Famiglie gastronomiche da tagliere e abbinamenti classici (regole in core/abbinamenti_horeca.yaml,
studio in docs/STUDIO_ABBINAMENTI.md).

  famiglia_prodotto(meta, doc) -> "crudo" | "insaccato" | ... | "duro" | "erborinato" | ... | None
  latte(meta, doc)             -> "vaccino" | "ovino" | "caprino" | "bufalino" | None (solo formaggi)
  tipo_base(meta, doc)         -> prima parola significativa del tipo ("pecorino", "salame", "mortadella")
  chiave_diversita(meta, doc)  -> chiave usata per non mettere due prodotti "uguali" nello stesso tagliere
  doppioni(prodotti)           -> [(motivo, [nomi])] coppie che violano la varieta' (stessa famiglia o stessa base)
  sottocategorie_classiche(famiglia) -> accompagnamenti classici (sottocategorie del catalogo)

Perche': il vincolo "non due salami" era espresso solo per alcune parole (INCOMPATIBILITY_MATRIX) e mancava
del tutto per i formaggi (due pecorini, due erborinati aromatizzati...). Qui la classificazione e' unica e
la usano selezione del tagliere, controllo finale e second brain.
"""
import os
import re

import yaml

from core.testo_prodotto import nome_prodotto

_PERCORSO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "abbinamenti_horeca.yaml")
_CACHE: dict = {}


def _cfg() -> dict:
    if "dati" not in _CACHE:
        with open(_PERCORSO, encoding="utf-8") as f:
            _CACHE["dati"] = yaml.safe_load(f) or {}
    return _CACHE["dati"]


def _testo(meta: dict, doc: str) -> str:
    t = f" {meta.get('tipo_prodotto') or ''} {nome_prodotto(doc or '')} ".lower()
    return re.sub(r"\s+", " ", t.replace("’", "'"))


def reparto_tagliere(meta: dict) -> "str | None":
    rep = str(meta.get("reparto") or "").upper()
    if rep == "SALUMI" or (rep == "CARNE" and meta.get("uso_tagliere")):
        return "salumi"
    if rep == "FORMAGGI":
        return "formaggi"
    return None


def famiglia_prodotto(meta: dict, doc: str = "") -> "str | None":
    gruppo = reparto_tagliere(meta)
    if not gruppo:
        return None
    t = _testo(meta, doc)
    for nome, parole in (_cfg().get(gruppo, {}).get("famiglie") or {}).items():
        if any(p and p.lower() in t for p in parole):
            return nome
    return None


def latte(meta: dict, doc: str = "") -> "str | None":
    if reparto_tagliere(meta) != "formaggi":
        return None
    t = _testo(meta, doc)
    for nome, parole in (_cfg().get("formaggi", {}).get("latte") or {}).items():
        if any((p == "" or p.lower() in t) for p in parole):
            return nome
    return None


def tipo_base(meta: dict, doc: str = "") -> str:
    generiche = set(_cfg().get("basi_generiche") or [])
    parole = re.findall(r"[a-zàèéìòù']+", str(meta.get("tipo_prodotto") or "").lower())
    if not parole:
        parole = re.findall(r"[a-zàèéìòù']+", nome_prodotto(doc or "").lower())
    for w in parole:
        if w not in generiche and len(w) >= 4:
            return w[:-1] if w.endswith(("i", "e")) and len(w) > 5 else w  # pecorini -> pecorin(o), salami -> salam
    return ""


def _radice(w: str) -> str:
    return w[:6]


def chiave_diversita(meta: dict, doc: str = "") -> str:
    """Famiglia se riconosciuta, altrimenti la sottocategoria."""
    return famiglia_prodotto(meta, doc) or str(meta.get("sottocategoria") or "").lower()


def ordine_famiglie(gruppo: str) -> list:
    return list((_cfg().get(gruppo) or {}).get("ordine_preferito") or [])


def doppioni(prodotti: list) -> list:
    """Coppie che violano la varieta' nello stesso tagliere: stessa base ("due pecorini") o stessa famiglia
    ("due crudi"). Ritorna [(motivo, [nomi coinvolti])]."""
    out = []
    per_base, per_fam = {}, {}
    for p in prodotti:
        m, d = p.get("metadata") or {}, p.get("document") or ""
        if not reparto_tagliere(m):
            continue
        nome = nome_prodotto(d) or str(p.get("id"))
        # la base conta solo per i formaggi (due pecorini di stagionature diverse); per i salumi basta la
        # famiglia, altrimenti "prosciutto crudo" e "prosciutto cotto" risulterebbero doppioni
        b = _radice(tipo_base(m, d)) if reparto_tagliere(m) == "formaggi" else ""
        if b:
            per_base.setdefault((reparto_tagliere(m), b), []).append(nome)
        f = famiglia_prodotto(m, d)
        if f:
            per_fam.setdefault((reparto_tagliere(m), f), []).append(nome)
    for (gr, b), nomi in per_base.items():
        if len(nomi) > 1:
            out.append((f"stesso tipo di {gr} ({b})", nomi))
    for (gr, f), nomi in per_fam.items():
        if len(nomi) > 1 and not any(set(nomi) == set(n) for _, n in out):
            out.append((f"stessa famiglia di {gr} ({f})", nomi))
    return out


def sottocategorie_classiche(famiglia: "str | None") -> list:
    return list((_cfg().get("abbinamenti_classici") or {}).get(famiglia or "", []) or [])
