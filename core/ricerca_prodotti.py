"""
cerca_prodotti: punto d'ingresso unico della ricerca ibrida (esatto + fornitore + lessicale + vettoriale).
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
from core.ricerca_base import MAX_PRODOTTI_PER_FORNITORE, ricerca_vettoriale, trova_match_esatti_per_codice, trova_match_lessicale, trova_match_per_fornitore
from core.vincoli_dieta import _CANALE_CORRENTE, _DIETA_CORRENTE, _QUERY_ORIGINALE, _query_vuole_salumi_di_terra, prodotto_compatibile_con_dieta


def _canale_formato_record(r: dict) -> str:
    """'horeca' | 'retail' | 'misto' | 'sconosciuto' del formato del prodotto: dal
    metadato arricchito (catalogo_v2) se c'e', altrimenti calcolato al volo."""
    c = r["metadata"].get("canale_formato")
    if c:
        return c
    return formato_prodotto(r["metadata"], r.get("document", ""))["canale_formato"]


def _ordina_per_canale(risultati: list, canale_locale: "str | None") -> list:
    """Preferenza (non esclusione) di formato in base al cliente: a un
    ristorante/bar (horeca) prima i formati grandi, a una bottega (retail)
    prima quelli piccoli. L'ordine di rilevanza resta invariato dentro ogni
    fascia, e i match espliciti (codice/fornitore nominato) non vengono mai
    retrocessi. Prima di questa funzione `canale_locale` veniva ricevuto da
    cerca_prodotti ma non usato (test reale R10)."""
    canale = (canale_locale or "").lower()
    if canale not in ("horeca", "retail"):
        return risultati
    ideale = canale
    opposto = "retail" if canale == "horeca" else "horeca"

    def fascia(r):
        if r.get("match_esatto") or r.get("match_fornitore"):
            return 0
        c = _canale_formato_record(r)
        if c == ideale:
            return 0
        if c == opposto:
            r["formato_non_ideale"] = True
            return 2
        return 1

    # La preferenza di formato agisce SOLO tra varianti dello stesso tipo di prodotto
    # (es. latte da 1 L prima di quello da 250 ml): non deve scavalcare la rilevanza
    # (un wurstel retail non va dietro a una "salsiccia di tonno" horeca irrilevante).
    def gruppo(r):
        m = r["metadata"]
        return str(m.get("tipo_prodotto") or f"{m.get('sottocategoria')}|{m.get('specifiche_liv4')}").lower()

    ordine_gruppo = {}
    for r in risultati:
        ordine_gruppo.setdefault(gruppo(r), len(ordine_gruppo))
    return sorted(risultati, key=lambda r: (ordine_gruppo[gruppo(r)], fascia(r)))


def _match_sottocategoria(r, sc_lower: str) -> bool:
    """Match flessibile su sottocategoria: cerca in sottocategoria (esatto),
    specifiche_liv4 (contiene) e document (contiene con word boundary).
    Risolve il bug per cui denominazioni come 'Parma', 'San Daniele', 'erborinato'
    non matchano la sottocategoria ECR generica (es. 'SALUMI INTERI/TRANCI')."""
    # 1. Match esatto sulla sottocategoria ECR
    if sc_lower == str(r["metadata"].get("sottocategoria", "")).lower():
        return True
    # 2. Contenuto in specifiche_liv4 (es. "PROSC CRUDO PARMA" contiene "parma")
    if sc_lower in str(r["metadata"].get("specifiche_liv4", "")).lower():
        return True
    # 3. Contenuto nel nome/descrizione prodotto (con word boundary per evitare falsi positivi)
    doc = r.get("document", "").lower()
    if re.search(r'\b' + re.escape(sc_lower) + r'\b', doc):
        return True
    return False


def cerca_prodotti(collezione, indice_codici, embedder, user_query: str, n_risultati: int,
                    indice_fornitori: "dict | None" = None,
                    indice_testuale: "list | None" = None,
                    filtro_categoria: "str | None" = None,
                    filtro_reparto: "str | None" = None,
                    filtro_sottocategoria: "str | None" = None,
                    tipo_locale: "str | None" = None,
                    filtro_dieta: "str | None" = None,
                    canale_locale: "str | None" = None,
                    esclusioni: "list | None" = None, intento: str = None) -> list:
    """Punto di ingresso unico: combina match esatti per codice + match per
    fornitore + match lessicale per nome + ricerca vettoriale, deduplicando per id (il match esatto ha
    sempre precedenza), con un cap per fornitore adattivo (non hardcoded per singola categoria/brand:
    si allarga da solo quando una nicchia merceologica ha pochi fornitori a catalogo), supporto a
    filtro_reparto, filtro_sottocategoria, filtro dietetico deterministico (vegano/vegetariano) con
    gestione delle negazioni, esclusione basi precotte per pizzerie e boost di coerenza HORECA/RETAIL
    in base al canale del cliente (canale_locale: 'horeca' | 'retail' | 'misto', vedi profilazione_locale.py)."""
    filtro_dieta = filtro_dieta or _DIETA_CORRENTE.get()
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
        esatti = [r for r in esatti if _match_sottocategoria(r, sc_lower)]
        fornitori_match = [r for r in fornitori_match if _match_sottocategoria(r, sc_lower)]
        lessicali_match = [r for r in lessicali_match if _match_sottocategoria(r, sc_lower)]
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

    # Esclusioni gia' note PRIMA del ranking: se restano solo a valle, il top-N
    # vettoriale si riempie di prodotti che verranno scartati e il risultato
    # finale e' vuoto (test reale R8: menu vegano -> solo surgelati scartati dal filtro tagliere).
    escl_reparti_pre = []
    escl_sottocat_pre = []
    ruolo_pre = None
    if ontologia.indice_ha_attributi(indice_testuale) and not filtro_reparto and not filtro_sottocategoria:
        ruolo_pre = ontologia.ruolo_da_query(user_query)
    if filtro_reparto != "MARE" and _query_vuole_salumi_di_terra(user_query):
        escl_reparti_pre.append("MARE")
    if intento in ("tagliere_o_ricetta", "composizione_piatto"):
        from core.config_manager import get_regole_tagliere as _grt
        _rt = _grt()
        escl_reparti_pre += [x.upper() for x in _rt.get("reparti_vietati", [])]
        escl_sottocat_pre += [x.upper() for x in _rt.get("sottocategorie_vietate", [])]

    try:
        query_embedding = embedder.embed_query(user_query)
        vettoriali = ricerca_vettoriale(
            collezione, query_embedding, n_risultati * 2,
            filtro_categoria=filtro_categoria,
            filtro_reparto=filtro_reparto,
            filtro_sottocategoria=None,  # Rimosso: ChromaDB applica solo uguaglianza esatta, il filtro flessibile è applicato post-retrieval
            filtro_dieta=filtro_dieta,
            escludi_reparti=escl_reparti_pre or None,
            escludi_sottocategorie=escl_sottocat_pre or None,
            richiedi_uso=ruolo_pre,
        )
    except Exception as e:
        from core.errori import breve
        print(f"[ATTENZIONE] Ricerca vettoriale fallita ({breve(e)}): solo ricerca lessicale")
        vettoriali = []

    combinati = list(esatti) + fornitori_match + lessicali_match
    
    # FAIRNESS ALGORITHM: Limitiamo la predominanza di un singolo fornitore
    # nei risultati vettoriali. Soft cap a 5 prodotti per fornitore.
    vettoriali_fair = []
    vettoriali_overflow = []
    conteggio_fornitori_vett = {}
    
    for r in vettoriali:
        f = str((r["metadata"].get("nome_fornitore") or "")).strip().lower()
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

    # Filtro flessibile sottocategoria post-retrieval (cerca in sottocategoria, specifiche_liv4, document)
    if filtro_sottocategoria:
        combinati = [r for r in combinati if _match_sottocategoria(r, filtro_sottocategoria.lower())]

    
    # ==========================================
    # FASE 3: IL MOTORE A REGOLE (Garbage Collector)
    # ==========================================
    from core.config_manager import get_regole_tagliere, get_regole_dieta, is_reparto_escluso_da_rag, is_categoria_documentale
    
    # 1. Filtro Assoluto Documentale/Aziendale (Vale SEMPRE)
    filtrati_base = []
    for r in combinati:
        rep = str(r["metadata"].get("reparto", "")).upper()
        cat = str(r["metadata"].get("categoria_prodotto", "")).upper()
        if is_reparto_escluso_da_rag(rep) or is_categoria_documentale(cat):
            continue
        filtrati_base.append(r)
    combinati = filtrati_base

    # 2. Filtro Tagliere (Solo se l'intento lo richiede)
    if intento in ("tagliere_o_ricetta", "composizione_piatto"):
        regole_tagliere = get_regole_tagliere()
        rep_vietati = [r.upper() for r in regole_tagliere.get("reparti_vietati", [])]
        sc_vietate = [s.upper() for s in regole_tagliere.get("sottocategorie_vietate", [])]
        
        filtrati_tagliere = []
        for r in combinati:
            rep = str(r["metadata"].get("reparto", "")).upper()
            sc = str(r["metadata"].get("sottocategoria", "")).upper()
            if rep in rep_vietati or sc in sc_vietate:
                continue
            filtrati_tagliere.append(r)
        combinati = filtrati_tagliere

    # 2b. "Salumi" senza citare il mare: via i prodotti ittici con nome da salume
    if filtro_reparto != "MARE" and _query_vuole_salumi_di_terra(user_query):
        combinati = [r for r in combinati if str(r["metadata"].get("reparto", "")).upper() != "MARE"]

    # 2c. Attributi arricchiti (presenti solo in catalogo_v2, vedi scripts/costruisci_catalogo_v2.py):
    # regole sull'USO del prodotto, non sulla sua categoria merceologica.
    if _query_vuole_salumi_di_terra(user_query) and filtro_reparto != "MARE":
        combinati = [r for r in combinati if r["metadata"].get("origine_proteina") != "pesce"]
    if intento in ("tagliere_o_ricetta", "composizione_piatto"):
        combinati = [r for r in combinati if not r["metadata"].get("richiede_cottura")]

    combinati = ontologia.filtra_olive_in_risultati(user_query, combinati)
    combinati = ontologia.escludi_solo_ingredienti(user_query, combinati)
    spec_olive = ontologia.spec_da_query(user_query)
    elenco_famiglia = False
    if spec_olive and ontologia.indice_ha_attributi(indice_testuale):
        # Famiglie nominate dal cliente (olive, taralli, patatine, frutta secca, topping, finger food):
        # "olive" = in salamoia, intere (regola del cliente). I prodotti giusti vanno in testa,
        # ordinati per tipologia/formato, invece di dipendere dal rank lessicale/vettoriale
        elenco = ontologia.vuole_elenco(_QUERY_ORIGINALE.get() or user_query)
        diretti = ontologia.scegli(spec_olive, indice_testuale, canale=canale_locale or _CANALE_CORRENTE.get(), dieta=filtro_dieta,
                                   n=n_risultati if elenco else 4 if spec_olive.get("famiglia") != "olive_da_tavola" else 3)
        ids_d = {r["id"] for r in diretti}
        # "che finger food avete?": solo la famiglia, niente vicini vettoriali (chat reale: sugo di tonno per crostini
        # e tartare di salmone elencati come finger food)
        elenco_famiglia = elenco and bool(diretti)
        combinati = diretti + ([] if elenco_famiglia else [r for r in combinati if r["id"] not in ids_d])
    elif ruolo_pre == "aperitivo" and ontologia.indice_ha_attributi(indice_testuale):
        # "aperitivo" generico: un prodotto per famiglia da ciotolina (olive, taralli, frutta secca, patatine,
        # finger food), non i soli prodotti che il vettoriale favorisce
        tipici = []
        for _nome_f in ontologia.FAMIGLIE_APERITIVO:
            _sp = ontologia.spec_famiglia(_nome_f, user_query)
            if _sp:
                tipici += ontologia.scegli(_sp, indice_testuale, canale=canale_locale or _CANALE_CORRENTE.get(),
                                           esclusi={r["id"] for r in tipici}, dieta=filtro_dieta, n=1)
        ids_t = {r["id"] for r in tipici}
        combinati = tipici + [r for r in combinati if r["id"] not in ids_t]

    # Regione chiesta dal cliente ("salumi lucani", "solo prodotti pugliesi"): prima i prodotti di produttori di quella
    # regione (sofood/regioni_produttori.csv), anche se la ricerca non li aveva trovati, purche' dello stesso reparto
    from core import territorio
    _q_reg = _QUERY_ORIGINALE.get() or user_query
    _regione = territorio.regione_richiesta(_q_reg)
    if _regione and intento not in ("tagliere_o_ricetta", "composizione_piatto") and indice_testuale:
        _reparti = {str(r["metadata"].get("reparto") or "") for r in combinati[:5]}
        if filtro_reparto:
            _reparti = {filtro_reparto}
        _ids = {r["id"] for r in combinati}
        combinati = combinati + [{"id": p["id"], "metadata": p["metadata"], "document": p["document"], "match_esatto": False}
                                 for p in indice_testuale if p["id"] not in _ids
                                 and str(p["metadata"].get("reparto") or "") in _reparti
                                 and territorio.di_regione(p, _regione)]
        combinati = territorio.ordina_per_regione(combinati, _regione, _q_reg)

    # 3. Filtro dietetico (YAML + flag catalogo), regole in prodotto_compatibile_con_dieta
    if filtro_dieta:
        combinati = [r for r in combinati if prodotto_compatibile_con_dieta(r["metadata"], filtro_dieta)]

    # Esclusioni persistenti del cliente ("niente tonno"): valgono per ogni ricerca della sessione
    from core.allergeni import ALLERGIE_CORRENTI
    if ontologia.ESCLUSIONI_CORRENTI.get() or ALLERGIE_CORRENTI.get() or logistica.ZONA_CORRENTE.get() == "fuori":
        combinati = [r for r in combinati if not ontologia.prodotto_escluso_da_cliente(r["metadata"], r.get("document", ""))]

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
    combinati = _ordina_per_canale(combinati, canale_locale)
    # confezioni da rivendita (espositore, souvenir) in coda, salvo richiesta: "avete del pecorino?" proponeva il
    # cofanetto "Sfizi Mix con Espositore (souvenir)" come pecorino (debug 2026-10-06)
    if not re.search(r"espositor|souvenir|regalo|confezion", (_QUERY_ORIGINALE.get() or user_query).lower()):
        combinati = sorted(combinati, key=lambda r: bool(re.search(r"espositor|souvenir", prima_riga(r.get("document", "")).lower())))
    if elenco_famiglia:
        return combinati[:n_risultati]  # l'elenco di una famiglia e' tutto quello che c'e', anche di un solo produttore

    fornitori_distinti = {(r["metadata"].get("nome_fornitore") or "") for r in combinati}
    if 0 < len(fornitori_distinti) <= 2:
        cap_categoria_di_nicchia = min(max(n_risultati, MAX_PRODOTTI_PER_FORNITORE), 12)
    else:
        cap_categoria_di_nicchia = MAX_PRODOTTI_PER_FORNITORE

    conteggio_fornitori = {}
    risultati_diversificati = []
    for r in combinati:
        fornitore = (r["metadata"].get("nome_fornitore") or "")
        conteggio_fornitori[fornitore] = conteggio_fornitori.get(fornitore, 0) + 1

        max_forn = cap_categoria_di_nicchia
        if r.get("match_esatto") or r.get("match_fornitore"):
            max_forn = max(max_forn, 8)

        if r.get("match_esatto") or conteggio_fornitori[fornitore] <= max_forn:
            risultati_diversificati.append(r)

    return risultati_diversificati[:n_risultati]
