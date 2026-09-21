# -*- coding: utf-8 -*-
"""
domain_rules.py
===============
Matrice di incompatibilità e regole di dominio per il Constraint Solver.
Definisce quali categorie, sottocategorie e reparti sono incompatibili tra loro
o hanno limiti di cardinalità (es. massimo 1 insaccato macinato per tagliere).
"""

# Definiamo le regole di incompatibilità (Mutual Exclusion & Cardinality)
INCOMPATIBILITY_MATRIX = {
    "SALUMI_MACINATI": {
        "description": "Insaccati stagionati macinati e aromatizzati (forti).",
        "members": ["SALAME", "FINOCCHIONA", "SPIANATA", "SCHIACCIATA", "SOPRESSA", "SOPPRESSATA", "VENTRICINA", "CIAUSCOLO", "SALAMI"],
        "max_per_board": 1,
        "error_msg": "Troppi insaccati macinati/aromatizzati. Sostituisci uno dei macinati con un pezzo intero (es. Prosciutto o Coppa) per bilanciare la texture."
    },
    "SALUMI_COTTI": {
        "description": "Salumi cotti (dolci e morbidi).",
        "members": ["MORTADELLA", "PROSCIUTTO COTTO", "PROSC COTTO", "PORCHETTA", "PROSCIUTTO ARROSTO", "ARROSTI"],
        "max_per_board": 1,
        "error_msg": "Troppi salumi cotti. Il tagliere risulta troppo dolce/grasso. Mantieni un solo salume cotto."
    },
    "PROSCIUTTI_CRUDI": {
        "description": "Prosciutti crudi interi stagionati.",
        "members": ["PROSCIUTTO CRUDO", "PROSC CRUDO", "SAN DANIELE", "PARMA", "IBERICO", "SERRANO"],
        "max_per_board": 2, # Ammettiamo max 2 crudi (es. un dolce e uno salato/iberico)
        "error_msg": "Più di due prosciutti crudi sbilanciano il tagliere. Inserisci salumi con texture diverse (es. macinati o cotti)."
    },
    "FORMAGGI_CROSTA_FIORITA": {
        "description": "Formaggi morbidi a crosta fiorita/lavata.",
        "members": ["BRIE", "CAMEMBERT", "TOMINO", "TALEGGIO", "CROSTA FIORITA"],
        "max_per_board": 1,
        "error_msg": "Evita di sovrapporre formaggi a crosta fiorita/lavata. Scegline uno solo e aggiungi un formaggio a pasta dura o erborinato."
    },
    "FORMAGGI_ERBORINATI": {
        "description": "Formaggi erborinati/blu (molto forti).",
        "members": ["GORGONZOLA", "ROQUEFORT", "STILTON", "ERBORINATO", "BLU"],
        "max_per_board": 1,
        "error_msg": "Più di un erborinato copre tutti i sapori. Massimo un formaggio blu per tagliere."
    },
    "FORMAGGI_FRESCHI_SPALMABILI": {
        "description": "Formaggi molto umidi, freschi o spalmabili.",
        "members": ["RICOTTA", "BURRATA", "STRACCIATELLA", "MOZZARELLA", "BUFALA", "CREMA SPALMABILE", "FRESCHI INDUSTRIALI", "FUSI"],
        "max_per_board": 1,
        "error_msg": "Troppi formaggi freschi/umidi rendono il tagliere 'bagnato'. Inserisci paste dure o semi-stagionati."
    },
    "TERRA_MARE_MIX": {
        "description": "Mix Terra e Mare vietato nello stesso tagliere (salvo richiesta esplicita).",
        "members_a": ["SALUMI", "CARNE"],
        "members_b": ["MARE", "ITTICO"],
        "allow_mix": False, # Di default non mescoliamo mare e monti
        "error_msg": "Hai mescolato referenze di mare e salumi di terra nello stesso tagliere. Separali o usa salumi di mare."
    }
}

def normalize_string(s: str) -> str:
    """Ritorna la stringa in maiuscolo senza spazi estremi."""
    if not s:
        return ""
    return s.strip().upper()

def check_board_violations(board_products: list, allow_terra_mare: bool = False) -> list:
    """
    Controlla un 'board' (lista di dizionari prodotto) contro la matrice di incompatibilità.
    Ritorna una lista di violazioni (stringhe). Se vuota, il board è APPROVED.

    FIX (analisi del 2024): il matching originale controllava SOLO i campi
    metadata sottocategoria/categoria_prodotto/reparto. Nel catalogo reale,
    circa metà delle referenze di salame/finocchiona/salamella sono
    classificate nel bucket generico "SALUMI INTERI/TRANCI" invece che in una
    sottocategoria specifica (è un'imprecisione della tassonomia originaria,
    non qualcosa che si può correggere qui): quel bucket non compare in
    nessun "members" della matrice, quindi due prodotti come "Finocchiona" e
    "Salame Toscano" potevano finire entrambi in quel bucket generico e
    superare il controllo senza essere mai contati come duplicati, anche se
    la regola SALUMI_MACINATI esiste ed è corretta. Per chiudere questo buco,
    ora controlliamo ANCHE la prima riga del testo del prodotto (nome
    commerciale), che contiene sempre la parola reale ("Finocchiona",
    "Salame"...) indipendentemente da come è stata taggata la sottocategoria.
    """
    violations = []
    
    # Raccogliamo le statistiche del board
    sc_counts = {}
    sc_examples = {}
    reparti = set()
    
    for prod in board_products:
        meta = prod.get("metadata", {})
        sc = normalize_string(meta.get("sottocategoria", ""))
        cat = normalize_string(meta.get("categoria_prodotto", ""))
        rep = normalize_string(meta.get("reparto", ""))
        # Nome commerciale reale del prodotto (prima riga del documento indicizzato):
        # è la rete di sicurezza contro i bucket di sottocategoria troppo generici
        # ("SALUMI INTERI/TRANCI", "ALTRI PRODOTTI", ecc.) che altrimenti farebbero
        # sfuggire il prodotto a ogni regola della matrice.
        documento = prod.get("document", "") or ""
        nome_prodotto = normalize_string(documento.splitlines()[0] if documento else "")

        # Consideriamo sottocategoria, categoria, reparto E nome prodotto per il match
        tags = [sc, cat, rep, nome_prodotto]
        
        reparti.add(rep)
        
        # Incrementiamo i contatori per le rule della matrice
        for rule_name, rule_data in INCOMPATIBILITY_MATRIX.items():
            if rule_name == "TERRA_MARE_MIX":
                continue
                
            members = [normalize_string(m) for m in rule_data.get("members", [])]
            # Se almeno un tag del prodotto è tra i members della regola (match esatto
            # sui campi di categoria, o il nome del membro compare come parola nel nome
            # prodotto reale)
            if any(tag in members or any(m in tag for m in members if m) for tag in tags):
                sc_counts[rule_name] = sc_counts.get(rule_name, 0) + 1
                sc_examples.setdefault(rule_name, []).append(nome_prodotto or prod.get("id", "?"))

    # Applichiamo le regole di cardinalità
    for rule_name, count in sc_counts.items():
        rule_data = INCOMPATIBILITY_MATRIX[rule_name]
        if count > rule_data["max_per_board"]:
            esempi = ", ".join(sc_examples.get(rule_name, [])[:4])
            violations.append(f"{rule_data['error_msg']} (referenze coinvolte: {esempi})")
            
    # Regola Terra-Mare
    if not allow_terra_mare:
        tm_rule = INCOMPATIBILITY_MATRIX["TERRA_MARE_MIX"]
        has_terra = any(r in tm_rule["members_a"] for r in reparti)
        has_mare = any(r in tm_rule["members_b"] for r in reparti)
        if has_terra and has_mare:
            violations.append(tm_rule["error_msg"])
            
    return violations


def prodotto_appartiene_a_famiglia(prodotto: dict, rule_name: str) -> bool:
    """Versione a singolo prodotto dello stesso matching usato in
    check_board_violations: usata dal motore di selezione (retrieval_utils.py)
    per evitare a monte di scegliere due prodotti della stessa famiglia,
    invece di scoprirlo solo a posteriori con un retry. Stessa logica di
    matching (metadata + nome prodotto), stessa fonte di verità: se cambi una
    delle due, cambia anche l'altra."""
    if rule_name not in INCOMPATIBILITY_MATRIX or rule_name == "TERRA_MARE_MIX":
        return False
    meta = prodotto.get("metadata", {})
    sc = normalize_string(meta.get("sottocategoria", ""))
    cat = normalize_string(meta.get("categoria_prodotto", ""))
    rep = normalize_string(meta.get("reparto", ""))
    documento = prodotto.get("document", "") or ""
    nome_prodotto = normalize_string(documento.splitlines()[0] if documento else "")
    tags = [sc, cat, rep, nome_prodotto]
    members = [normalize_string(m) for m in INCOMPATIBILITY_MATRIX[rule_name].get("members", [])]
    return any(tag in members or any(m in tag for m in members if m) for tag in tags)
