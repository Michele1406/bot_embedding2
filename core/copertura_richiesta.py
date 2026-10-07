# -*- coding: utf-8 -*-
"""
copertura_richiesta.py
======================
Abstention deterministica: individua le parole della richiesta che NON compaiono in nessun punto del
catalogo (nome, tipo, descrizione, ingredienti, produttore). Se il cliente chiede "culatello",
"wagyu" o "arancini" e il catalogo non contiene mai quelle parole, il retrieval vettoriale restituisce
comunque i "piu' vicini" (prosciutti di pesce, fassona, riso): senza un segnale esplicito il modello
tende a presentarli come se fossero cio' che e' stato chiesto.

Il risultato diventa una riga nel contesto del modello, non un blocco: la decisione resta al modello,
ma sapendo che quel termine non esiste a catalogo.

Nota: la distanza vettoriale NON e' utilizzabile come soglia (misurato: "pasta per ristorante" 0.86
vs "sushi di wagyu" 0.83 con embedding gemini-embedding-2): per questo si usa il lessico.
"""

import re
import unicodedata

_STOP = {
    "dammi", "dimmi", "fammi", "vorrei", "voglio", "avete", "abbiamo", "servono", "serve", "cerco", "cercavo",
    "proponi", "propongo", "consigli", "consigliami", "mostrami", "fammelo", "ristorante", "ristoranti", "trattoria",
    "pizzeria", "locale", "cliente", "clienti", "referenze", "referenza", "prodotti", "prodotto", "formati", "formato",
    "grande", "grandi", "piccolo", "piccoli", "qualche", "alcuni", "alcune", "tipologie", "tipologia", "diverse",
    "diversi", "diversa", "sempre", "ancora", "anche", "oppure", "quindi", "perche", "perché", "quanto", "quanti",
    "bisogno", "proposta", "proposte", "selezione", "assortimento", "aperitivo", "aperitivi", "tagliere", "taglieri",
    "cosa", "cose", "alternativa", "alternative", "abbinare", "abbinamento", "abbinamenti", "pubblico", "pubblici",
    "settimana", "stagione", "stagionale", "particolari", "particolare", "migliore", "migliori", "ottimo", "ottima",
    "venduto", "vendete", "vendono", "cucina", "cucinare", "preparare", "preparazione", "ingredienti", "ingrediente",
    "genere", "stesso", "stessa", "stessi", "stesse", "quella", "quello", "quelle", "quelli", "questa", "questo",
    "questi", "queste", "vostro", "vostra", "vostri", "vostre", "nostro", "nostra", "nostri", "nostre",
    "possibile", "possibili", "grazie", "salve", "buongiorno", "buonasera", "ciao",
    "allergico", "allergica", "allergici", "allergiche", "allergia", "allergie", "intollerante", "intolleranza",
    "celiaco", "celiaca", "celiaci", "adesso", "invece", "allora", "ancora", "subito", "domani", "settimana",
    # parole comuni di 5 lettere
    "altro", "altra", "altri", "altre", "tutto", "tutti", "tutte", "fatto", "fatta", "prima", "dopo", "quale",
    "quali", "molto", "molti", "molte", "ogni", "poco", "tanto", "tanti", "meglio", "piatto", "piatti", "menu",
    "dolce", "dolci", "salato", "salati", "fresco", "fresca", "freschi", "forte", "forti", "buono", "buona",
    "buoni", "buone", "pezzo", "pezzi", "gusto", "gusti", "serve", "vorra", "dovre", "tipo", "tipi", "unico",
    "unica", "primo", "primi", "prima", "terzo", "terza", "mezzo", "mezza", "ideale", "ideali", "locali",
    "grosso", "grossa", "bello", "bella", "belle", "belli", "nuovo", "nuova", "nuovi", "nuove", "solo", "sole",
    "sotto", "sopra", "fuori", "dentro", "senza", "circa", "cosi", "pronto", "pronta", "pronti", "pronte",
}
# forme verbali/avverbiali: non sono prodotti ("parlami", "dirmi", "rigenerare", "considerando", "sarebbero")
_RE_VERBO = re.compile(r"(are|ere|ire|arsi|ersi|irsi|ando|endo|mi|ti|ci|vi|lo|la|li|le|ne|si|ato|ata|uto|uta|ito|ita|"
                       r"iamo|ete|ono|ero|erei|ebbe|ebbero|isce|asse|esse|mente)$")
_STOP2 = {"chiesto", "chiesti", "detto", "detti", "fatto", "visto", "preso", "scritto", "dato", "dati", "messo", "spiego", "preso", "visto", "metti", "parli", "marche", "utili", "salda", "tipologie", "aziende", "azienda",
          "offerta", "rinnovo", "serie", "elenco", "lista", "gamma", "linea", "linee", "marca", "marchi", "marchio",
          "referenti", "foto", "immagine", "immagini", "descrizione", "descrivi", "spiega", "spiegami"}
_CACHE: dict = {}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", t)


def _vocabolario(indice_testuale: list) -> dict:
    chiave = (id(indice_testuale), len(indice_testuale))
    if chiave not in _CACHE:
        voc = {}  # parola -> numero di prodotti in cui compare
        for p in indice_testuale:
            m = p["metadata"]
            testo = " ".join([p["document"], str(m.get("tipo_prodotto") or ""), str(m.get("nome_fornitore") or ""),
                              str(m.get("sottocategoria") or ""), str(m.get("specifiche_liv4") or "")])
            for w in set(_norm(testo).split()):
                voc[w] = voc.get(w, 0) + 1
        _CACHE.clear()
        _CACHE[chiave] = voc
    return _CACHE[chiave]


def _presente(parola: str, voc: dict) -> bool:
    """Match esatto o per radice (plurali/singolari: "arancini" ~ "arancino", "olive" ~ "oliva")."""
    if parola in voc:
        return True
    radice = parola[:-1] if len(parola) > 6 else parola
    if len(radice) >= 6 and any(w.startswith(radice) for w in voc if len(w) >= len(radice)):
        return True
    return False


def _quasi_presente(parola: str, voc: dict) -> bool:
    """Refusi ("parmiggiano", "salda" per "salada"): simile a una parola del catalogo."""
    import difflib
    # solo parole frequenti: un refuso punta a una parola comune ("parmigiano"), non a una rara ("arancioni")
    cand = [w for w, df in voc.items() if df >= 3 and abs(len(w) - len(parola)) <= 1 and w[:1] == parola[:1]]
    return bool(difflib.get_close_matches(parola, cand, n=1, cutoff=0.9 if len(parola) <= 8 else 0.86))


_DIMINUTIVI = ("ina", "ine", "ino", "ini", "etta", "ette", "etto", "etti", "ella", "elle", "ello", "elli")


def _diminutivo_presente(parola: str, voc: dict) -> bool:
    """"marmellatina", "olivette", "vasetti": la parola base c'e' a catalogo (chat reale: "marmellatina" dichiarata
    non disponibile)."""
    for suf in _DIMINUTIVI:
        radice = parola[:-len(suf)]
        if parola.endswith(suf) and len(radice) >= 4:
            if any(w.startswith(radice) and len(w) <= len(radice) + 2 for w in voc):
                return True
    return False


def termini_senza_riscontro(query: str, indice_testuale: list, sinonimi: "dict | None" = None) -> list:
    """Parole della query (>= 5 lettere, non generiche) assenti da tutto il catalogo."""
    if not indice_testuale:
        return []
    from core.logistica import parole_geografiche  # citta' e regioni ("sono a Milano") non sono prodotti mancanti
    geo = parole_geografiche()
    voc = _vocabolario(indice_testuale)
    out = []
    for w in _norm(query).split():
        if len(w) < 5 or w in _STOP or w in _STOP2 or w in geo or w.isdigit() or _RE_VERBO.search(w):
            continue
        if sinonimi and w in sinonimi:
            continue
        if not _presente(w, voc) and not _quasi_presente(w, voc) and not _diminutivo_presente(w, voc) and w not in out:
            out.append(w)
    return out


def riga_contesto(termini: list) -> str:
    if not termini:
        return ""
    elenco = ", ".join(f"'{t}'" for t in termini)
    return (f"[ATTENZIONE RICERCA: nel catalogo non compare MAI il termine {elenco}. Se il cliente sta chiedendo "
            f"proprio quel prodotto, comunica con chiarezza che non e' disponibile; non presentare come sostituti "
            f"prodotti diversi e non inventare nomi. Puoi proporre solo alternative realmente presenti qui sotto, "
            f"dichiarandole come alternative.]")
