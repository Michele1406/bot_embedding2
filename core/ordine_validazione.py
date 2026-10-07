# -*- coding: utf-8 -*-
"""
Validazione dell'ordine estratto dall'LLM PRIMA di inoltrarlo al gestionale.

L'estrazione (order_extractor) legge la chat con un modello: puo' inventare o storpiare un prodotto, o sbagliare
la Partita IVA. Un ordine sbagliato costa piu' di una risposta sbagliata, quindi:
  * ogni riga deve corrispondere a UN prodotto del catalogo (codice articolo, oppure nome con copertura alta);
    la riga inoltrata riporta codice e nome del CATALOGO, non quelli scritti dal modello;
  * le righe non riconosciute o ambigue non vengono inoltrate: si chiede conferma al cliente;
  * la Partita IVA deve superare il controllo di checksum (algoritmo ufficiale a 11 cifre).
"""
import re

from core import guardrail_output
from core.testo_prodotto import nome_prodotto


def piva_valida(piva: "str | None") -> bool:
    """Controllo di checksum della Partita IVA italiana (11 cifre)."""
    p = re.sub(r"\D", "", str(piva or ""))
    if len(p) != 11 or p == "0" * 11:
        return False
    somma = 0
    for i, c in enumerate(p[:10]):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        somma += n
    return (10 - somma % 10) % 10 == int(p[10])


def _per_codice(codice: "str | None", indice_testuale: list):
    c = re.sub(r"\s+", "", str(codice or "")).lower()
    if len(c) < 3:
        return None
    for p in indice_testuale:
        if re.sub(r"\s+", "", str(p["metadata"].get("codice_prodotto") or "")).lower() == c:
            return p
    return None


def valida_righe(prodotti: list, indice_testuale: list, soglia: float = 0.7) -> tuple:
    """prodotti: [{codice_articolo, nome_prodotto, fornitore, quantita}] (dict). Ritorna (valide, scartate):
    valide   = [{codice_prodotto, nome, fornitore, quantita, id_catalogo}] con i dati del catalogo
    scartate = [(nome_scritto, motivo)]"""
    valide, scartate = [], []
    for r in prodotti or []:
        nome = str(r.get("nome_prodotto") or "").strip()
        qta = r.get("quantita") if r.get("quantita") is not None else 1
        try:
            qta = int(qta)
        except (TypeError, ValueError):
            qta = 1
        if qta < 1 or qta > 999:
            scartate.append((nome, f"quantita' non valida ({qta})"))
            continue
        p = _per_codice(r.get("codice_articolo"), indice_testuale)
        if p is None:
            testo = f"{nome} {r.get('fornitore') or ''}".strip()
            cop, cand = guardrail_output.miglior_prodotti(testo, indice_testuale)
            if cop < soglia or not cand:
                scartate.append((nome, "non presente a catalogo"))
                continue
            if len(cand) > 1:
                scartate.append((nome, f"ambiguo ({len(cand)} prodotti simili)"))
                continue
            p = cand[0]
        m = p["metadata"]
        valide.append({"codice_prodotto": str(m.get("codice_prodotto") or ""),
                       "nome": nome_prodotto(p["document"]),
                       "fornitore": str(m.get("nome_fornitore") or ""),
                       "quantita": qta, "id_catalogo": p["id"]})
    return valide, scartate
