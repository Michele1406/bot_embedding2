from core.agent_topology import IntentClassification
from core.domain_rules import check_board_violations, prodotto_appartiene_a_famiglia, INCOMPATIBILITY_MATRIX
"""
retrieval_utils.py
====================
Logica di recupero condivisa tra app.py (server web) e main_chatbot_v2.py (CLI).

RICERCA IBRIDA in tre livelli, sempre combinati:
1. Match ESATTO su codice prodotto (se la query contiene un codice noto).
2. Match su fornitore/brand (se la query nomina un produttore o un suo alias
   inequivocabile — es. "Farino", "De Giorgi").
3. Ricerca vettoriale semantica su ChromaDB per tutto il resto.

NOTA DI DESIGN (importante, leggi prima di aggiungere nuove regole):
Questo file un tempo conteneva anche un "classificatore di intent" per
parole chiave (tagliere, aperitivo, cucina pugliese, cucina romana, ecc.)
che filtrava e forzava fornitori "prioritari" per ciascuna categoria.
È stato rimosso perché causava due problemi seri, verificati sulle chat
reali del bot:
  1. Suggeriva sempre gli stessi ~10 fornitori (quelli inseriti a mano nelle
     liste "fornitori_prioritari"), indipendentemente da cosa chiedesse
     davvero il cliente — un bias di business hardcoded nel codice, non un
     comportamento emergente dell'IA.
  2. Scartava dal contesto RAG prodotti validi solo perché il loro testo
     conteneva una parola nella blocklist ("crema di", "julienne", ecc.),
     facendo sì che il bot dicesse "non ce l'ho" su prodotti realmente a
     catalogo, semplicemente perché il filtro li aveva già buttati via
     prima che il modello li vedesse.
Se in futuro serve davvero escludere una categoria di prodotti da un
contesto specifico, è molto più sicuro farlo con un'istruzione in linguaggio
naturale nel system prompt (che il modello applica con buon senso, caso per
caso) piuttosto che con una blocklist di sottostringhe nel codice (che non
distingue il contesto ed è un elenco infinito da manutenere).
"""

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
        dati = collezione.get(include=["metadatas"])
    except Exception as e:
        print(f"[ATTENZIONE] Impossibile costruire l'indice fornitori: {e}")
        return indice

    for doc_id, meta in zip(dati.get("ids", []), dati.get("metadatas", [])):
        cat = str(meta.get("categoria_prodotto", ""))
        rep = str(meta.get("reparto", ""))
        if cat in CATEGORIE_ESCLUSE_DA_RAG or rep in REPARTI_ESCLUSI_DA_RAG:
            continue
        fornitore = str(meta.get("nome_fornitore", "")).strip()
        if fornitore and fornitore.upper() != "NAN":
            key = fornitore.lower()
            indice.setdefault(key, []).append(doc_id)
    print(f"[INFO] Indice fornitori costruito: {len(indice)} fornitori mappati.")
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
        prima_linea = doc.splitlines()[0] if doc else ""
        titolo_pulito = prima_linea.replace("\ufeff", "").lower()
        prodotti_memoria.append({
            "id": doc_id,
            "metadata": meta,
            "document": doc,
            "titolo_lower": titolo_pulito,
        })
    print(f"[INFO] Indice testuale catalogo costruito: {len(prodotti_memoria)} prodotti indicizzati in memoria.")
    return prodotti_memoria


def trova_match_lessicale(query: str, indice_testuale: list, max_risultati: int = 5,
                          categoria_filtro: str = "", max_per_fornitore: int = 2) -> list:
    """Cerca match diretti per parola chiave nel nome del prodotto.
    Garantisce che termini specifici (es. 'taralli', 'olive', 'nocciole', 'anacardi', 'bottarga')
    restituiscano sempre i prodotti reali, diversificando per fornitore per non monopolizzare i risultati."""
    if not indice_testuale or not query:
        return []

    STOPWORDS = {"cosa", "come", "dove", "quando", "vorrei", "voglio", "abbiamo", "avete", "servono", "consigli", "consigliami", "buono", "buona", "bello", "bella", "prodotti", "prodotto", "piatto", "piatti"}
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
        punteggio = sum(1 for t in tokens if t in titolo)
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
    "burro": ["montanari & gruzza", "la casera"],
}


def trova_match_per_fornitore(user_query: str, indice_fornitori: dict, collezione, max_risultati: int = 10) -> list:
    """Se la query nomina esplicitamente un brand/fornitore (o un suo alias
    inequivocabile), recupera direttamente i suoi prodotti."""
    if not indice_fornitori:
        return []

    query_lower = user_query.lower()
    doc_ids_trovati = []

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

    stopwords = {"cosa", "come", "dove", "quando", "vorrei", "voglio", "abbiamo", "avete", "servono", "consigli", "consigliami", "buono", "buona", "bello", "bella", "prodotti", "prodotto", "piatto", "piatti", "1961", "dop", "igp", "bio"}
    alias_matched = {a for a in _ALIAS_FORNITORI if re.search(r"\b" + re.escape(a) + r"\b", query_lower)}
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
                       filtro_sottocategoria: "str | None" = None) -> list:
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


_PAROLE_NEGAZIONE = ("senza", "privo", "prive", "assente", "assenti", "0%", "zero", "no ")


def _termine_presente_non_negato(testo_lower: str, termine: str) -> bool:
    """Come 'termine in testo_lower', ma ignora i casi in cui il termine è
    preceduto da una negazione a breve distanza (es. 'senza carne', '0%
    lattosio'), per evitare di escludere per errore prodotti che dichiarano
    esplicitamente l'ASSENZA di un ingrediente invece della sua presenza."""
    for m in re.finditer(re.escape(termine), testo_lower):
        contesto_prima = testo_lower[max(0, m.start() - 25):m.start()]
        if any(neg in contesto_prima for neg in _PAROLE_NEGAZIONE):
            continue
        return True
    return False


def formatta_proposta_ricetta(template_scelto, slot_riempiti):
    return {
        "template": template_scelto,
        "slot": slot_riempiti
    }

def componi_proposta_da_ricettario(richiesta_cliente: str, tipo_locale: "str | None", collezione_ricette,
                                    collezione_prodotti, indice_codici: dict, embedder,
                                    indice_fornitori: "dict | None" = None,
                                    indice_testuale: "list | None" = None,
                                    target_salumi: "int | None" = None,
                                    target_formaggi: "int | None" = None,
                                    prodotti_gia_proposti: "list | None" = None,
                                    piano_ricerca: "dict | None" = None,
                                    query_completa: str = "",
                                    prodotti_esclusi: set = None,
                                    filtro_dieta: str = None,
                                    ricette_escluse: set = None,
                                    categoria_ereditata: str = None,
                                    piatto_precedente_nome: str = None) -> str:
    """Implementa la pipeline RAG per il Ricettario (vedi documentazione in
    docs/struttura_rag_ricettario.md per i dettagli implementativi e il
    funzionamento del ciclo di selezione e arricchimento)."""
    
    prodotti_esclusi = set(prodotti_gia_proposti) if prodotti_gia_proposti else set()
    
    # Se il piano ricerca definisce quantità forzate globali per i taglieri, usale
    if piano_ricerca and piano_ricerca.get("intento") == "tagliere_o_ricetta":
        for comp in piano_ricerca.get("componenti", []):
            if comp.get("ruolo") == "salumi" and comp.get("quantita_target") is not None:
                target_salumi = int(comp.get("quantita_target"))
            if comp.get("ruolo") == "formaggi" and comp.get("quantita_target") is not None:
                target_formaggi = int(comp.get("quantita_target"))
                
    target_salumi = target_salumi if target_salumi is not None else 3
    target_formaggi = target_formaggi if target_formaggi is not None else 3

    # Step 1: Trova il miglior template
    query_ibrida = f"{richiesta_cliente} {tipo_locale or ''}"
    
    try:
        q_emb = embedder.get_embedding(query_ibrida)
        res = collezione_ricette.query(
            query_embeddings=[q_emb],
            n_results=10,
            include=["metadatas"]
        )
        risultati_ricettario = []
        for d_id, meta in zip(res["ids"][0], res["metadatas"][0]):
            risultati_ricettario.append({
                "id_ricetta": d_id,
                "nome_piatto": meta.get("nome_piatto", "Senza Nome"),
                "categoria": meta.get("categoria", "sconosciuta"),
                "slot": json.loads(meta.get("slot", "[]")),
                "canali_sconsigliati": json.loads(meta.get("canali_sconsigliati", "[]"))
            })
    except Exception as e:
        print(f"Errore ricerca ricette: {e}")
        risultati_ricettario = []

    if not risultati_ricettario:
        print(f"[DEBUG RAG] Richiesta '{richiesta_cliente}' NON PERTINENTE al ricettario. Fallback al catalogo.")
        return ""

    template_scelto = None
    for r in risultati_ricettario:
        if ricette_escluse and r["id_ricetta"] in ricette_escluse:
            continue
        if tipo_locale and tipo_locale in r.get("canali_sconsigliati", []):
            continue
        if categoria_ereditata and r.get("categoria") != categoria_ereditata:
            continue
        template_scelto = r
        break
        
    if not template_scelto:
        for r in risultati_ricettario:
            if ricette_escluse and r["id_ricetta"] in ricette_escluse:
                continue
            if tipo_locale and tipo_locale in r.get("canali_sconsigliati", []):
                continue
            template_scelto = r
            break
            
    if not template_scelto:
        template_scelto = risultati_ricettario[0]

    # Step 2: Riempi gli slot (qui c'è la ricerca ibrida vera e propria)
    
    # 3 retries for Constraint Solver
    violations = []
    for attempt in range(3):
        slot_riempiti = riempi_slot_ricetta(
            template_scelto,
            collezione_prodotti,
            indice_codici,
            embedder,
            indice_fornitori,
            indice_testuale,
            target_salumi=target_salumi,
            target_formaggi=target_formaggi,
            query_utente=richiesta_cliente,
            prodotti_esclusi=prodotti_esclusi,
            piano_ricerca=piano_ricerca
        )

        # Check violations
        board_products = [s["prodotto_trovato"] for s in slot_riempiti if s.get("prodotto_trovato")]
        violations = check_board_violations(board_products, allow_terra_mare=True)
        if not violations:
            break

        print(f"[CONSTRAINT SOLVER] Violazioni rilevate (Attempt {attempt+1}): {violations}")
        # FIX: prima escludevamo TUTTI i prodotti del board (anche quelli senza
        # nessun problema, es. pane/olive già corretti), buttando via lavoro
        # buono e rischiando comunque di ripescare la stessa combinazione
        # sbagliata al giro dopo perché l'esclusione non era mirata alla
        # famiglia incriminata. Ora escludiamo solo i prodotti effettivamente
        # coinvolti nelle violazioni rilevate (quelli citati nel messaggio di
        # errore), lasciando intatte le altre scelte già corrette.
        nomi_incriminati = set()
        for v in violations:
            if "referenze coinvolte:" in v:
                elenco = v.split("referenze coinvolte:")[-1].strip(" )")
                nomi_incriminati.update(n.strip().lower() for n in elenco.split(","))
        if nomi_incriminati:
            for prod in board_products:
                doc = prod.get("document", "") or ""
                nome = doc.splitlines()[0].strip().lower() if doc else ""
                if nome in nomi_incriminati:
                    prodotti_esclusi.add(prod["id"])
        else:
            # Fallback: se per qualche motivo non si riescono a isolare i nomi
            # (es. regola TERRA_MARE_MIX, che non ne elenca), escludiamo
            # comunque tutto il board come rete di sicurezza (comportamento
            # precedente), meglio ripartire da capo che restare bloccati.
            for prod in board_products:
                prodotti_esclusi.add(prod["id"])

    if violations:
        # Rete di sicurezza finale: dopo 3 tentativi il board ha ancora
        # violazioni note. Prima venivano semplicemente ignorate e la proposta
        # usciva comunque come se fosse a posto. Ora lo segnaliamo esplicitamente
        # nel testo che arriva al modello, così Nino può almeno scegliere di
        # ammorbidire la proposta o avvisare il cliente, invece di presentare
        # con sicurezza una combinazione che il sistema stesso sa essere
        # discutibile.
        print(f"[CONSTRAINT SOLVER] Esauriti i tentativi con violazioni residue: {violations}")
        template_scelto = dict(template_scelto)
        nota_esistente = template_scelto.get("note_composizione", "") or ""
        template_scelto["note_composizione"] = (
            nota_esistente
            + " [AVVISO INTERNO NON DA MOSTRARE AL CLIENTE: la combinazione proposta potrebbe avere una "
            + "ripetizione di texture/famiglia non ideale (" + "; ".join(violations) + "). "
            + "Se puoi, presenta la proposta con più cautela o offri di modificarla su richiesta.]"
        )

    return formatta_proposta_ricetta(template_scelto, slot_riempiti)


def cerca_prodotti(collezione, indice_codici, embedder, user_query: str, n_risultati: int,
                    indice_fornitori: "dict | None" = None,
                    indice_testuale: "list | None" = None,
                    filtro_categoria: "str | None" = None,
                    filtro_reparto: "str | None" = None,
                    filtro_sottocategoria: "str | None" = None,
                    tipo_locale: "str | None" = None,
                    filtro_dieta: "str | None" = None,
                    canale_locale: "str | None" = None,
                    esclusioni: "list | None" = None) -> list:
    """Punto di ingresso unico: combina match esatti per codice + match per
    fornitore + match lessicale per nome + ricerca vettoriale, deduplicando per id (il match esatto ha
    sempre precedenza), con un cap per fornitore adattivo (non hardcoded per singola categoria/brand:
    si allarga da solo quando una nicchia merceologica ha pochi fornitori a catalogo), supporto a
    filtro_reparto, filtro_sottocategoria, filtro dietetico deterministico (vegano/vegetariano) con
    gestione delle negazioni, esclusione basi precotte per pizzerie e boost di coerenza HORECA/RETAIL
    in base al canale del cliente (canale_locale: 'horeca' | 'retail' | 'misto', vedi profilazione_locale.py)."""
    esatti = trova_match_esatti_per_codice(user_query, indice_codici, collezione)
    id_visti = {r["id"] for r in esatti}

    fornitori_match = []
    if indice_fornitori:
        for r in trova_match_per_fornitore(user_query, indice_fornitori, collezione, max_risultati=n_risultati):
            if r["id"] not in id_visti:
                fornitori_match.append(r)
                id_visti.add(r["id"])

    query_lower = user_query.lower()
    is_beer_query = bool(re.search(r"\bbirr[ae]\b", query_lower))
    is_jam_query = bool(re.search(r"\b(marmellat[ae]|confettur[ae]|mostard[ae]|compost[ae])\b", query_lower))
    is_spice_query = bool(re.search(r"\b(spezi[ae]|arom[ai]|erbe aromatiche|condiment[oi]|boschi|rub|pepe|peperoncino|origano|rosmarino)\b", query_lower))

    lessicali_match = []
    if indice_testuale:
        max_forn_less = 8 if (is_beer_query or is_jam_query or is_spice_query) else 2
        for r in trova_match_lessicale(user_query, indice_testuale, max_risultati=n_risultati, max_per_fornitore=max_forn_less):
            if r["id"] not in id_visti:
                lessicali_match.append(r)
                id_visti.add(r["id"])

    if filtro_sottocategoria:
        sc_lower = filtro_sottocategoria.lower()
        esatti = [r for r in esatti if sc_lower == str(r["metadata"].get("sottocategoria", "")).lower()]
        fornitori_match = [r for r in fornitori_match if sc_lower == str(r["metadata"].get("sottocategoria", "")).lower()]
        lessicali_match = [r for r in lessicali_match if sc_lower == str(r["metadata"].get("sottocategoria", "")).lower()]
    elif filtro_reparto:
        rep_lower = filtro_reparto.lower()
        esatti = [r for r in esatti if rep_lower == str(r["metadata"].get("reparto", "")).lower()]
        fornitori_match = [r for r in fornitori_match if rep_lower == str(r["metadata"].get("reparto", "")).lower()]
        lessicali_match = [r for r in lessicali_match if rep_lower == str(r["metadata"].get("reparto", "")).lower()]
    elif filtro_categoria:
        cat_lower = filtro_categoria.lower()
        esatti = [r for r in esatti if cat_lower in str(r["metadata"].get("categoria_prodotto", "")).lower() or cat_lower in str(r["metadata"].get("categoria_tassonomia", "")).lower() or cat_lower in str(r["metadata"].get("reparto", "")).lower()]
        fornitori_match = [r for r in fornitori_match if cat_lower in str(r["metadata"].get("categoria_prodotto", "")).lower() or cat_lower in str(r["metadata"].get("categoria_tassonomia", "")).lower() or cat_lower in str(r["metadata"].get("reparto", "")).lower()]
        lessicali_match = [r for r in lessicali_match if cat_lower in str(r["metadata"].get("categoria_prodotto", "")).lower() or cat_lower in str(r["metadata"].get("categoria_tassonomia", "")).lower() or cat_lower in str(r["metadata"].get("reparto", "")).lower()]

    try:
        query_embedding = embedder.embed_query(user_query)
        vettoriali = ricerca_vettoriale(
            collezione, query_embedding, n_risultati * 2,
            filtro_categoria=filtro_categoria,
            filtro_reparto=filtro_reparto,
            filtro_sottocategoria=filtro_sottocategoria,
        )
    except Exception as e:
        print(f"[ATTENZIONE] Ricerca vettoriale fallita: {e}")
        vettoriali = []

    combinati = list(esatti) + fornitori_match + lessicali_match
    
    # FAIRNESS ALGORITHM: Limitiamo la predominanza di un singolo fornitore
    # nei risultati vettoriali. Soft cap a 5 prodotti per fornitore.
    vettoriali_fair = []
    vettoriali_overflow = []
    conteggio_fornitori_vett = {}
    
    for r in vettoriali:
        f = str(r["metadata"].get("nome_fornitore", "")).strip().lower()
        if f and conteggio_fornitori_vett.get(f, 0) >= 5:
            vettoriali_overflow.append(r)
        else:
            if f:
                conteggio_fornitori_vett[f] = conteggio_fornitori_vett.get(f, 0) + 1
            vettoriali_fair.append(r)
            
    vettoriali_riordinati = vettoriali_fair + vettoriali_overflow

    for r in vettoriali_riordinati:
        if r["id"] not in id_visti:
            combinati.append(r)
            id_visti.add(r["id"])

    # FILTRO DIETETICO STRUTTURATO SU REPARTO (VEGANO / VEGETARIANO)
    # Priorità al campo strutturato vegano/vegetariano (SI/NO), che è il dato
    # affidabile. Il controllo testuale interviene SOLO come rete di sicurezza
    # per i reparti non categoricamente esclusi (es. un ragù di verdure nel
    # reparto Dispensa) ed è negation-aware: "senza carne" non fa più
    # scattare l'esclusione per la presenza della parola "carne".
    if filtro_dieta:
        fd_lower = filtro_dieta.lower()
        filtrati_dieta = []
        for r in combinati:
            rep = str(r["metadata"].get("reparto", "")).upper()
            doc_basso = r.get("document", "").lower()
            is_veg = str(r["metadata"].get("vegano", "")).upper()
            is_vgt = str(r["metadata"].get("vegetariano", "")).upper()

            if fd_lower == "vegano":
                if rep in ["CARNE", "SALUMI", "FORMAGGI", "MARE"]:
                    continue
                if is_veg.startswith("NO") and any(
                    _termine_presente_non_negato(doc_basso, t)
                    for t in ("carne", "latte", "formaggio", "uov", "miele", "strutto")
                ):
                    continue
            elif fd_lower == "vegetariano":
                if rep in ["CARNE", "SALUMI", "MARE"]:
                    continue
                if is_vgt.startswith("NO") and any(
                    _termine_presente_non_negato(doc_basso, t)
                    for t in ("carne", "pesce", "strutto", "tonno", "acciug")
                ):
                    continue
            elif fd_lower == "senza_glutine":
                is_sg = str(r["metadata"].get("senza_glutine", "")).upper()
                if is_sg.startswith("NO"):
                    # Filtro molto stringente sui reparti a rischio (pasta, pane, dolci, panati)
                    # a meno che non ci sia scritto esplicitamente "senza glutine" nel testo
                    if rep in ["PANIFICATI E SOSTITUTIVI", "PASTA", "PASTA FRESCA", "DOLCI E DESSERT"] and not any(w in doc_basso for w in ["senza glutine", "gluten free"]):
                        continue
                    # E una blacklist di parole nel documento
                    if any(_termine_presente_non_negato(doc_basso, t) for t in ("glutine", "frumento", "farina di grano", "orzo", "farro", "kamut", "seitan", "pangrattato", "panat", "infarinat")):
                        continue
            filtrati_dieta.append(r)
        combinati = filtrati_dieta

    # FILTRO CANALE PIZZERIA: le pizzerie NON comprano basi precotte Pinsa/Pizza/Padellino Farino
    if tipo_locale and tipo_locale.lower() == "pizzeria":
        filtrati_pizzeria = []
        for r in combinati:
            sc = str(r["metadata"].get("sottocategoria", "")).lower()
            cod = str(r["metadata"].get("codice_prodotto", "")).upper()
            doc_basso = r.get("document", "").lower()
            if sc == "basi per pizza e impasti" or cod == "FARINO10" or any(p in doc_basso for p in ["base pinsa", "base pizza", "base padellino", "pinsa precotta"]):
                continue
            filtrati_pizzeria.append(r)
        combinati = filtrati_pizzeria

    # FILTRO CANALE BAR: niente pasta cruda da cuocere o carni crude da macelleria
    if tipo_locale and tipo_locale.lower() == "bar" and not any(k in query_lower for k in ["pasta", "primo", "carne", "cucina"]):
        filtrati_bar = []
        for r in combinati:
            rep = str(r["metadata"].get("reparto", "")).upper()
            sc = str(r["metadata"].get("sottocategoria", "")).lower()
            doc_basso = r.get("document", "").lower()
            if rep == "CARNE" and any(w in doc_basso for w in ["coscia da battere", "trita", "macinato", "tagliata"]):
                continue
            if sc in ["pasta secca di semola", "pasta fresca", "pasta integrale e speciali"]:
                continue
            filtrati_bar.append(r)
        combinati = filtrati_bar

    # REGOLA RISOTTO ALL'ONDA E MANTECATURA: escludi Riso Nero / Riso Venere e dai priorità a Carnaroli
    if "risotto" in query_lower:
        filtrati_risotto = []
        for r in combinati:
            doc_basso = r.get("document", "").lower()
            if any(k in doc_basso for k in ["riso nero", "riso venere", "integrale nero"]):
                continue
            filtrati_risotto.append(r)
        combinati = filtrati_risotto

    # BOOST DI COERENZA CANALE (HORECA / RETAIL)
    # Sostituisce il vecchio boost "solo horeca per la ristorazione": ora vale
    # anche il caso inverso (bottega/negozio -> formati retail). È un boost di
    # ordinamento, non un filtro: se per un prodotto esiste solo un formato,
    # resta comunque proponibile a qualsiasi canale.
    if canale_locale in ("horeca", "retail"):
        def punteggio_canale(r):
            formato = rileva_formato_prodotto(r.get("document", ""))
            return punteggio_coerenza_canale(canale_locale, formato)
        combinati.sort(key=punteggio_canale, reverse=True)

    # BOOST INTELLIGENTE SPEZIE BOSCHI SULLA BASE DEI METADATI GASTRONOMICI
    if is_spice_query:
        target_field = None
        if any(w in query_lower for w in ["pesce", "marinara", "mare", "tonno", "salmone", "crostacei", "polpo", "spigola", "orata"]):
            target_field = "buono_per_pesce"
        elif any(w in query_lower for w in ["carne", "manzo", "maiale", "bistecca", "tagliata", "pollo", "arrosto", "bbq", "grigliat", "rub", "hamburger", "burger"]):
            target_field = "buono_per_carne"
        elif any(w in query_lower for w in ["patat", "chips", "fritt"]):
            target_field = "buono_per_patate"
        elif any(w in query_lower for w in ["pasta", "primi", "spaghett", "risott", "sugo", "ragù", "soffritt"]):
            target_field = "buono_per_primi"
        elif any(w in query_lower for w in ["pizza", "pinsa", "focacc", "bruschett"]):
            target_field = "buono_per_pizza"
        elif any(w in query_lower for w in ["verdur", "insalat", "zupp", "legum"]):
            target_field = "buono_per_verdure"
        elif any(w in query_lower for w in ["dolc", "dessert", "torta"]):
            target_field = "buono_per_dolci"

        def punteggio_spezia(r):
            meta = r.get("metadata", {})
            if str(meta.get("codice_fornitore", "")) == "19010829" or "boschi" in str(meta.get("nome_fornitore", "")).lower():
                if target_field:
                    val = meta.get(target_field)
                    if val is True or str(val).lower() == "true":
                        if target_field == "buono_per_carne" and (meta.get("buono_per_bbq") is True or str(meta.get("buono_per_bbq", "")).lower() == "true"):
                            return 15
                        return 12
                    return 2
                return 5
            return 0
        combinati.sort(key=punteggio_spezia, reverse=True)
    # FILTRO ESCLUSIONI LESSICALI
    if esclusioni:
        filtrati_esclusioni = []
        for r in combinati:
            # Controllo nome prodotto, ingredienti, note e sottocategoria
            doc_basso = r.get("document", "").lower()
            sottocategoria = str(r["metadata"].get("sottocategoria", "")).lower()
            
            # Se ALMENO UNA parola dell'esclusione è presente nel testo o nella sottocategoria, salta il prodotto
            deve_escludere = False
            for escl in esclusioni:
                escl = escl.lower().strip()
                # Cerchiamo l'esclusione come parola intera per evitare falsi positivi
                if re.search(r'\b' + re.escape(escl) + r'\b', doc_basso) or re.search(r'\b' + re.escape(escl) + r'\b', sottocategoria):
                    deve_escludere = True
                    break
            
            if not deve_escludere:
                filtrati_esclusioni.append(r)
        combinati = filtrati_esclusioni

    # Cap per fornitore (varietà), ADATTIVO: se per questa ricerca la
    # categoria è coperta da pochi fornitori (es. un solo fornitore fa birra,
    # o marmellate, o spezie), il cap si allarga automaticamente invece di
    # richiedere di elencare a mano i nomi dei fornitori "speciali". Così se
    # domani entra o esce un fornitore di birra dal catalogo, il comportamento
    # si aggiorna da solo, senza toccare il codice.
    fornitori_distinti = {r["metadata"].get("nome_fornitore", "") for r in combinati}
    if 0 < len(fornitori_distinti) <= 2:
        cap_categoria_di_nicchia = min(max(n_risultati, MAX_PRODOTTI_PER_FORNITORE), 12)
    else:
        cap_categoria_di_nicchia = MAX_PRODOTTI_PER_FORNITORE

    conteggio_fornitori = {}
    risultati_diversificati = []
    for r in combinati:
        fornitore = r["metadata"].get("nome_fornitore", "")
        conteggio_fornitori[fornitore] = conteggio_fornitori.get(fornitore, 0) + 1

        max_forn = cap_categoria_di_nicchia
        if r.get("match_esatto") or r.get("match_fornitore"):
            max_forn = max(max_forn, 8)

        if r.get("match_esatto") or conteggio_fornitori[fornitore] <= max_forn:
            risultati_diversificati.append(r)

    return risultati_diversificati[:n_risultati]


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
    s = re.sub(r'\s+', ' ', s).strip()

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


def costruisci_contesto_testuale(record_prodotti: list, id_gia_mostrati: "set | None" = None) -> str:
    """Trasforma la lista di record in blocco di testo per il prompt RAG."""
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
            prima_linea = r['document'].splitlines()[0] if r.get('document') else ""
            nome_pulito = pulisci_nome_commerciale(prima_linea, meta.get('nome_fornitore', ''))
            tag_match = "[MATCH ESATTO SU CODICE PRODOTTO] " if r.get("match_esatto") else ""
            tag_gia_visto = " [GIÀ MENZIONATO IN PRECEDENZA]" if r["id"] in id_gia_mostrati else ""
    
            blocco = f"\n--- MATCH {idx_totale} (ID: {r['id']}){tag_gia_visto} ---\n"
            blocco += f"{tag_match}Prodotto: {nome_pulito} (Produttore: {meta.get('nome_fornitore')})\n"
            blocco += f"Reparto: {meta.get('reparto', 'N/A')} | Categoria: {meta.get('categoria_tassonomia', meta.get('categoria_prodotto'))} | Sottocategoria: {meta.get('sottocategoria', 'N/A')} | Codice: {meta.get('codice_prodotto')}\n"
            blocco += f"Varianti: {meta.get('varianti_prodotto')}\n"
    
            percorso_img = str(meta.get("percorso_immagine", "")).strip()
            ha_img_reale = (
                meta.get("ha_immagine_primaria")
                and percorso_img
                and percorso_img.lower() not in ("", "nan", "none", "false")
                and os.path.exists(percorso_img)
            )
            if ha_img_reale:
                blocco += f"Percorso File Immagine: {percorso_img}\n"
            else:
                blocco += "Immagine: NESSUNA FOTO A CATALOGO (NON inserire tag [IMG] per questo)\n"
    
            blocco += f"Scheda: {r['document']}\n"
            blocchi.append(blocco)

    # Aggiungo la lista riassuntiva dei fornitori disponibili
    fornitori_presenti = set()
    for r in record_ordinati:
        f = str(r["metadata"].get("nome_fornitore", "")).strip().title()
        if f and f.lower() not in ("sconosciuto", "nan", "none", "azienda agricola", "null"):
            fornitori_presenti.add(f)
            
    if fornitori_presenti:
        blocchi.append(f"\n[FORNITORI DISPONIBILI RILEVATI IN QUESTO CONTESTO: {', '.join(sorted(fornitori_presenti))}]\n")

    return "\n".join(blocchi)


# ====================================================================
# MODULO RICETTARIO E COMPOSIZIONE GASTRONOMICA (TASK 2)
# ====================================================================
NOME_COLLEZIONE_RICETTE = "ricette_sofood"
SOGLIA_DISTANZA_SOSTITUTO = 0.35  # oltre questa distanza, meglio omettere che forzare un match debole


def _embed_testo_query(embedder, testo: str) -> list:
    """Helper compatibile con EmbedderMultimodaleGemini e EmbedderGemini."""
    if hasattr(embedder, "embed_query"):
        return embedder.embed_query(testo)
    elif hasattr(embedder, "embed_documento"):
        return embedder.embed_documento(testo)
    raise AttributeError("L'embedder fornito non possiede né embed_query né embed_documento.")


SOGLIA_DISTANZA_MAX_TEMPLATE = 0.72  # oltre questa distanza il match è rumoroso e non pertinente


def trova_template_ricetta(collezione_ricette, embedder, richiesta_cliente: str,
                            tipo_locale: "str | None" = None, n_candidati: int = 4,
                            filtro_dieta: "str | None" = None,
                            ricette_escluse: "set | list | None" = None,
                            categoria_ereditata: "str | None" = None,
                            piatto_precedente_nome: "str | None" = None) -> list:
    """
    Cerca il/i template di ricetta più vicini alla richiesta del cliente.
    Il filtro di canale si applica QUI (selezione del template), non sui
    singoli prodotti del RAG generico altrove nel codice — vedi Guardrail.
    Regola Pizzerie: le pizzerie NON usano basi pinsa o padellino precotte!
    """
    richiesta_lower = richiesta_cliente.lower()
    embedding = _embed_testo_query(embedder, richiesta_cliente)

    # Identificazione portata/categoria richiesta dall'utente
    portate_target = None
    if any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["tagliere", "taglieri"]):
        portate_target = {"tagliere"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["risotto", "riso"]):
        portate_target = {"risotto"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in [
        "primo piatto", "primi piatti", "un primo", "i primi", "come primo", "pasta", "spaghetti", "spaghettone", "paccheri",
        "rigatoni", "gnocchi", "fregola", "calamarata", "orecchiette", "linguine", "fusilli", "scialatielli"
    ]) or (re.search(r"\bprimo\b", richiesta_lower) and not re.search(r"\b(dal|del|al|nel|col|questo|quel)\s+primo\b", richiesta_lower)):
        portate_target = {"primo", "risotto"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["secondo", "secondi"]):
        portate_target = {"secondo"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["antipasto", "antipasti", "entrée", "entree"]):
        portate_target = {"antipasto"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["dolce", "dolci", "dessert"]):
        portate_target = {"dolce"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["contorno", "contorni"]):
        portate_target = {"contorno"}
    elif bool(re.search(r"\b(?:panin[oi]|burger|hamburg\w*)\b", richiesta_lower)):
        if not any(k in richiesta_lower for k in ["al piatto", "secondo piatto", "senza pane", "senza bun"]):
            portate_target = {"panino"}
        else:
            portate_target = {"secondo"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["pizza", "pinsa", "padellino"]):
        portate_target = {"pizza"}
    elif categoria_ereditata:
        # Se l'utente non ha specificato una portata nuova esplicita, eredita la categoria precedente
        cat_ered = categoria_ereditata.lower().strip()
        if cat_ered in ["risotto", "riso"]:
            portate_target = {"risotto"}
        elif cat_ered == "primo" and piatto_precedente_nome and any(k in piatto_precedente_nome.lower() for k in ["risotto", "riso"]):
            portate_target = {"risotto"}
        else:
            portate_target = {cat_ered}

    # Query ChromaDB con eventuale filtro where sulla categoria per massima precisione
    where_filter = None
    if portate_target:
        if len(portate_target) == 1:
            where_filter = {"categoria": list(portate_target)[0]}
        else:
            where_filter = {"categoria": {"$in": list(portate_target)}}

    try:
        if where_filter:
            risultati = collezione_ricette.query(query_embeddings=[embedding], n_results=35, where=where_filter)
        else:
            risultati = collezione_ricette.query(query_embeddings=[embedding], n_results=40)
    except Exception as e:
        risultati = collezione_ricette.query(query_embeddings=[embedding], n_results=40)

    candidati = []
    if risultati.get("ids") and risultati["ids"][0]:
        for i, id_ricetta in enumerate(risultati["ids"][0]):
            if ricette_escluse and id_ricetta in ricette_escluse:
                continue

            meta = risultati["metadatas"][0][i]
            # Filtro unidirezionale: esclude solo i template esplicitamente sconsigliati per questo canale
            if tipo_locale and tipo_locale.lower() in str(meta.get("canali_sconsigliati", "")).lower():
                continue

            # Regola categorica per pizzerie: non proporre basi pinsa/padellino a una pizzeria
            nome_basso = meta["nome_piatto"].lower()
            if tipo_locale and tipo_locale.lower() == "pizzeria" and any(p in nome_basso for p in ["pinsa", "padellino"]):
                continue

            # Filtro per panini di pesce / tartare a crudo:
            # Proponili SOLO se l'utente ha esplicitamente richiesto pesce, mare, tonno, tartare, crudo, polpo, salmone, alici.
            # Per richieste generiche di panino/burger ("un panino", "un panino più semplice", "burger"), non proporre tartare di pesce
            is_panino_or_burger = bool(re.search(r"\b(?:panin[oi]|burger|hamburg\w*|sandwich)\b", richiesta_lower))
            ha_richiesta_pesce = any(w in richiesta_lower for w in ["tonno", "salmone", "pesce", "polpo", "spada", "gamber", "mare", "alici", "tartare", "crudo"])
            if is_panino_or_burger and not ha_richiesta_pesce:
                if any(w in nome_basso for w in ["tartare", "tonno", "polpo", "salmone", "pesce", "alici", "bottarga"]):
                    continue

            # Filtro dietetico sui template (vegano / vegetariano)
            if filtro_dieta and filtro_dieta.lower() == "vegano":
                cat_ricetta = meta.get("categoria", "").lower()
                ingr_raw = str(meta.get("ingredienti_json", "")).lower()
                parole_non_vegane = [
                    "carne", "salumi", "tagliere", "salame", "crudo", "pesce", "tonno", "alici", "gamberi",
                    "formaggio", "formaggi", "caciocavallo", "provola", "mozzarella", "burrata", "ricotta",
                    "pecorino", "parmigiano", "latte", "uovo", "uova", "lardo", "pancetta", "guanciale"
                ]
                if any(k in nome_basso for k in parole_non_vegane) or any(k in ingr_raw for k in parole_non_vegane):
                    continue

            distanza = risultati["distances"][0][i] if "distances" in risultati and risultati["distances"] else 0.0

            # Soglia di distanza semantica: scarta match spuri (>0.72) se non c'è corrispondenza testuale significativa
            tokens_richiesta = [t for t in re.findall(r"\w+", richiesta_lower) if len(t) > 3 and t not in ["formati", "formato", "confezioni", "confezione", "questi", "ingredienti", "avete"]]
            ha_match_lessicale = any(t in nome_basso or (len(t) >= 6 and t[:6] in nome_basso) for t in tokens_richiesta)
            if distanza > SOGLIA_DISTANZA_MAX_TEMPLATE and not ha_match_lessicale:
                continue

            candidati.append({
                "id_ricetta": id_ricetta,
                "nome_piatto": meta["nome_piatto"],
                "categoria": meta["categoria"],
                "note_composizione": meta.get("note_composizione", ""),
                "ingredienti": json.loads(meta["ingredienti_json"]),
                "distanza": distanza,
            })

    # Se è stata richiesta esplicitamente una portata, filtra rigorosamente le ricette della portata corretta
    if portate_target:
        candidati = [c for c in candidati if str(c.get("categoria", "")).lower() in portate_target]

    # Reranking selettivo su preparazioni specifiche o ingredienti protagonisti nominati dal cliente
    parole_focus = [
        "pinsa", "padellino", "maritozzo", "toast", "scrocchiarella", "burger", "hamburg", "paella",
        "polpo", "ricci", "riccio", "cozze", "vongole", "astice", "scampi", "gamberi", "tartufo",
        "carbonara", "amatriciana", "cacio e pepe", "totano", "seppia",
        "spagna", "spagnolo", "spagnola", "spagnoli", "spagnole", "tapas", "iberico", "iberica", "cecina", "bellota"
    ]
    focus_richiesto = next((p for p in parole_focus if (p in richiesta_lower or re.search(r"\b" + p, richiesta_lower))), None)
    if focus_richiesto:
        corrispondenti = [
            t for t in candidati
            if focus_richiesto in t["nome_piatto"].lower()
            or any(focus_richiesto in str(ing.get("INGREDIENTE_GENERICO", "")).lower() or focus_richiesto in str(ing.get("NOTE_INGREDIENTE", "")).lower() for ing in t.get("ingredienti", []))
        ]
        altri = [t for t in candidati if t not in corrispondenti]
        corrispondenti.sort(key=lambda x: (0 if str(x.get("id_ricetta", "")).startswith("SO_") else 1, x.get("distanza", 99.0)))
        altri.sort(key=lambda x: (0 if str(x.get("id_ricetta", "")).startswith("SO_") else 1, x.get("distanza", 99.0)))
        candidati = corrispondenti + altri
    else:
        candidati.sort(key=lambda x: (0 if str(x.get("id_ricetta", "")).startswith("SO_") else 1, x.get("distanza", 99.0)))

    return candidati[:n_candidati]


PAROLE_NUMERI = {
    "un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4,
    "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10
}


def _parse_numero_italiano(val_str: str) -> int:
    val_str = val_str.strip().lower()
    if val_str.isdigit():
        return int(val_str)
    return PAROLE_NUMERI.get(val_str, 1)


def estrai_conteggi_tagliere(testo: str, ha_gia_prodotti: bool = False) -> tuple[int, int]:
    """Estrae il numero target di salumi e formaggi desiderati per un tagliere.
    Ritorna: (target_salumi, target_formaggi)
    - Default tagliere generico se non specificato: (3, 3)
    - 'altri 2 salumi oltre questo e altrettanti formaggi' -> (2, 2) se ha già prodotti, altrimenti (3, 3)
    - 'degustazione con 5 formaggi diversi' -> (0, 5)
    - '4 salumi e 2 formaggi' -> (4, 2)
    - 'tagliere per il mio pub' -> (3, 3)
    """
    testo_basso = testo.lower()
    num_pattern = r"(?:\d+|un|uno|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)"

    target_salumi = None
    salumi_match = re.search(rf"(altri\s+)?({num_pattern})\s+(?:tipi\s+di\s+|referenze\s+di\s+|qualit[àa]\s+di\s+)?(?:salumi|salume|prosciutti|prosciutto)", testo_basso)
    if salumi_match:
        is_altri = bool(salumi_match.group(1))
        n = _parse_numero_italiano(salumi_match.group(2))
        if (is_altri or "oltre" in testo_basso) and not ha_gia_prodotti:
            n += 1
        target_salumi = n

    target_formaggi = None
    formaggi_match = re.search(rf"(altri\s+)?({num_pattern})\s+(?:tipi\s+di\s+|referenze\s+di\s+|qualit[àa]\s+di\s+)?(?:formaggi|formaggio|caci)", testo_basso)
    if formaggi_match:
        is_altri = bool(formaggi_match.group(1))
        n = _parse_numero_italiano(formaggi_match.group(2))
        if (is_altri or "oltre" in testo_basso) and not ha_gia_prodotti:
            n += 1
        target_formaggi = n

    if "altrettanti formaggi" in testo_basso or "altrettante referenze di formaggi" in testo_basso:
        target_formaggi = target_salumi if target_salumi is not None else 3
    elif "altrettanti salumi" in testo_basso:
        target_salumi = target_formaggi if target_formaggi is not None else 3

    ha_parole_salumi = bool(re.search(r"\b(salum[ie]|prosciutt[ie]|insaccat[ie]|speck|lardo|pancett[ae]|capocoll[oi]|bresaol[ae])\b", testo_basso))
    ha_parole_formaggi = bool(re.search(r"\b(formagg[ie]|caci[oi]|pecorin[oi]|parmigian[oi]|mozzarell[ae]|burrat[ae])\b", testo_basso))

    if ha_parole_formaggi and not ha_parole_salumi:
        if target_formaggi is None:
            target_formaggi = 3
        target_salumi = 0
    elif ha_parole_salumi and not ha_parole_formaggi:
        if target_salumi is None:
            target_salumi = 3
        target_formaggi = 0

    if target_salumi is None and target_formaggi is None:
        target_salumi = 3
        target_formaggi = 3
    else:
        if target_salumi is None:
            target_salumi = 3
        if target_formaggi is None:
            target_formaggi = 3

    return (target_salumi, target_formaggi)


PAROLE_PLURALI_SLOT = ["misti", "miste", "assortiti", "assortite", "selezione", "mix"]
N_ESPANSIONE_PLURALE = 3  # quanti prodotti distinti generare da uno slot plurale


# ====================================================================
# CLUSTER REGIONALI E TERRITORIALI PER TAGLIERI E MENU
# ====================================================================
def rileva_cluster_regionale(testo: str) -> "str | None":
    """Rileva se il testo fa riferimento a una specifica regione o paese
    (Spagna, Toscana, Puglia, ecc.), in base alle parole chiave dichiarate in
    fornitori_config.TEMI_REGIONALI. Aggiungere o modificare una regione si fa
    SOLO in fornitori_config.py, non qui."""
    t_lower = testo.lower()
    for reg, cfg in TEMI_REGIONALI.items():
        parole = cfg.get("parole_chiave", [])
        if parole and any(re.search(r"\b" + re.escape(w), t_lower) for w in parole):
            return reg
    return None


# Esclusioni di QUALITA' (non di brand): prodotti che, per il loro impiego
# specifico, non stanno bene in un tagliere anche se appartengono al reparto
# giusto (es. una crema spalmabile fusa non va tra i formaggi da affettare).
# Questa è l'unica lista "a mano" rimasta: non contiene MAI nomi di fornitore,
# solo caratteristiche del prodotto, quindi non va toccata quando cambia il
# catalogo fornitori.
_QUALITA_ESCLUSIONI_TAGLIERE = {
    "formaggi": ("julienne", "cubettata", "per pizza", "latte ", "burro", "besciamella",
                 "cremoso", "yogurt", "tiramis", "cacao", "caramello", "dessert", "dolce",
                 "pasticceria", "spalmabile", "fuso", "crema di formaggio",
                 "mozzarella", "burrata", "treccia", "stracciatella", "ricotta",
                 "carpinello", "calugi", "salsa", "mousse"),
    "salumi": ("wurstel", "wǬrstel", "trita", "macinato", "hamburger"),
    "olive": ("sugo", "patǸ", "pate", "candit", "granell", "pastella", "crema", "carciof", "olio"),
}


def _fornitori_ammessi_per_slot(ruolo: str, regione: str) -> "list | None":
    """None = nessun vincolo (qualsiasi fornitore col ruolo giusto va bene).
    Lista (anche vuota) = solo i fornitori taggati per quel ruolo/regione in
    fornitori_config.py. Lista vuota = nessun fornitore disponibile: lo slot
    verrà saltato invece di proporre qualcosa fuori tema."""
    slugs = elenco_fornitori_per_ruolo_regione(ruolo, regione)
    if not slugs:
        return None if regione == "generico" else []
    nomi = []
    for slug in slugs:
        nomi.extend(FORNITORI[slug]["nomi_match"])
    return nomi


def _seleziona_componente_tagliere(ruolo: str, regione: str, collezione_prodotti, indice_codici,
                                    embedder, indice_fornitori, indice_testuale,
                                    filtro_reparto: "str | None" = None,
                                    filtro_categoria: "str | None" = None,
                                    filtro_sottocategoria: "str | None" = None,
                                    n_target: int = 1,
                                    query_utente: str = "",
                                    prodotti_esclusi: "set | None" = None,
                                    max_per_fornitore: int = 1,
                                    esclusioni_dinamiche: "list | None" = None,
                                    sottocategoria_forzata: "str | None" = None) -> list:
    """Motore GENERICO di selezione di una componente del tagliere (salumi,
    formaggi, mare, pane, olive, sottoli, mostarde_confetture, snack_secco,
    contorni), valido per QUALSIASI regione e QUALSIASI fornitore configurato
    in fornitori_config.py.

    Prima della riorganizzazione, ognuna di queste componenti aveva un blocco
    if/elif scritto a mano PER OGNI SINGOLA REGIONE (5-6 blocchi quasi
    identici, ripetuti per salumi, formaggi, pane, olive...): aggiungere un
    fornitore significava trovare e modificare il blocco giusto in mezzo a
    centinaia di righe. Ora basta aggiungere una entry in fornitori_config.py:
    questa funzione la trova e la usa automaticamente, per qualunque ruolo e
    qualunque regione, comprese quelle aggiunte in futuro."""
    n_pool = max(n_target * 5, 15)

    tema_cfg = TEMI_REGIONALI.get(regione) or TEMI_REGIONALI["generico"]
    testo_tema = tema_cfg.get(ruolo) or TEMI_REGIONALI["generico"].get(ruolo, ruolo)
    if not testo_tema:
        return []

    fornitori_ammessi = _fornitori_ammessi_per_slot(ruolo, regione)
    if fornitori_ammessi == []:
        return []

    testo_tema = f"{testo_tema} {query_utente}"

    candidati = cerca_prodotti(
        collezione_prodotti, indice_codici, embedder,
        testo_tema,
        n_risultati=n_pool,
        indice_fornitori=indice_fornitori,
        indice_testuale=indice_testuale,
        filtro_reparto=filtro_reparto,
        filtro_categoria=filtro_categoria,
        filtro_sottocategoria=filtro_sottocategoria,
        esclusioni=esclusioni_dinamiche
    )

    esclusioni = _QUALITA_ESCLUSIONI_TAGLIERE.get(ruolo, ())
    permetti_parole_esplicite = query_utente.lower()
    filtrati = []
    for r in candidati:
        doc_p = r["document"].splitlines()[0].lower() if r.get("document") else ""
        forn_p = str(r["metadata"].get("nome_fornitore", "")).lower()
        if esclusioni and any(w in doc_p for w in esclusioni) and not any(
            exp in permetti_parole_esplicite for exp in ("spalmabil", "crostin", "crema", "mozzarella", "burrata", "treccia", "stracciatella", "ricotta", "bufala")
        ):
            continue
        if ruolo == "salumi":
            rep_p = str(r["metadata"].get("reparto", "")).upper()
            if "sfilaccio" in doc_p and "sfilaccio" not in permetti_parole_esplicite:
                continue
            if rep_p == "CARNE" and not any(w in doc_p for w in ["carpaccio", "salada", "bresaola"]):
                continue
        if ruolo == "pane" and not any(k in doc_p for k in ["tarall", "grissin", "carasau", "guttiau", "frisell", "pane", "picos", "cracker"]):
            continue
        if ruolo == "olive" and "oliv" not in doc_p:
            continue
        if fornitori_ammessi is not None and not any(fa in forn_p for fa in fornitori_ammessi):
            continue
        filtrati.append(r)

    if prodotti_esclusi:
        non_mostrati = [r for r in filtrati if r["id"] not in prodotti_esclusi]
        if len(non_mostrati) >= n_target:
            filtrati = non_mostrati
        else:
            gia_mostrati = [r for r in filtrati if r["id"] in prodotti_esclusi]
            filtrati = non_mostrati + gia_mostrati

    n_forn_ruolo = len(elenco_fornitori_per_ruolo_regione(ruolo, regione)) or 99
    cap_forn = 3 if n_forn_ruolo <= 1 else max_per_fornitore
    conteggio_forn = {}
    conteggio_sottocategoria = {}
    selezionati = []

    for r in filtrati:
        f = r["metadata"].get("nome_fornitore", "").lower()
        sc = str(r["metadata"].get("sottocategoria", "")).lower()

        # Chiave di diversità: FAMIGLIA gastronomica (INCOMPATIBILITY_MATRIX di
        # domain_rules.py) se il prodotto appartiene a una famiglia nota (es.
        # "Finocchiona" e "Salame Toscano" sono entrambi SALUMI_MACINATI anche
        # se la loro sottocategoria in ChromaDB è taggata in modo diverso —
        # "SALAME" per uno, il bucket generico "SALUMI INTERI/TRANCI" per
        # l'altro: capitava che finissero considerati "categorie diverse" e
        # comparissero insieme nello stesso tagliere). Se il prodotto non
        # appartiene a nessuna famiglia nota, si ricade sulla sottocategoria
        # grezza come prima.
        chiave_diversita = sc
        for rule_name in INCOMPATIBILITY_MATRIX:
            if rule_name == "TERRA_MARE_MIX":
                continue
            if prodotto_appartiene_a_famiglia(r, rule_name):
                chiave_diversita = rule_name
                break

        # Se la sottocategoria è forzata dall'utente, permettiamo infiniti cloni di quella sottocategoria, 
        # altrimenti capiamo a 1 per favorire l'eterogeneità (es. 1 solo prosciutto, 1 solo salame)
        if sottocategoria_forzata and sottocategoria_forzata.lower() in sc:
            cap_sc = 99
        else:
            cap_sc = 1

        if conteggio_forn.get(f, 0) < cap_forn and conteggio_sottocategoria.get(chiave_diversita, 0) < cap_sc:
            selezionati.append(r)
            conteggio_forn[f] = conteggio_forn.get(f, 0) + 1
            conteggio_sottocategoria[chiave_diversita] = conteggio_sottocategoria.get(chiave_diversita, 0) + 1

        if len(selezionati) >= n_target:
            break
            
    return selezionati


def riempi_slot_ricetta(template: dict, collezione_prodotti, indice_codici: dict, embedder,
                         indice_fornitori: "dict | None" = None,
                         indice_testuale: "list | None" = None,
                         target_salumi: int = 3,
                         target_formaggi: int = 3,
                         query_utente: str = "",
                         prodotti_esclusi: "set | None" = None,
                         piano_ricerca: "dict | None" = None) -> list:
    """Step 2: per ogni ingrediente generico del template, cerca il prodotto
    reale a catalogo con la ricerca ibrida (cerca_prodotti).
    Se il template è un tagliere o aperitivo, espande dinamicamente il numero
    di salumi e formaggi a target_salumi/target_formaggi, rispettando la
    territorialità tramite fornitori_config.py: qui NON compare mai un nome
    di fornitore hardcoded. Aggiungere/togliere un fornitore o una regione si
    fa esclusivamente in fornitori_config.py."""
    id_ricetta = template.get("id_ricetta") or template.get("id", "")
    is_tagliere = (
        template.get("categoria") == "tagliere"
        or "tagliere" in str(template.get("nome_piatto", "")).lower()
        or any(w in query_utente.lower() for w in ["tagliere", "tapas"])
    ) and id_ricetta not in ["TAGLIERE_MARE_ITALFISH"]

    if is_tagliere:
        slot_riempiti = []
        regione = rileva_cluster_regionale(
            f"{query_utente} {template.get('nome_piatto', '')} {template.get('STILE_CUCINA', template.get('stile_cucina', ''))}"
        ) or "generico"
        is_spagnolo = regione == "spagna"
        is_mare = any(w in query_utente.lower() for w in ["mare", "pesce", "ittic", "polpo", "tonno", "salmone"]) or (id_ricetta == "TAGLIERE_MARE_ITALFISH")

        def get_comps_piano(ruolo_target: str) -> list:
            comps = []
            if not piano_ricerca or "componenti" not in piano_ricerca:
                return comps
            for c in piano_ricerca.get("componenti", []):
                if c.get("ruolo") == ruolo_target:
                    comps.append(c)
            return comps
            
        def get_comp_piano(ruolo_target: str) -> dict:
            comps = get_comps_piano(ruolo_target)
            return comps[0] if comps else {} if comps else [{}] # Fallback ad un componente vuoto

        # 1. Salumi
        comps_salumi = get_comps_piano("salumi")
        for idx_comp, comp_salumi in enumerate(comps_salumi):
            ts = target_salumi if idx_comp == 0 else 0
            if comp_salumi.get("quantita_target") is not None:
                ts = int(comp_salumi.get("quantita_target"))
                
            if ts > 0:
                if is_mare:
                    sel_salumi = _seleziona_componente_tagliere(
                        "mare", "generico", collezione_prodotti, indice_codici, embedder,
                        indice_fornitori, indice_testuale, filtro_reparto="MARE",
                        n_target=ts, query_utente=comp_salumi.get("query_pulita", query_utente),
                        prodotti_esclusi=prodotti_esclusi,
                        esclusioni_dinamiche=comp_salumi.get("esclusioni"),
                        sottocategoria_forzata=comp_salumi.get("sottocategoria_forzata")
                    )
                    etichetta = "Salumi o affettati di mare"
                else:
                    sel_salumi = _seleziona_componente_tagliere(
                        "salumi", regione, collezione_prodotti, indice_codici, embedder,
                        indice_fornitori, indice_testuale, filtro_reparto="SALUMI",
                        n_target=ts, query_utente=comp_salumi.get("query_pulita", query_utente),
                        prodotti_esclusi=prodotti_esclusi,
                        esclusioni_dinamiche=comp_salumi.get("esclusioni"),
                        sottocategoria_forzata=comp_salumi.get("sottocategoria_forzata")
                    )
                    etichetta = "Salumi artigianali"
                for idx, prod in enumerate(sel_salumi):
                    slot_riempiti.append({
                        "ingrediente_richiesto": f"{etichetta} ({idx_comp+1}.{idx+1})",
                        "categoria_attesa": "Salumi" if not is_mare else "Mare",
                        "ruolo": "protagonista",
                        "note_ingrediente": "affettati o da morsa",
                        "prodotto_trovato": prod,
                    })

        # 2. Formaggi
        comps_formaggi = get_comps_piano("formaggi")
        for idx_comp, comp_formaggi in enumerate(comps_formaggi):
            tf = target_formaggi if idx_comp == 0 else 0
            if comp_formaggi.get("quantita_target") is not None:
                tf = int(comp_formaggi.get("quantita_target"))
                
            vuole_formaggi_esplicito = any(w in query_utente.lower() for w in ["formagg", "cacio", "pecorin"]) or bool(comp_formaggi)
            tema_regione = TEMI_REGIONALI.get(regione) or TEMI_REGIONALI["generico"]
            formaggi_di_default = tema_regione.get("formaggi_di_default", True) and not is_mare
            abilita_formaggi = tf > 0 and (formaggi_di_default or vuole_formaggi_esplicito)
    
            if abilita_formaggi:
                sel_formaggi = _seleziona_componente_tagliere(
                    "formaggi", regione, collezione_prodotti, indice_codici, embedder,
                    indice_fornitori, indice_testuale, filtro_categoria="Formaggi",
                    n_target=tf, query_utente=comp_formaggi.get("query_pulita", query_utente),
                    prodotti_esclusi=prodotti_esclusi,
                    esclusioni_dinamiche=comp_formaggi.get("esclusioni"),
                    sottocategoria_forzata=comp_formaggi.get("sottocategoria_forzata")
                )
                for idx, prod in enumerate(sel_formaggi):
                    slot_riempiti.append({
                        "ingrediente_richiesto": f"Formaggi da degustazione ({idx_comp+1}.{idx+1})",
                        "categoria_attesa": "Formaggi",
                        "ruolo": "protagonista",
                        "note_ingrediente": "pasta dura o semidura da tavola",
                        "prodotto_trovato": prod,
                    })

        # 3. Componente aggiuntiva di mare/tapas
        if is_spagnolo:
            comp_mare = get_comp_piano("mare")
            sel_tapas = _seleziona_componente_tagliere(
                "mare", "spagna", collezione_prodotti, indice_codici, embedder,
                indice_fornitori, indice_testuale, filtro_reparto="MARE",
                n_target=1, query_utente=comp_mare.get("query_pulita", query_utente), 
                prodotti_esclusi=prodotti_esclusi,
                esclusioni_dinamiche=comp_mare.get("esclusioni"),
                sottocategoria_forzata=comp_mare.get("sottocategoria_forzata")
            )
            if sel_tapas:
                slot_riempiti.append({
                    "ingrediente_richiesto": "Acciughe o conserve di mare da tapas",
                    "categoria_attesa": "Mare",
                    "ruolo": "secondario",
                    "note_ingrediente": "stile spagnolo",
                    "prodotto_trovato": sel_tapas[0],
                })
        elif is_mare:
            candidati_tartare = cerca_prodotti(
                collezione_prodotti, indice_codici, embedder,
                "tartare o carpaccio di pesce crudo da degustazione", n_risultati=8,
                indice_fornitori=indice_fornitori, indice_testuale=indice_testuale,
                filtro_reparto="MARE",
            )
            sel_tartare = [
                r for r in candidati_tartare
                if any(w in (r["document"].splitlines()[0].lower() if r.get("document") else "")
                       for w in ("tartare", "carpaccio", "crud"))
            ][:1]
            if sel_tartare:
                slot_riempiti.append({
                    "ingrediente_richiesto": "Tartare o carpaccio di mare",
                    "categoria_attesa": "Mare",
                    "ruolo": "protagonista",
                    "note_ingrediente": "in ciotolina da degustazione con filo d'olio a crudo",
                    "prodotto_trovato": sel_tartare[0],
                })

        # 4. Accompagnamento croccante (pane/taralli/grissini)
        comp_pane = get_comp_piano("pane")
        sel_pane = _seleziona_componente_tagliere(
            "pane", regione, collezione_prodotti, indice_codici, embedder,
            indice_fornitori, indice_testuale, filtro_categoria="Dispensa",
            n_target=1, query_utente=comp_pane.get("query_pulita", query_utente),
            prodotti_esclusi=prodotti_esclusi,
            esclusioni_dinamiche=comp_pane.get("esclusioni"),
            sottocategoria_forzata=comp_pane.get("sottocategoria_forzata")
        )
        if sel_pane:
            slot_riempiti.append({
                "ingrediente_richiesto": "Accompagnamento croccante",
                "categoria_attesa": "Dispensa",
                "ruolo": "secondario",
                "note_ingrediente": "pane, taralli o grissini di accompagnamento",
                "prodotto_trovato": sel_pane[0],
            })

        # 5. Olive da tavola
        comp_olive = get_comp_piano("olive")
        vuole_olive = (
            "oliv" in query_utente.lower()
            or bool(comp_olive)
            or (tema_regione.get("olive_di_default", False))
            or (regione == "spagna" or "spagn" in query_utente.lower())
        )
        if vuole_olive:
            sel_olive = _seleziona_componente_tagliere(
                "olive", regione, collezione_prodotti, indice_codici, embedder,
                indice_fornitori, indice_testuale, filtro_reparto="DISPENSA",
                n_target=1, query_utente=comp_olive.get("query_pulita", query_utente),
                prodotti_esclusi=prodotti_esclusi,
                esclusioni_dinamiche=comp_olive.get("esclusioni"),
                sottocategoria_forzata=comp_olive.get("sottocategoria_forzata")
            )
            if sel_olive:
                slot_riempiti.append({
                    "ingrediente_richiesto": "Olive da tavola per aperitivo",
                    "categoria_attesa": "Dispensa",
                    "ruolo": "secondario",
                    "note_ingrediente": "olive verdi o nere, da servire in ciotolina",
                    "prodotto_trovato": sel_olive[0],
                })

        # 6. Specialità territoriale aggiuntiva (sottoli / mostarde e
        # confetture / snack secco): la prima disponibile per questa regione,
        # in ordine di priorità. Se l'utente ha chiesto esplicitamente
        # mostarde/marmellate/miele, forziamo quel ruolo indipendentemente
        # dalla regione (cercando in qualunque cluster le abbia a catalogo).
        vuole_mostarda_esplicita = any(k in query_utente.lower() for k in ["confettur", "marmellat", "mostard", "compost", "miele"])
        ordine_specialita = ["mostarde_confetture", "sottoli", "snack_secco"] if vuole_mostarda_esplicita else ["sottoli", "mostarde_confetture", "snack_secco"]
        for ruolo_extra in ordine_specialita:
            regioni_da_provare = [regione] if not vuole_mostarda_esplicita else list(TEMI_REGIONALI.keys())
            trovato = False
            for reg_prova in regioni_da_provare:
                if not elenco_fornitori_per_ruolo_regione(ruolo_extra, reg_prova):
                    continue
                sel_extra = _seleziona_componente_tagliere(
                    ruolo_extra, reg_prova, collezione_prodotti, indice_codici, embedder,
                    indice_fornitori, indice_testuale, filtro_categoria="Dispensa",
                    n_target=1, query_utente=query_utente, prodotti_esclusi=prodotti_esclusi,
                )
                if sel_extra:
                    slot_riempiti.append({
                        "ingrediente_richiesto": "Specialità territoriale",
                        "categoria_attesa": "Dispensa",
                        "ruolo": "secondario",
                        "note_ingrediente": ruolo_extra.replace("_", " "),
                        "prodotto_trovato": sel_extra[0],
                    })
                    trovato = True
                    break
            if trovato:
                break

        return slot_riempiti


    # Flusso standard per ricette diverse dal tagliere (primi, secondi, pizze, ecc.)
    query_u_lower = query_utente.lower()
    fornitori_in_query = []
    if indice_fornitori:
        for f_k in indice_fornitori:
            if len(f_k) >= 4 and re.search(r"\b" + re.escape(f_k) + r"\b", query_u_lower):
                fornitori_in_query.append(f_k)

    slot_riempiti = []
    for ingr in template["ingredienti"]:
        nome_ingr = ingr["INGREDIENTE_GENERICO"]
        categoria_attesa = str(ingr.get("CATEGORIA_ATTESA", "")).lower()
        ruolo = str(ingr.get("RUOLO", "opzionale")).lower()
        note = str(ingr.get("NOTE_INGREDIENTE") or "")

        q_cerca = nome_ingr
        # Adattamento con brand esplicito: se il cliente nomina un fornitore
        # nella richiesta, e quel fornitore è pertinente per questo
        # ingrediente, la query di ricerca combina ingrediente + nome del
        # brand. Niente più liste manuali "hamburger oberto", "pasta
        # gentile"...: il legame ingrediente<->fornitore è generico (il
        # brand è pertinente se il suo ruolo dichiarato in
        # fornitori_config.py copre la categoria attesa, o se il fornitore
        # non ha un ruolo da tagliere dichiarato ma il nome dell'ingrediente
        # compare comunque vicino al brand nella richiesta del cliente).
        # Vale automaticamente anche per un fornitore aggiunto oggi in
        # fornitori_config.py, senza dover toccare questo file.
        _RUOLO_PER_CATEGORIA_ATTESA = {
            "salumi": "salumi", "carne": "salumi", "mare": "mare", "pesce": "mare",
            "formaggi": "formaggi", "dispensa": "pane",
        }
        for forn_k in fornitori_in_query:
            slug = match_fornitore(forn_k)
            ruoli_forn = FORNITORI.get(slug, {}).get("ruoli", []) if slug else []
            ruolo_atteso = _RUOLO_PER_CATEGORIA_ATTESA.get(categoria_attesa)
            pertinente = (ruolo_atteso and ruolo_atteso in ruoli_forn) or (
                not ruoli_forn and any(w in nome_ingr.lower() for w in forn_k.split())
            ) or (not ruoli_forn and forn_k in query_u_lower and categoria_attesa in ("dispensa", "carne", "mare"))
            if pertinente:
                q_cerca = f"{nome_ingr} {forn_k}"
                break

        # Se la salsa è richiesta esplicitamente (ketchup / maionese)
        if any(w in nome_ingr.lower() for w in ["salsa", "salse"]):
            if any(w in query_u_lower for w in ["ketchup", "kechup", "chechup"]):
                q_cerca = "ketchup biologico Biobontà"
            elif any(w in query_u_lower for w in ["maionese", "mayo"]):
                q_cerca = "maionese Biobontà"
            elif "senape" in query_u_lower:
                q_cerca = "senape Biobontà"

        is_plurale = any(p in nome_ingr.lower() for p in PAROLE_PLURALI_SLOT)

        if is_plurale:
            risultati = cerca_prodotti(
                collezione_prodotti, indice_codici, embedder,
                q_cerca, n_risultati=N_ESPANSIONE_PLURALE * 4,
                indice_fornitori=indice_fornitori,
                indice_testuale=indice_testuale,
                filtro_categoria=ingr.get("CATEGORIA_ATTESA"),
            )
            if categoria_attesa:
                filtrati = [r for r in risultati
                            if categoria_attesa in str(r["metadata"].get("categoria_prodotto", "")).lower()
                            or categoria_attesa in str(r["metadata"].get("reparto", "")).lower()
                            or categoria_attesa in str(r["metadata"].get("categoria_tassonomia", "")).lower()]
                risultati = filtrati

            visti_fornitori = set()
            selezionati = []
            for r in risultati:
                forn = r["metadata"].get("nome_fornitore", "").lower()
                if forn not in visti_fornitori:
                    visti_fornitori.add(forn)
                    selezionati.append(r)
                if len(selezionati) >= N_ESPANSIONE_PLURALE:
                    break

            for idx, prod in enumerate(selezionati):
                slot_riempiti.append({
                    "ingrediente_richiesto": f"{nome_ingr} ({idx+1})",
                    "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""),
                    "ruolo": ruolo,
                    "note_ingrediente": note,
                    "prodotto_trovato": prod,
                })
            if not selezionati:
                slot_riempiti.append({
                    "ingrediente_richiesto": nome_ingr,
                    "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""),
                    "ruolo": ruolo,
                    "note_ingrediente": note,
                    "prodotto_trovato": None,
                })
        else:
            risultati = cerca_prodotti(
                collezione_prodotti, indice_codici, embedder,
                q_cerca, n_risultati=5,
                indice_fornitori=indice_fornitori,
                indice_testuale=indice_testuale,
                filtro_categoria=ingr.get("CATEGORIA_ATTESA"),
            )
            candidati = [
                r for r in risultati
                if categoria_attesa in str(r["metadata"].get("categoria_prodotto", "")).lower()
                or categoria_attesa in str(r["metadata"].get("reparto", "")).lower()
                or categoria_attesa in str(r["metadata"].get("categoria_tassonomia", "")).lower()
            ] if categoria_attesa else risultati

            if "salsiccia" in nome_ingr.lower() and "fegato" not in (query_u_lower + " " + nome_ingr.lower() + " " + note.lower()):
                senza_fegato = [r for r in candidati if "fegato" not in str(r.get("document", "")).lower()]
                if senza_fegato:
                    candidati = senza_fegato

            if "fungh" in nome_ingr.lower() and not any(k in nome_ingr.lower() for k in ["risotto", "pasta"]):
                senza_carboidrati = [r for r in candidati if not any(k in str(r.get("document", "")).lower() for k in ["risotto", "riso ", "pasta "])]
                if senza_carboidrati:
                    candidati = senza_carboidrati

            is_pizza_ricetta = any(k in str(template.get("categoria", "")).lower() for k in ["pizza", "pinsa"]) or any(k in str(template.get("nome_piatto", "")).lower() for k in ["pizza", "padellino", "pinsa"])
            is_dolce_ricetta = any(k in str(template.get("categoria", "")).lower() for k in ["dolce", "dessert"]) or any(k in str(template.get("nome_piatto", "")).lower() for k in ["gelato", "dolce", "sorbetto", "dessert"])

            if is_pizza_ricetta:
                # Per pizze e pinse: MAI sughi pronti da pasta per gli slot pomodoro/passata/pelati o erbe
                if any(w in nome_ingr.lower() for w in ["pomodoro", "passata", "pelati", "polpa", "datterini", "salsa"]):
                    senza_sughi_pasta = [r for r in candidati if not any(k in str(r.get("document", "")).lower() for k in ["sugo pronto", "ragù", "ragu", "pasta ", "calamarata"])]
                    if senza_sughi_pasta:
                        candidati = senza_sughi_pasta
                if any(w in nome_ingr.lower() for w in ["basilico", "origano"]):
                    senza_sughi = [r for r in candidati if not any(k in str(r.get("document", "")).lower() for k in ["sugo ", "ragù", "ragu"])]
                    if senza_sughi:
                        candidati = senza_sughi

            if is_dolce_ricetta:
                # Per dolci e gelati: MAI ingredienti salati, oli, aceti, conserve sott'olio o piccanti
                candidati_dolci = [r for r in candidati if not any(k in str(r.get("document", "")).lower() for k in ["olio extravergine", "olio evo", "peperoncino", "piccante", "sottolio", "sott'olio", "aceto", "capperi", "olive ", "sale "])]
                candidati = candidati_dolci

            slot_riempiti.append({
                "ingrediente_richiesto": nome_ingr,
                "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""),
                "ruolo": ruolo,
                "note_ingrediente": note,
                "prodotto_trovato": candidati[0] if candidati else None,
            })

    # Aggiunta dinamica per ingredienti specifici espliciti richiesti dall'utente
    INGREDIENTI_EXTRA_CHECK = [
        ("capperi", "capperi di Pantelleria IGP al sale La Nicchia"),
        ("cucunci", "cucunci La Nicchia"),
        ("ketchup", "ketchup biologico Biobontà"),
        ("maionese", "maionese Biobontà"),
        ("senape", "senape Biobontà"),
        ("bacon", "pancetta tesa in conca di marmo Adò bacon"),
        ("pancetta", "pancetta tesa in conca di marmo Adò"),
        ("acciughe", "filetti di acciughe del Cantabrico Medimer"),
        ("alici", "alici marinate o deliscate"),
        ("olive", "olive da tavola in salamoia"),
        ("pomodorini", "datterini pomodorini Così Com'è"),
        ("datterini", "datterini Così Com'è"),
        ("burrata", "burrata pugliese"),
        ("stracciatella", "stracciatella"),
        ("scamorza", "scamorza affumicata"),
    ]
    for ingr_kw, query_cerca in INGREDIENTI_EXTRA_CHECK:
        if re.search(r"\b" + re.escape(ingr_kw) + r"\b", query_u_lower):
            gia_coperto = any(
                ingr_kw in str(s.get("ingrediente_richiesto", "")).lower()
                or (s.get("prodotto_trovato") and ingr_kw in s["prodotto_trovato"]["document"].lower())
                for s in slot_riempiti
            )
            if not gia_coperto:
                res_extra = cerca_prodotti(
                    collezione_prodotti, indice_codici, embedder,
                    query_cerca, n_risultati=3,
                    indice_fornitori=indice_fornitori,
                    indice_testuale=indice_testuale,
                )
                if res_extra:
                    slot_riempiti.append({
                        "ingrediente_richiesto": f"{ingr_kw.capitalize()} (richiesto esplicitamente dal cliente)",
                        "categoria_attesa": res_extra[0]["metadata"].get("categoria_prodotto", "Dispensa"),
                        "ruolo": "secondario",
                        "note_ingrediente": "arricchimento richiesto dal cliente da integrare nella preparazione",
                        "prodotto_trovato": res_extra[0],
                    })

    # Se la richiesta è per un panino o burger (non al piatto) ma nessuno slot contiene pane/buns, inserisci Buns Farino
    is_burger_o_panino = any(k in query_u_lower for k in ["panino", "burger", "hamburger"]) and not any(k in query_u_lower for k in ["al piatto", "senza pane", "senza bun"])
    if is_burger_o_panino:
        ha_pane = any(
            any(w in str(s.get("ingrediente_richiesto", "")).lower() or (s.get("prodotto_trovato") and w in str(s["prodotto_trovato"].get("document", "")).lower())
                for w in ["bun", "pane", "ciabatta", "focaccia"])
            for s in slot_riempiti
        )
        if not ha_pane:
            buns_match = cerca_prodotti(collezione_prodotti, indice_codici, embedder, "Buns Classico Farino burger", n_risultati=2, indice_fornitori=indice_fornitori)
            if buns_match:
                slot_riempiti.insert(0, {
                    "ingrediente_richiesto": "Buns Classico per burger Farino",
                    "categoria_attesa": "Dispensa",
                    "ruolo": "protagonista",
                    "note_ingrediente": "Buns artigianali Farino per burger",
                    "prodotto_trovato": buns_match[0],
                })

    return slot_riempiti


def gestisci_slot_mancante(slot: dict, collezione_prodotti, embedder,
                            soglia_distanza: float = SOGLIA_DISTANZA_SOSTITUTO,
                            categoria_ricetta: str = None) -> dict:
    """Step 3: deterministico nel codice, non lasciato al giudizio del modello.
    Se il protagonista manca -> SCARTA_RICETTA.
    Se secondario/opzionale manca -> cerca sostituto per similarità o imposta OMESSO."""
    if slot["prodotto_trovato"] is not None:
        slot["esito"] = "TROVATO"
        return slot

    if slot["ruolo"] == "protagonista":
        slot["esito"] = "SCARTA_RICETTA"
        return slot

    ingr_nome_basso = str(slot.get("ingrediente_richiesto", "")).lower()

    # Guardrail 1: erbe aromatiche fresche non a catalogo -> tassativamente OMESSO (mai sostituire con sughi o oli)
    if any(w in ingr_nome_basso for w in ["basilico", "prezzemolo", "origano fresco", "menta", "rosmarino fresco", "timo"]):
        slot["esito"] = "OMESSO"
        return slot

    is_dolce = (categoria_ricetta and any(k in categoria_ricetta.lower() for k in ["dolce", "dessert"])) or any(k in ingr_nome_basso for k in ["panna", "gelato", "coulis", "frutta fresca", "torta", "dessert", "dolce"])

    embedding = _embed_testo_query(embedder, slot["ingrediente_richiesto"])
    where_filter = {"categoria_prodotto": slot["categoria_attesa"]} if slot["categoria_attesa"] else None
    candidati = collezione_prodotti.query(
        query_embeddings=[embedding], n_results=5,
        where=where_filter,
    )
    if candidati.get("distances") and candidati["distances"][0] and candidati["distances"][0][0] <= soglia_distanza:
        # Se siamo in un dolce, verifichiamo che il sostituto non sia salato/olio/piccante
        if is_dolce:
            candidato_valido = None
            for idx_c, doc_c in enumerate(candidati["documents"][0]):
                doc_c_lower = doc_c.lower() if doc_c else ""
                if not any(k in doc_c_lower for k in ["olio ", "aceto", "peperoncino", "piccante", "salato", "sottolio", "sott'olio", "capperi", "olive", "sale ", "ragu", "sugo"]):
                    candidato_valido = idx_c
                    break
            if candidato_valido is not None:
                slot["esito"] = "SOSTITUITO"
                slot["sostituto_id"] = candidati["ids"][0][candidato_valido]
                meta_sostituto = candidati["metadatas"][0][candidato_valido]
                prima_linea = candidati["documents"][0][candidato_valido].splitlines()[0] if candidati["documents"][0][candidato_valido] else ""
                slot["sostituto_nome"] = pulisci_nome_commerciale(prima_linea, meta_sostituto.get("nome_fornitore", ""))
            else:
                slot["esito"] = "OMESSO"
        else:
            slot["esito"] = "SOSTITUITO"
            slot["sostituto_id"] = candidati["ids"][0][0]
            meta_sostituto = candidati["metadatas"][0][0]
            prima_linea = candidati["documents"][0][0].splitlines()[0] if candidati["documents"][0][0] else ""
            slot["sostituto_nome"] = pulisci_nome_commerciale(prima_linea, meta_sostituto.get("nome_fornitore", ""))
    else:
        slot["esito"] = "OMESSO"
    return slot


def proponibile(slot_riempiti: list) -> bool:
    """Regola documentata anche nella Legenda del ricettario: proponibile se
    TUTTI i 'protagonista' sono risolti E almeno metà dei 'secondario' lo è.
    Gli 'opzionale' non condizionano l'esito."""
    protagonisti = [s for s in slot_riempiti if s["ruolo"] == "protagonista"]
    if any(s.get("esito") == "SCARTA_RICETTA" for s in protagonisti):
        return False

    secondari = [s for s in slot_riempiti if s["ruolo"] == "secondario"]
    if secondari:
        trovati = sum(1 for s in secondari if s.get("esito") in ("TROVATO", "SOSTITUITO"))
        if trovati < len(secondari) / 2:
            return False
    return True


