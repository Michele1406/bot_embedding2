"""
Ricerca di base: indici in memoria, match lessicale/esatto/fornitore e ricerca vettoriale su ChromaDB.
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
from core.vincoli_dieta import _clausole_dieta_chroma


def parse_db_json_field(val, default=None):
    if default is None:
        default = []
    if val is None or str(val).lower() in ('nan', 'none', 'null'):
        return default
    if isinstance(val, (list, dict)):
        return val if val else (val if type(val) == type(default) else default)
    if not str(val).strip():
        return default
    try:
        import json
        parsed = json.loads(val)
        return parsed if parsed is not None else default
    except Exception:
        return [x.strip() for x in str(val).split(',') if x.strip()]


CATEGORIE_ESCLUSE_DA_RAG = [
    "SCHEDA AZIENDALE FORNITORE",
    "INFO LOGISTICA E ORDINI",
    "REGOLE DI CONSEGNA ZONALE",
    "RICETTARIO E REGOLE",
    "SCHEDA FORNITORE",
    "CONSEGNE E SPEDIZIONI",
]
REPARTI_ESCLUSI_DA_RAG = ["AZIENDALE", "LOGISTICA"]

# Quanti prodotti al massimo dello stesso fornitore possono comparire nel
# contesto RAG di un singolo turno. Serve solo a garantire varietà quando la
# ricerca vettoriale restituisce per caso molti prodotti dello stesso brand;
# non dipende da alcuna logica di "intent" o cucina.
MAX_PRODOTTI_PER_FORNITORE = 2

# Pattern permissivo per riconoscere codici prodotto nella query (es.
# LPSFIESP, RAFI064, 8015493004055).
_PATTERN_TOKEN_CODICE = re.compile(r"[A-Za-z0-9._\-/]{4,}")


def costruisci_indice_codici(collezione) -> dict:
    """Costruisce, UNA VOLTA all'avvio, un dizionario:
        codice_prodotto (maiuscolo) -> id documento in ChromaDB
    Da tenere in memoria per tutta la vita del processo (server o CLI)."""
    indice = {}
    try:
        dati = collezione.get(include=["metadatas"])
    except Exception as e:
        print(f"[ATTENZIONE] Impossibile costruire l'indice codici: {e}")
        return indice

    for doc_id, meta in zip(dati.get("ids", []), dati.get("metadatas", [])):
        codice = str(meta.get("codice_prodotto", "")).strip().upper()
        if codice and codice != "NAN":
            indice[codice] = doc_id
    print(f"[INFO] Indice codici prodotto costruito: {len(indice)} codici mappati.")
    return indice


def costruisci_indice_fornitori(collezione) -> dict:
    """Costruisce una mappa fornitore (in minuscolo) -> lista di id documento,
    per recuperare subito il catalogo di un produttore quando viene nominato."""
    indice = {}
    try:
        dati = collezione.get(include=["metadatas", "documents"])
    except Exception as e:
        print(f"[ATTENZIONE] Impossibile costruire l'indice fornitori: {e}")
        return indice

    from core.anagrafica_fornitori import ANAGRAFICA, norm
    vocabolario: dict = {}  # parola -> fornitori nei cui prodotti compare (per scartare alias troppo comuni)
    for doc_id, meta, doc in zip(dati.get("ids", []), dati.get("metadatas", []), dati.get("documents", []) or []):
        cat = str(meta.get("categoria_prodotto", ""))
        rep = str(meta.get("reparto", ""))
        if cat in CATEGORIE_ESCLUSE_DA_RAG or rep in REPARTI_ESCLUSI_DA_RAG:
            continue
        fornitore = str(meta.get("nome_fornitore", "")).strip()
        if fornitore and fornitore.upper() != "NAN":
            key = fornitore.lower()
            indice.setdefault(key, []).append(doc_id)
            for w in set(norm(doc).split()):
                vocabolario.setdefault(w, set()).add(key)
    ANAGRAFICA.costruisci(sorted(indice), {w: len(s) for w, s in vocabolario.items()}, _ALIAS_FORNITORI_MANUALI)
    print(f"[INFO] Indice fornitori costruito: {len(indice)} fornitori mappati, {len(ANAGRAFICA.alias)} alias.")
    return indice


def costruisci_indice_testuale(collezione) -> list:
    """Pre-carica in memoria i metadati e la prima riga di tutti i prodotti
    del catalogo per abilitare la ricerca lessicale ibrida ad altissima velocità."""
    try:
        dati = collezione.get(include=["metadatas", "documents"])
    except Exception as e:
        print(f"[ATTENZIONE] Impossibile costruire l'indice testuale: {e}")
        return []

    prodotti_memoria = []
    for doc_id, meta, doc in zip(dati.get("ids", []), dati.get("metadatas", []), dati.get("documents", [])):
        cat = str(meta.get("categoria_prodotto", ""))
        rep = str(meta.get("reparto", ""))
        if cat in CATEGORIE_ESCLUSE_DA_RAG or rep in REPARTI_ESCLUSI_DA_RAG:
            continue
        # Il titolo usato per il match lessicale NON contiene il nome del
        # produttore: altrimenti "salumi" troverebbe "FRANCHI SALUMI - Würstel"
        # e "olio" troverebbe "Olio Anfosso - Olive taggiasche" (test reali R2/R9).
        titolo_pulito = nome_senza_produttore(doc, str(meta.get("nome_fornitore") or "")).lower()
        sottocat = f"{meta.get('sottocategoria') or ''} {meta.get('specifiche_liv4') or ''} {meta.get('tipo_prodotto') or ''}".lower()
        prodotti_memoria.append({
            "id": doc_id,
            "metadata": meta,
            "document": doc,
            "titolo_lower": titolo_pulito,
            "sottocat_lower": sottocat,
        })
    _calcola_idf(prodotti_memoria)
    print(f"[INFO] Indice testuale catalogo costruito: {len(prodotti_memoria)} prodotti indicizzati in memoria.")
    return prodotti_memoria


_IDF_TOKEN: dict = {}


def _calcola_idf(prodotti_memoria: list) -> None:
    """Peso inverso di frequenza dei token (titolo+sottocategoria): un token
    che compare in quasi tutto il catalogo ("olio", "di") pesa poco, uno raro
    ("culatello") pesa tanto. Sostituisce il vecchio conteggio piatto."""
    import math
    df: dict = {}
    for p in prodotti_memoria:
        for t in set(re.findall(r"[a-z0-9àèéìòù]+", p["titolo_lower"] + " " + p["sottocat_lower"])):
            df[t] = df.get(t, 0) + 1
    n = max(len(prodotti_memoria), 1)
    _IDF_TOKEN.clear()
    _IDF_TOKEN.update({t: math.log(1 + n / c) for t, c in df.items()})


# Parole che non identificano un prodotto: funzione grammaticale, richieste
# generiche e tipi di locale ("ristorante" non è un ingrediente).
STOPWORDS_QUERY = {
    "cosa", "come", "dove", "quando", "vorrei", "voglio", "abbiamo", "avete", "servono", "consigli",
    "consigliami", "buono", "buona", "bello", "bella", "prodotti", "prodotto", "piatto", "piatti",
    "per", "con", "che", "del", "della", "delle", "dei", "degli", "una", "uno", "alla", "alle", "nel", "nella",
    "ristorante", "ristoranti", "locale", "trattoria", "osteria", "pizzeria", "bar", "bistrot", "cucina",
    "mio", "mia", "miei", "nostro", "nostra", "cliente", "clienti", "proposta", "proponi", "proporre",
}


def trova_match_lessicale(query: str, indice_testuale: list, max_risultati: int = 5,
                          categoria_filtro: str = "", max_per_fornitore: int = 2) -> list:
    """Cerca match diretti per parola chiave nel nome del prodotto.
    Garantisce che termini specifici (es. 'taralli', 'olive', 'nocciole', 'anacardi', 'bottarga')
    restituiscano sempre i prodotti reali, diversificando per fornitore per non monopolizzare i risultati."""
    if not indice_testuale or not query:
        return []

    STOPWORDS = STOPWORDS_QUERY
    tokens = [w.lower().rstrip("s.,;") for w in re.findall(r"[A-Za-z0-9àèéìòù]+", query.lower()) if len(w) >= 4 and w not in STOPWORDS]

    if not tokens:
        return []

    match_trovati = []
    for p in indice_testuale:
        if categoria_filtro and categoria_filtro.lower() not in str(p["metadata"].get("categoria_prodotto", "")).lower():
            continue
        titolo = p["titolo_lower"]
        # Se la query cerca snack/olive da tavola/aperitivo, non abbinare sughi di pomodoro per pasta
        if any(o in query.lower() for o in ["olive", "tavola", "aperitivo", "snack", "ciotol"]) and "sugo" not in query.lower() and "sugo" in titolo:
            continue
        # Se la query cerca pasta, primi piatti, sughi o condimenti, non abbinare semilavorati dolci per gelateria/pasticceria
        # (es. "Pasta Pura 100% Pistacchio" o creme dolci di frutta secca non sono formati di pasta né sughi pronti)
        sottocat = str(p["metadata"].get("sottocategoria", "")).lower()
        if any(e in titolo for e in ["pasta pura", "crema al pistacchio"]) and any(c in sottocat for c in ["frutta secca", "dolci", "gelat", "pasticcer"]):
            if any(d in query.lower() for d in ["gelat", "pasticcer", "dessert", "dolc", "colazion", "farcitur", "pasta pura"]):
                pass
            elif any(s in query.lower() for s in ["pasta", "sugo", "sughi", "condimento", "condimenti", "primo", "primi", "salato"]):
                continue
            elif "pasta pura" in titolo and "pasta pura" not in query.lower():
                continue
        # Punteggio IDF: match sul nome pieno, match sulla sottocategoria/tipo (peso metà).
        sottocat_p = p.get("sottocat_lower", "")
        punteggio = sum(_IDF_TOKEN.get(t, 1.0) for t in tokens if t in titolo)
        punteggio += 0.5 * sum(_IDF_TOKEN.get(t, 1.0) for t in tokens if t in sottocat_p and t not in titolo)
        # Bonus se la query nomina il TIPO del prodotto (catalogo_v2): "latte" -> "latte fresco ...",
        # non "gelato al cioccolato al latte".
        tipo_p = str(p["metadata"].get("tipo_prodotto") or "").lower().split()
        if punteggio > 0 and tipo_p and any(t == tipo_p[0] or (len(t) >= 5 and tipo_p[0].startswith(t)) for t in tokens):
            punteggio += 1.5 * max(_IDF_TOKEN.get(tipo_p[0], 1.0), 1.0)
        if punteggio > 0:
            match_trovati.append((punteggio, {
                "id": p["id"],
                "metadata": p["metadata"],
                "document": p["document"],
                "match_lessicale": True,
            }))

    match_trovati.sort(key=lambda x: x[0], reverse=True)
    conteggio_fornitori = {}
    risultati_diversificati = []
    for m in match_trovati:
        p = m[1]
        forn = p["metadata"].get("nome_fornitore", "")
        conteggio_fornitori[forn] = conteggio_fornitori.get(forn, 0) + 1
        if conteggio_fornitori[forn] <= max_per_fornitore:
            risultati_diversificati.append(p)
            if len(risultati_diversificati) >= max_risultati:
                break
    return risultati_diversificati


def trova_match_esatti_per_codice(user_query: str, indice_codici: dict, collezione) -> list:
    """Cerca nella query token che combaciano esattamente con un codice_prodotto
    noto. Ritorna una lista di record ChromaDB (di solito 0 o 1)."""
    if not indice_codici:
        return []

    token_trovati = {t.upper() for t in _PATTERN_TOKEN_CODICE.findall(user_query)}
    id_da_recuperare = [indice_codici[t] for t in token_trovati if t in indice_codici]
    if not id_da_recuperare:
        return []

    try:
        risultato = collezione.get(ids=id_da_recuperare, include=["metadatas", "documents"])
    except Exception as e:
        print(f"[ATTENZIONE] Lookup esatto per codice fallito: {e}")
        return []

    return [
        {"id": doc_id, "metadata": meta, "document": doc_text, "match_esatto": True}
        for doc_id, meta, doc_text in zip(
            risultato.get("ids", []), risultato.get("metadatas", []), risultato.get("documents", [])
        )
    ]


# Alias SOLO per nomi propri di fornitori/marchi (sinonimi, abbreviazioni,
# refusi comuni). Deliberatamente NON contiene parole generiche di prodotto
# (es. "pollo", "birra", "capocollo") mappate a un fornitore specifico:
# farlo forzerebbe sempre lo stesso brand per un concetto generico, che è
# esattamente il bias che abbiamo eliminato dal resto del file.
_ALIAS_FORNITORI = {
    "solera": ["solera"],
    "formaggeria toscana": ["formaggeria toscana"],
    "birrificio messina": ["birrificio messina"],
    "capuano": ["capuano"],
    "de filippis": ["de filippis"],
    "de giorgi": ["i de giorgi"],
    "farino": ["farino", "forni farino"],
    "branchi": ["branchi"],
    "franchi": ["franchi", "franchi salumi"],
    "crucolo": ["crucolo"],
    "anfosso": ["anfosso"],
    "pachineat": ["pachineat"],
    "montanari": ["montanari & gruzza"],
    "battaccone": ["battaccone"],
    "la casera": ["la casera"],
    "la ghianda": ["la ghianda"],
    "latte nobile": ["latte nobile"],
    "lovison": ["lovison"],
    "marrazzo": ["casa marrazzo 1934"],
    "casa marrazzo": ["casa marrazzo 1934"],
    "casa prencipe": ["casa prencipe"],
    "prencipe": ["casa prencipe"],
    "finagricola": ["finagricola"],
    "gentile": ["pastificio gentile", "conserve gentile", "forni gentile"],
    "pastificio gentile": ["pastificio gentile", "conserve gentile", "forni gentile"],
    "colimena": ["colimena"],
    "calugi": ["calugi"],
    "smeralda": ["smeralda"],
    "pisani dossi": ["pisani dossi"],
    "oberto": ["oberto"],
    "mongetto": ["mongetto"],
    "il mongetto": ["mongetto"],
    "di tria": ["di tria"],
    "cecinas nieto": ["cecinas nieto"],
    "medimer": ["medimer"],
    "boschi": ["boschi"],
    "valle di gresta": ["azienda agricola valle di gresta"],
    "val di gresta": ["azienda agricola valle di gresta"],
    "biobonta": ["biobontà"],
    "biobontà": ["biobontà"],
    "la bottega di ado": ["la bottega di ado'"],
    "ado'": ["la bottega di ado'"],
    "marilungo": ["pasta marilungo"],
    "acquerello": ["acquerello"],
    "la nicchia": ["la nicchia"],
    "salumi martina franca": ["salumi martina franca"],
    "pessolani": ["pessolani"],
    "suriano": ["suriano"],
    "mulino marino": ["mulino marino"],
    "evergreen": ["evergreen"],
    "guglielmi": ["guglielmi"],
    "scudellaro": ["scudellaro"],
    "patrone": ["patrone 1992"],
    "patrone 1992": ["patrone 1992"],
    "il convento": ["il convento"],
    "agricola buongiorno": ["agricola buongiorno"],
    "nino galli": ["nino galli"],
    "fresco piada": ["fresco piada"],
    "nero fermento": ["nero fermento"],
    "coradazzi": ["coradazzi"],
    "pellizziari": ["pellizziari"],
    "recco": ["recco"],
    "l'abbondanza": ["l'abbondanza"],
    "abbondanza": ["l'abbondanza"],
    "fratelli lunardi": ["fratelli lunardi", "lunardi"],
    "lunardi": ["fratelli lunardi", "lunardi"],
    "cantucci": ["fratelli lunardi", "lunardi"],
    "cantuccino": ["fratelli lunardi", "lunardi"],
    "cantuccini": ["fratelli lunardi", "lunardi"],
    "biscotti di prato": ["fratelli lunardi", "lunardi"],
    "colatura": ["delfino", "conserve gentile", "pastificio gentile"],
    "colatura di alici": ["delfino", "conserve gentile", "pastificio gentile"],
    "limoncello": ["il convento", "smeralda"],
    "uova": ["scudellaro"],
    "uova fresche": ["scudellaro"],
    "uovo": ["scudellaro"],
    "crucoloso": ["crucolo"],
    "spalmabile": ["crucolo"],
    "carnaroli": ["acquerello", "agricola lodigiana"],
    "burro": ["montanari & gruzza"],
}


# Varianti di NOME (refusi, forme brevi) che gli alias automatici di core/anagrafica_fornitori.py non deducono
_ALIAS_FORNITORI_MANUALI = {
    "val di gresta": ["valle di gresta"], "ado'": ["bottega di ado"], "ado": ["bottega di ado"],
    "il mongetto": ["mongetto"], "forni farino": ["farino"], "biobonta": ["biobont"], "bio bonta": ["biobont"],
    "solera iberica": ["solera"], "boschi": ["boschi"], "recco": ["recco"], "delfino": ["delfino"],
}


def trova_match_per_fornitore(user_query: str, indice_fornitori: dict, collezione, max_risultati: int = 10) -> list:
    """Se la query nomina esplicitamente un brand/fornitore (o un suo alias
    inequivocabile), recupera direttamente i suoi prodotti. Gli alias dei nomi si calcolano dal catalogo
    (core/anagrafica_fornitori.py); _ALIAS_FORNITORI resta per le parole di prodotto legate a un marchio."""
    if not indice_fornitori:
        return []

    query_lower = user_query.lower()
    doc_ids_trovati = []

    from core.anagrafica_fornitori import ANAGRAFICA
    for nome in ANAGRAFICA.fornitori_in_testo(user_query):
        doc_ids_trovati.extend(indice_fornitori.get(nome, []))

    for alias, targets in _ALIAS_FORNITORI.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", query_lower):
            for tgt in targets:
                for f_nome, ids in indice_fornitori.items():
                    if tgt in f_nome:
                        doc_ids_trovati.extend(ids)

    if not doc_ids_trovati:
        return []

    visti = set()
    doc_ids_unici = [x for x in doc_ids_trovati if not (x in visti or visti.add(x))]

    try:
        risultato = collezione.get(ids=doc_ids_unici, include=["metadatas", "documents"])
    except Exception as e:
        print(f"[ATTENZIONE] Lookup fornitore fallito: {e}")
        return []

    stopwords = STOPWORDS_QUERY | {"1961", "dop", "igp", "bio"}
    alias_matched = {a for a in _ALIAS_FORNITORI if re.search(r"\b" + re.escape(a) + r"\b", query_lower)}
    alias_matched |= set(ANAGRAFICA.alias_trovati(user_query))
    tokens = [w for w in re.findall(r"[A-Za-z0-9àèéìòù]+", query_lower) if len(w) >= 3 and w not in stopwords and not any(w in a for a in alias_matched)]

    prodotti = []
    for doc_id, meta, doc_text in zip(
        risultato.get("ids", []), risultato.get("metadatas", []), risultato.get("documents", [])
    ):
        doc_lower = (doc_text + " " + meta.get("codice_prodotto", "")).lower()
        score = sum(1 for t in tokens if t in doc_lower) if tokens else 0
        prodotti.append((score, {
            "id": doc_id,
            "metadata": meta,
            "document": doc_text,
            "match_fornitore": True
        }))

    if tokens:
        prodotti.sort(key=lambda x: x[0], reverse=True)

    return [p[1] for p in prodotti[:max_risultati]]


def ricerca_vettoriale(collezione, query_embedding: list, n_risultati: int,
                       filtro_categoria: "str | None" = None,
                       filtro_reparto: "str | None" = None,
                       filtro_sottocategoria: "str | None" = None,
                       filtro_dieta: "str | None" = None,
                       escludi_reparti: "list | None" = None,
                       escludi_sottocategorie: "list | None" = None,
                       richiedi_uso: "str | None" = None) -> list:
    """Ricerca vettoriale standard su ChromaDB, esclusa la manutenzione interna
    (schede fornitore/logistica), con eventuale filtro su reparto, sottocategoria o categoria."""
    clausole = [
        {"reparto": {"$nin": REPARTI_ESCLUSI_DA_RAG}},
        {"categoria_prodotto": {"$nin": CATEGORIE_ESCLUSE_DA_RAG}}
    ]
    if filtro_sottocategoria:
        clausole.append({"sottocategoria": " ".join(filtro_sottocategoria.split()).upper()})
    elif filtro_reparto:
        clausole.append({"reparto": filtro_reparto})
    elif filtro_categoria:
        clausole.append({"categoria_prodotto": filtro_categoria})

    clausole.extend(_clausole_dieta_chroma(filtro_dieta))
    if escludi_reparti:
        clausole.append({"reparto": {"$nin": list(escludi_reparti)}})
    if escludi_sottocategorie:
        clausole.append({"sottocategoria": {"$nin": list(escludi_sottocategorie)}})
    if richiedi_uso:
        clausole.append({f"uso_{richiedi_uso}": True})  # solo catalogo_v2 (attributo derivato dall'LLM)
    where_filter = {"$and": clausole} if len(clausole) > 1 else clausole[0]

    try:
        risultati = collezione.query(
            query_embeddings=[query_embedding],
            n_results=n_risultati,
            where=where_filter,
        )
    except Exception as e:
        print(f"[ATTENZIONE] Ricerca vettoriale con where fallita: {e}, tento fallback senza filtro...")
        try:
            risultati = collezione.query(
                query_embeddings=[query_embedding],
                n_results=n_risultati,
            )
        except Exception as e2:
            print(f"[ATTENZIONE] Ricerca vettoriale fallita del tutto: {e2}")
            return []

    record = []
    if risultati.get("ids") and risultati["ids"][0]:
        for doc_id, meta, doc_text in zip(
            risultati["ids"][0], risultati["metadatas"][0], risultati["documents"][0]
        ):
            if meta.get("reparto") in REPARTI_ESCLUSI_DA_RAG:
                continue
            record.append({"id": doc_id, "metadata": meta, "document": doc_text, "match_esatto": False})
    return record
