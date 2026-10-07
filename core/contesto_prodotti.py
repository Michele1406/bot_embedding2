"""
Formattazione dei prodotti trovati nel contesto testuale per il modello.
(Estratto da retrieval_utils.py: i nomi restano importabili anche da li').
"""
from core.domain_rules import check_board_violations, prodotto_appartiene_a_famiglia, INCOMPATIBILITY_MATRIX
from core.testo_prodotto import prima_riga, nome_senza_produttore, riga_foto, scheda_strutturata
from core.parse_formato import formato_prodotto
from core.anagrafica_fornitori import nome_breve
from core import logistica, ontologia, territorio

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


def pulisci_nome_commerciale(nome_grezzo: str, nome_fornitore: str = "") -> str:
    """Ripulisce il nome grezzo del prodotto da prefissi fornitore (es. 'FARINO -'),
    termini di magazzino/imballo (SOTTOVUOTO, S/O, ATM, ecc.) e pesi finali,
    così il modello riceve già un nome pulito da presentare al cliente invece
    di doverlo riformattare correttamente da solo ogni volta."""
    s = nome_grezzo.strip()
    s = re.sub(r'^[^\w]*PRODOTTO\s*:\s*', '', s, flags=re.I).strip()

    # Rimuovi brand iniziale se c'è un trattino (es. 'FARINO - ', 'BBS - ')
    s = re.sub(r'^[A-Za-z0-9\s\'\.]+\s*-\s*', '', s).strip()
    if nome_fornitore:
        # Rimuovi il fornitore iniziale SOLO se è parola intera \b
        s = re.sub(r'^\b' + re.escape(nome_fornitore.strip()) + r'\b\s*[-:]?\s*', '', s, flags=re.I).strip()
        # Rimuovi anche "di [Fornitore]" o il fornitore alla fine della stringa
        s = re.sub(r'(?i)\bdi\s+' + re.escape(nome_fornitore.strip()) + r'\b', '', s).strip()
        s = re.sub(r'(?i)\b' + re.escape(nome_fornitore.strip()) + r'\b$', '', s).strip()
    
    # Rimuovi termini tecnici di imballo, taglio e magazzino
    for t in [r'\bSOTTOVUOTO\b', r'\bS/O\b', r'\bS/COTENNA\b', r'\bS/C\b', r'\bATM\b',
              r'\bMEZZA\b', r'\bINTERA\b', r'\bSILURO\b', r'\bTRANCI(?:O)?\b',
              r'\bAFFETTAT[OA]\b', r'\bIN BUSTA\b', r'\bIN VASCHETTA\b']:
        s = re.sub(t, '', s, flags=re.I).strip()

    # Rimuovi pesi finali grezzi (es. '1.8KG', '250G', '500 ML')
    s = re.sub(r'\b\d+(?:[\.,]\d+)?\s*(?:KG|G|GR|ML|CL|L)\b', '', s, flags=re.I).strip()
    s = re.sub(r"\bKG\s?\d+(?:[\.,]\d+)?\b", "", s, flags=re.I)        # "Kg15"
    s = re.sub(r"\b(?:a\s+)?met[aà]'?(?=\s|$)", "", s, flags=re.I)     # "a Meta'" (mezza forma)
    # parentesi rimaste vuote o con il solo resto del formato ("(Mezza Forma)" -> "( Forma)", chat reale)
    s = re.sub(r"\(\s*(?:forma|pezzo|pezzi|trancio|meta'?|metà)?\s*\)", "", s, flags=re.I)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r'\s+', ' ', s).strip(" -")

    # Mappatura codici orfani o privi di nome commerciale esplicito
    mappatura_codici_orfani = {
        "RAFI067": "Trito Piccante di Verdure e Peperoncino",
    }
    s_codice = re.sub(r'[^A-Za-z0-9]', '', s).upper()
    if s_codice in mappatura_codici_orfani:
        return mappatura_codici_orfani[s_codice]

    parole = s.split()
    minuscole = {'di', 'da', 'del', 'della', 'delle', 'dei', 'degli', 'al', 'alla', 'alle', 'ai', 'con', 'e', 'in', 'su', 'per', 'a'}
    maiuscole_fisse = {'IGP', 'DOP', 'DOC', 'BBS'}
    parole_title = []
    for i, p in enumerate(parole):
        p_upper = p.upper().rstrip(',.')
        if p_upper in maiuscole_fisse:
            parole_title.append(p_upper)
        elif i > 0 and p.lower() in minuscole:
            parole_title.append(p.lower())
        else:
            parole_title.append(p.capitalize())
    return ' '.join(parole_title)


# Schede COMPLETE per i primi N prodotti; gli altri in una riga (nome, produttore, tipo, formato). Debug 2026-10-06: con
# 30-33 schede complete (~60.000 caratteri) la risposta ne citava 1-3; il modello si perdeva e la quota si consumava.
MAX_SCHEDE_COMPLETE = 12


def riga_compatta(r: dict) -> str:
    meta = r["metadata"]
    nome = pulisci_nome_commerciale(prima_riga(r.get("document", "")), meta.get("nome_fornitore") or "")
    f = formato_prodotto(meta, r.get("document", ""))
    fmt = ""
    if f.get("valore") is not None:
        g = f["valore"]
        fmt = f" | {g / 1000:g} {'kg' if f['unita'] == 'g' else 'L'}" if g >= 1000 else f" | {g:g} {f['unita']}"
    return (f"- {nome} (Produttore: {nome_breve(meta.get('nome_fornitore'), r.get('document', ''))}) | "
            f"{meta.get('sottocategoria') or ''}{fmt}")


def costruisci_contesto_testuale(record_prodotti: list, id_gia_mostrati: "set | None" = None,
                                 max_schede: int = MAX_SCHEDE_COMPLETE) -> str:
    """Trasforma la lista di record in blocco di testo per il prompt RAG: schede complete per i primi max_schede,
    righe compatte per gli altri (citabili, ma senza descrizione: se servono si approfondiscono al turno dopo)."""
    if len(record_prodotti) > max_schede:
        completi = costruisci_contesto_testuale(record_prodotti[:max_schede], id_gia_mostrati, max_schede)
        return (completi + "\n\n[ALTRI PRODOTTI PERTINENTI (solo nome e formato: citali solo se servono, senza inventare "
                "descrizioni)]\n" + "\n".join(riga_compatta(r) for r in record_prodotti[max_schede:]))
    if not record_prodotti:
        return "Nessun prodotto trovato nel catalogo per questa richiesta."

    id_gia_mostrati = id_gia_mostrati or set()
    record_ordinati = sorted(record_prodotti, key=lambda r: 1 if r["id"] in id_gia_mostrati else 0)

    # Raggruppamento per dominio assegnato
    record_per_dominio = {}
    for r in record_ordinati:
        dom = r.get("dominio_assegnato", "generale")
        if dom not in record_per_dominio:
            record_per_dominio[dom] = []
        record_per_dominio[dom].append(r)

    blocchi = []
    idx_totale = 0
    for dominio, recs in record_per_dominio.items():
        if dominio != "generale":
            blocchi.append(f"\n[=========== DOMINIO RICHIESTO: {dominio.upper()} ===========]")
            
        for r in recs:
            idx_totale += 1
            meta = r["metadata"]
            prima_linea = prima_riga(r['document']) if r.get('document') else ""
            nome_pulito = pulisci_nome_commerciale(prima_linea, (meta.get('nome_fornitore') or ''))
            tag_match = "[MATCH ESATTO SU CODICE PRODOTTO] " if r.get("match_esatto") else ""
            tag_gia_visto = " [GIÀ MENZIONATO IN PRECEDENZA]" if r["id"] in id_gia_mostrati else ""
    
            blocco = f"\n--- MATCH {idx_totale} (ID: {r['id']}){tag_gia_visto} ---\n"
            blocco += f"{tag_match}Prodotto: {nome_pulito} (Produttore: {nome_breve(meta.get('nome_fornitore'), r.get('document', ''))})\n"
            blocco += f"Reparto: {meta.get('reparto', 'N/A')} | Categoria: {meta.get('categoria_tassonomia', meta.get('categoria_prodotto'))} | Sottocategoria: {meta.get('sottocategoria', 'N/A')} | Codice: {meta.get('codice_prodotto')}\n"
            blocco += f"Varianti: {meta.get('varianti_prodotto')}\n"
            _origine = territorio.riga_origine(meta, r.get("document", ""))
            if _origine:
                blocco += _origine + "\n"
            for _riga_extra in ontologia.righe_extra_contesto(meta):
                blocco += _riga_extra + "\n"
            _f = formato_prodotto(meta, r.get("document", ""))
            if _f["valore"] is not None:
                _g = _f["valore"]
                _txt = f"{_g / 1000:g} {'kg' if _f['unita'] == 'g' else 'L'}" if _g >= 1000 else f"{_g:g} {_f['unita']}"
                blocco += f"Formato: {_txt} (pezzatura {_f['canale_formato'].upper()})" + (" - peso variabile" if _f["peso_variabile"] else "") + "\n"
    
            blocco += riga_foto(meta) + "\n"
    
            blocco += scheda_strutturata(r["document"]) + "\n"
            blocchi.append(blocco)

    # Aggiungo la lista riassuntiva dei fornitori disponibili
    fornitori_presenti = set()
    for r in record_ordinati:
        f = str((r["metadata"].get("nome_fornitore") or "")).strip().title()
        if f and f.lower() not in ("sconosciuto", "nan", "none", "azienda agricola", "null"):
            fornitori_presenti.add(f)
            
    if fornitori_presenti:
        blocchi.append(f"\n[FORNITORI DISPONIBILI RILEVATI IN QUESTO CONTESTO: {', '.join(sorted(fornitori_presenti))}]\n")

    return "\n".join(blocchi)
