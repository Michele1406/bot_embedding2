# -*- coding: utf-8 -*-
"""
fornitori_config.py
====================
QUESTO È L'UNICO FILE DA TOCCARE QUANDO SI AGGIUNGE O SI TOGLIE UN FORNITORE
DAL CATALOGO SO FOOD.

Prima della riorganizzazione, il "sapere" su ogni fornitore (di che regione è,
che ruolo gioca in un tagliere, quali parole chiave lo riconoscono in una
richiesta cliente...) era sparso e duplicato in almeno 4 punti diversi del
codice (retrieval_utils.py in tre punti diversi, app.py e system_prompt_v2.py).
Aggiungere un fornitore nuovo richiedeva editare codice Python in più file,
con alto rischio di dimenticanze e comportamenti incoerenti.

Ora quel "sapere" vive in UNA SOLA struttura dati. Tutto il resto del sistema
(retrieval, composizione taglieri, adattamento a brand esplicito in chat) legge
da qui. Aggiungere un fornitore = aggiungere una entry qui sotto. Toglierlo =
rimuovere l'entry (o mettere "attivo": False). Nessun'altra riga di codice va
toccata.

Il match verso ChromaDB avviene sul campo metadata "nome_fornitore" tramite
`nomi_match` (sottostringa case-insensitive) oppure tramite `codice_fornitore`
(anagrafica, quando disponibile: più affidabile del nome perché non cambia mai).

Campi per fornitore:
- nomi_match: lista di sottostringhe (lowercase) che identificano il fornitore
  nel campo "nome_fornitore" di ChromaDB. Basta che una sia contenuta nel nome.
- codice_fornitore: codice anagrafico (es. "19010014"), se noto. Se presente ha
  SEMPRE priorità sul match per nome (più robusto ai refuse di battitura/rebrand).
- regione: cluster regionale/stilistico di appartenenza per la composizione dei
  taglieri e dei piatti a tema (vedi TEMI_REGIONALI sotto). Usa "generico" se il
  fornitore non è legato a una regione specifica.
- ruoli: lista dei ruoli merceologici che il fornitore copre nei taglieri /
  nelle proposte composte. Valori ammessi: "salumi", "formaggi", "mare",
  "pane", "olive", "sottoli", "mostarde_confetture", "snack_secco", "contorni".
  Lascia lista vuota [] se il fornitore non partecipa a composizioni taglieri
  (es. un fornitore di sole materie prime per cucina).
- attivo: metti a False per disattivare temporaneamente un fornitore senza
  cancellare la sua configurazione (es. fuori produzione stagionale).
"""

FORNITORI = {
    # ---------------------------------------------------------------- SPAGNA
    "solera": {
        "nomi_match": ["solera"],
        "codice_fornitore": None,
        "regione": "spagna",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "cecinas_nieto": {
        "nomi_match": ["cecinas nieto", "nieto"],
        "codice_fornitore": None,
        "regione": "spagna",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "medimer": {
        "nomi_match": ["medimer"],
        "codice_fornitore": None,
        "regione": "spagna",
        "ruoli": ["mare"],
        "attivo": True,
    },

    # --------------------------------------------------------------- TOSCANA
    "franchi_salumi": {
        "nomi_match": ["franchi salumi", "franchi"],
        "codice_fornitore": None,
        "regione": "toscana",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "la_bottega_di_ado": {
        "nomi_match": ["la bottega di ado", "bottega di ado"],
        "codice_fornitore": None,
        "regione": "toscana",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "patrone_1992": {
        "nomi_match": ["patrone 1992", "patrone"],
        "codice_fornitore": None,
        "regione": "toscana",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "formaggeria_toscana": {
        "nomi_match": ["formaggeria toscana"],
        "codice_fornitore": None,
        "regione": "toscana",
        "ruoli": ["formaggi"],
        "attivo": True,
    },
    "calugi": {
        "nomi_match": ["calugi"],
        "codice_fornitore": None,
        "regione": "toscana",
        "ruoli": ["snack_secco"],
        "attivo": True,
    },

    # ---------------------------------------------------------------- PUGLIA
    "salumi_martina_franca": {
        "nomi_match": ["salumi martina franca", "martina franca"],
        "codice_fornitore": None,
        "regione": "puglia",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "la_ghianda": {
        "nomi_match": ["la ghianda"],
        "codice_fornitore": None,
        "regione": "puglia",
        "ruoli": ["formaggi"],
        "attivo": True,
    },
    "recco": {
        "nomi_match": ["recco"],
        "codice_fornitore": None,
        "regione": "puglia",
        "ruoli": ["formaggi"],
        "attivo": True,
    },
    "i_de_giorgi": {
        "nomi_match": ["i de giorgi", "de giorgi"],
        "codice_fornitore": None,
        "regione": "puglia",
        "ruoli": ["sottoli"],
        "attivo": True,
    },

    # -------------------------------------------------------------- TRENTINO
    "crucolo": {
        "nomi_match": ["crucolo"],
        "codice_fornitore": None,
        "regione": "trentino",
        # NB: "Crucoloso" (creme spalmabili fuse) e' un prodotto diverso dal
        # salume/formaggio da tagliere dello stesso fornitore: l'esclusione
        # anti-dessert generica nel filtro qualita' se ne occupa comunque.
        "ruoli": ["salumi", "formaggi"],
        "attivo": True,
    },
    "capriz": {
        "nomi_match": ["capriz"],
        "codice_fornitore": None,
        "regione": "trentino",
        "ruoli": ["formaggi"],
        "attivo": True,
    },
    "valle_di_gresta": {
        "nomi_match": ["valle di gresta"],
        "codice_fornitore": None,
        "regione": "trentino",
        "ruoli": ["contorni"],
        "attivo": True,
    },

    # -------------------------------------------------------------- PIEMONTE
    "oberto": {
        "nomi_match": ["oberto"],
        "codice_fornitore": None,
        "regione": "piemonte",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "la_casera": {
        "nomi_match": ["la casera"],
        "codice_fornitore": None,
        "regione": "piemonte",
        "ruoli": ["formaggi"],
        "attivo": True,
    },
    "il_mongetto": {
        "nomi_match": ["il mongetto", "mongetto"],
        "codice_fornitore": None,
        "regione": "piemonte",
        "ruoli": ["mostarde_confetture"],
        "attivo": True,
    },

    # ---------------------------------------------------------------- EMILIA
    "branchi": {
        "nomi_match": ["branchi"],
        "codice_fornitore": None,
        "regione": "emilia",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "bbs": {
        "nomi_match": ["bbs"],
        "codice_fornitore": None,
        "regione": "emilia",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "pellizziari": {
        "nomi_match": ["pellizziari"],
        "codice_fornitore": None,
        "regione": "emilia",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "fattoria_ca_dante": {
        "nomi_match": ["fattoria ca' dante", "fattoria ca dante"],
        "codice_fornitore": None,
        "regione": "emilia",
        "ruoli": ["salumi"],
        "attivo": True,
    },
    "montanari_gruzza": {
        "nomi_match": ["montanari & gruzza", "montanari e gruzza", "montanari"],
        "codice_fornitore": None,
        "regione": "emilia",
        "ruoli": ["formaggi"],
        "attivo": True,
    },
    "carpinello": {
        "nomi_match": ["carpinello"],
        "codice_fornitore": None,
        "regione": "emilia",
        "ruoli": ["formaggi"],
        "attivo": True,
    },

    # --------------------------------------------------------------- SARDEGNA
    "smeralda": {
        "nomi_match": ["smeralda"],
        "codice_fornitore": None,
        "regione": "sardegna",
        "ruoli": ["mare"],
        "attivo": True,
    },
    "battaccone": {
        "nomi_match": ["battaccone"],
        "codice_fornitore": None,
        "regione": "sardegna",
        "ruoli": ["pane"],
        "attivo": True,
    },

    # --------------------------------------------------------- MARE (generico)
    "italfish": {
        "nomi_match": ["italfish"],
        "codice_fornitore": "19010925",
        "regione": "generico",
        "ruoli": ["mare"],
        "attivo": True,
    },
    "colimena": {
        "nomi_match": ["colimena"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": ["mare"],
        "attivo": True,
    },

    # -------------------------------------------------------- TRASVERSALI
    "farino": {
        "nomi_match": ["farino"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": ["pane"],
        "attivo": True,
    },
    "fresco_piada": {
        "nomi_match": ["fresco piada"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": ["pane"],
        "attivo": True,
    },
    "capuano": {
        "nomi_match": ["capuano"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": ["olive"],
        "attivo": True,
    },
    "de_filippis": {
        "nomi_match": ["de filippis"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": ["olive"],
        "attivo": True,
    },
    "anfosso": {
        "nomi_match": ["anfosso"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": ["olive"],
        "attivo": True,
    },
    "biobonta": {
        "nomi_match": ["biobonta", "biobontà"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": [],  # salse/condimenti: gestito come brand esplicito, non da tagliere
        "attivo": True,
    },
    "san_salvatore": {
        "nomi_match": ["san salvatore"],
        "codice_fornitore": "19010926",
        "regione": "generico",
        "ruoli": [],
        "attivo": True,
    },
    "boschi_1961": {
        "nomi_match": ["boschi 1961", "boschi"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": [],  # spezie: gestite dai campi buono_per_* dedicati, non dal motore taglieri
        "attivo": True,
    },
    "pastificio_gentile": {
        "nomi_match": ["pastificio gentile", "gentile"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": [],
        "attivo": True,
    },
    "casa_prencipe": {
        "nomi_match": ["casa prencipe", "prencipe"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": [],
        "attivo": True,
    },
    "pasta_marilungo": {
        "nomi_match": ["pasta marilungo", "marilungo"],
        "codice_fornitore": None,
        "regione": "generico",
        "ruoli": [],
        "attivo": True,
    },

    # ------------------------------------------------------------------
    # TEMPLATE per aggiungere un nuovo fornitore: copia il blocco sotto,
    # rinomina la chiave (slug univoco), compila i campi e basta.
    # "nuovo_fornitore_slug": {
    #     "nomi_match": ["nome esatto o parziale come appare in ChromaDB"],
    #     "codice_fornitore": None,  # oppure "19010xxx" se noto (preferibile)
    #     "regione": "generico",     # o una regione esistente / una nuova
    #     "ruoli": [],               # es. ["salumi"], ["formaggi"], ecc.
    #     "attivo": True,
    # },
}


# ============================================================================
# TEMI REGIONALI
# ============================================================================
# Breve descrizione testuale del carattere gastronomico di ogni cluster, usata
# SOLO come query di ricerca vettoriale generica quando per un ruolo/regione
# non c'e' ancora nessun fornitore taggato in FORNITORI (fallback), o come
# arricchimento del testo di ricerca. NON contiene nomi di prodotto: quelli
# vengono letti a runtime dal catalogo reale via fornitori_config, così restano
# sempre sincronizzati con cosa è davvero disponibile.
TEMI_REGIONALI = {
    "spagna": {
        "parole_chiave": ["spagnol", "spagna", "iberic", "tapas", "bellota", "cecina", "jamon", "jamón"],
        "salumi": "salumi iberici stagionati stile spagnolo, prosciutto bellota, chorizo, salchichon",
        "formaggi": "",  # niente formaggi italiani di default in un tagliere spagnolo
        "formaggi_di_default": False,
        "mare": "acciughe e conserve di mare stile cantabrico",
        "pane": "pane croccante da tapas, picos, grissini",
        "olive": "olive da tavola in salamoia",
    },
    "toscana": {
        "parole_chiave": ["toscan", "maremm", "chianti", "firenze", "finocchiona"],
        "salumi": "salumi toscani tradizionali, finocchiona, salame, lardo",
        "formaggi": "pecorino toscano e formaggi a pasta dura toscani",
        "formaggi_di_default": True,
        "pane": "grissini e pane croccante toscano",
        "olive": "olive da tavola artigianali",
        "snack_secco": "snack secchi al tartufo toscano",
    },
    "puglia": {
        "parole_chiave": ["puglies", "puglia", "martina franca", "barese", "salento", "murge"],
        "salumi": "capocollo di Martina Franca, pancetta, salumi pugliesi tradizionali",
        "formaggi": "caciocavallo, burrata, provolone e formaggi pugliesi",
        "formaggi_di_default": True,
        "pane": "taralli e grissini pugliesi",
        "olive": "olive bella di Cerignola, olive baresane",
        "sottoli": "verdure grigliate e sott'olio pugliesi",
    },
    "trentino": {
        "parole_chiave": ["trentin", "alto adige", "tirol", "montagna", "alpino", "dolomit", "crucolo", "capriz"],
        "salumi": "carne salada, salame di montagna stile trentino",
        "formaggi": "formaggi di malga e formaggi alpini stagionati",
        "formaggi_di_default": True,
        "pane": "grissini di montagna",
        "contorni": "patate di montagna con buccia",
    },
    "piemonte": {
        "parole_chiave": ["piemont", "langhe", "torino", "fassona", "casera", "mongetto"],
        "salumi": "sfilaccio di fassona, carpaccio, bresaola piemontese",
        "formaggi": "toma piemontese, formaggi erborinati piemontesi",
        "formaggi_di_default": True,
        "pane": "grissini piemontesi",
        "mostarde_confetture": "mostarda e confetture piemontesi da abbinare ai formaggi",
    },
    "emilia": {
        "parole_chiave": ["emilian", "emilia", "romagna", "parma", "bologna", "modena"],
        "salumi": "prosciutto cotto, mortadella, prosciutto di Parma DOP, salumi emiliani",
        "formaggi": "parmigiano reggiano DOP e formaggi emiliani stagionati",
        "formaggi_di_default": True,
        "pane": "piadina e grissini",
    },
    "sardegna": {
        "parole_chiave": ["sard", "sardegna"],
        "mare": "bottarga di muggine, polpa di riccio, specialità di mare sarde",
        "pane": "pane carasau, guttiau",
        "formaggi_di_default": False,
    },
    "generico": {
        "parole_chiave": [],
        "salumi": "selezione di salumi artigianali italiani",
        "formaggi": "selezione di formaggi da tavola italiani",
        "formaggi_di_default": True,
        "pane": "taralli o grissini artigianali",
        "olive": "olive da tavola artigianali",
    },
}


def elenco_fornitori_per_ruolo_regione(ruolo: str, regione: str) -> list:
    """Restituisce l'elenco dei fornitori attivi che coprono un dato ruolo
    (es. 'salumi') in una data regione (es. 'puglia')."""
    risultato = []
    for slug, cfg in FORNITORI.items():
        if not cfg.get("attivo", True):
            continue
        if ruolo in cfg.get("ruoli", []) and cfg.get("regione") == regione:
            risultato.append(slug)
    return risultato


def match_fornitore(nome_fornitore_catalogo: str, codice_fornitore_catalogo: str = "") -> "str | None":
    """Dato un nome/codice fornitore come appare nei metadati di ChromaDB,
    restituisce lo slug della entry in FORNITORI corrispondente (o None)."""
    n = (nome_fornitore_catalogo or "").strip().lower()
    c = (codice_fornitore_catalogo or "").strip()
    for slug, cfg in FORNITORI.items():
        if not cfg.get("attivo", True):
            continue
        if c and cfg.get("codice_fornitore") and c == cfg["codice_fornitore"]:
            return slug
        if any(nm in n for nm in cfg.get("nomi_match", [])):
            return slug
    return None


def regione_del_fornitore(nome_fornitore_catalogo: str, codice_fornitore_catalogo: str = "") -> str:
    slug = match_fornitore(nome_fornitore_catalogo, codice_fornitore_catalogo)
    if slug:
        return FORNITORI[slug].get("regione", "generico")
    return "generico"
