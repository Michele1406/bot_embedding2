# -*- coding: utf-8 -*-
"""
testo_prodotto.py
=================
Helper condivisi per leggere il "nome" di un prodotto dal documento indicizzato.

Prima di questo modulo il nome veniva letto ovunque con `doc.splitlines()[0]`:
con documenti che iniziano con una riga vuota o con il solo BOM (es.
19010826_CD309, 19010369_LAGHIANDAP01) il nome risultava vuoto e tutti i filtri
basati sul titolo (esclusioni tagliere, constraint solver, formattazione)
smettevano di funzionare in silenzio.
"""

import re

_PREFISSO_PRODOTTO = re.compile(r"^\s*PRODOTTO\s*:\s*", re.IGNORECASE)


def prima_riga(doc: "str | None") -> str:
    """Prima riga NON vuota del documento, senza BOM e con spazi estremi tolti."""
    if not doc:
        return ""
    for riga in doc.splitlines():
        pulita = riga.replace("﻿", "").strip()
        if pulita:
            return pulita
    return ""


def nome_prodotto(doc: "str | None") -> str:
    """Riga del nome prodotto, senza l'etichetta 'PRODOTTO:'."""
    return _PREFISSO_PRODOTTO.sub("", prima_riga(doc)).strip()


def _parole(testo: str) -> set:
    return {p for p in re.findall(r"[a-z0-9àèéìòù]+", (testo or "").lower()) if len(p) >= 3}


def nome_senza_produttore(doc: "str | None", nome_fornitore: str = "") -> str:
    """Nome del prodotto SENZA il prefisso del produttore.

    "FRANCHI SALUMI - WÜRSTEL SOTTOVUOTO"  (fornitore "Franchi Salumi") -> "WÜRSTEL SOTTOVUOTO"
    "Olio Anfosso - Olive Taggiasche"       (fornitore "Anfosso")        -> "Olive Taggiasche"

    Il prefisso viene tolto solo se, prima del primo trattino, compare almeno
    una parola del nome del fornitore: così un trattino interno al nome vero
    del prodotto ("Prosciutto di Parma - Stagionato 24 mesi") non viene
    scambiato per un brand. Serve alla ricerca lessicale: se la query contiene
    "salumi", un prodotto non deve essere trovato solo perché il suo produttore
    si chiama "Franchi Salumi".
    """
    nome = nome_prodotto(doc)
    if " - " not in nome and "-" not in nome:
        return nome
    prefisso, _, resto = nome.partition(" - ") if " - " in nome else nome.partition("-")
    if resto.strip() and _parole(prefisso) & _parole(nome_fornitore):
        return resto.strip()
    return nome


_RE_DISCLAIMER = re.compile(r"\[DISCLAIMER[^\]]*\].*", re.IGNORECASE | re.DOTALL)


def scheda_per_contesto(doc: "str | None") -> str:
    """Testo della scheda per il modello, SENZA il disclaimer legale finale (in 1187 schede su 1338): il modello lo
    usava come risposta ("le foto sono a titolo illustrativo", chat reale) e costa token."""
    return _RE_DISCLAIMER.sub("", (doc or "").replace("﻿", "")).strip()


# Sezioni della scheda: il RACCONTO del prodotto (per argomentare) e i DATI TECNICI (per le domande mirate:
# "e' magro?", "che ingredienti ha?", "contiene glutine?"). Le altre sezioni restano nei dati tecnici.
_RE_SEZIONE = re.compile(r"^\W*([A-ZÀ-Ü][A-ZÀ-Ü '/]{2,40}?)\s*(?:\([^)]*\))?\s*:\s*", re.MULTILINE)
_SEZIONI_RACCONTO = ("DESCRIZIONE", "ABBINAMENTI GASTRONOMICI", "STAGIONATURA", "TIPOLOGIA", "TIPOLOGIA FORMAGGIO",
                     "CROSTA", "ORIGINE DEL LATTE", "LUOGO DI PRODUZIONE", "LUOGO DI STAGIONATURA", "ORIGINE", "FAQ")
_SEZIONI_ESCLUSE = ("PRODOTTO",)


def sezioni_scheda(doc: "str | None") -> list:
    """[(TITOLO, testo)] nell'ordine della scheda, senza disclaimer."""
    testo = scheda_per_contesto(doc)
    marcatori = list(_RE_SEZIONE.finditer(testo))
    out = []
    for i, m in enumerate(marcatori):
        fine = marcatori[i + 1].start() if i + 1 < len(marcatori) else len(testo)
        corpo = re.sub(r"\s+", " ", testo[m.end():fine]).strip()
        if corpo:
            out.append((m.group(1).strip(), corpo))
    return out


def scheda_strutturata(doc: "str | None") -> str:
    """Scheda per il modello divisa in "Racconto" (descrizione, stagionatura, abbinamenti della scheda...) e "Dati
    tecnici" (ingredienti, valori nutrizionali, allergeni, conservazione, produttore). Se la scheda non ha sezioni
    riconoscibili, il testo cosi' com'e'."""
    sezioni = sezioni_scheda(doc)
    racconto = [f"{t.capitalize()}: {c}" for t, c in sezioni if t in _SEZIONI_RACCONTO]
    tecnici = [f"{t}: {c}" for t, c in sezioni if t not in _SEZIONI_RACCONTO and t not in _SEZIONI_ESCLUSE]
    if not racconto:
        return "Scheda: " + scheda_per_contesto(doc)
    righe = ["Racconto del prodotto (usalo per descriverlo): " + " | ".join(racconto)]
    if tecnici:
        righe.append("Dati tecnici (solo per domande mirate: ingredienti, valori, se e' magro, allergeni, conservazione): "
                     + " | ".join(tecnici))
    return "\n".join(righe)


def riga_foto(meta: dict) -> str:
    """'Foto: disponibile' solo se il file esiste davvero; mai il percorso (lo inserisce il sistema, core/foto.py)."""
    from core.foto import percorso
    return "Foto: disponibile (la allega il sistema quando serve)" if percorso(meta) else "Foto: non disponibile"
