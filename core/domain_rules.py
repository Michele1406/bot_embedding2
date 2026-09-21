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
    """
    violations = []
    
    # Raccogliamo le statistiche del board
    sc_counts = {}
    reparti = set()
    
    for prod in board_products:
        meta = prod.get("metadata", {})
        sc = normalize_string(meta.get("sottocategoria", ""))
        cat = normalize_string(meta.get("categoria_prodotto", ""))
        rep = normalize_string(meta.get("reparto", ""))
        
        # Consideriamo sia sottocategoria che categoria per il match
        tags = [sc, cat, rep]
        
        reparti.add(rep)
        
        # Incrementiamo i contatori per le rule della matrice
        for rule_name, rule_data in INCOMPATIBILITY_MATRIX.items():
            if rule_name == "TERRA_MARE_MIX":
                continue
                
            members = [normalize_string(m) for m in rule_data.get("members", [])]
            # Se almeno un tag del prodotto è tra i members della regola
            if any(tag in members or any(m in tag for m in members if m) for tag in tags):
                sc_counts[rule_name] = sc_counts.get(rule_name, 0) + 1

    # Applichiamo le regole di cardinalità
    for rule_name, count in sc_counts.items():
        rule_data = INCOMPATIBILITY_MATRIX[rule_name]
        if count > rule_data["max_per_board"]:
            violations.append(rule_data["error_msg"])
            
    # Regola Terra-Mare
    if not allow_terra_mare:
        tm_rule = INCOMPATIBILITY_MATRIX["TERRA_MARE_MIX"]
        has_terra = any(r in tm_rule["members_a"] for r in reparti)
        has_mare = any(r in tm_rule["members_b"] for r in reparti)
        if has_terra and has_mare:
            violations.append(tm_rule["error_msg"])
            
    return violations
