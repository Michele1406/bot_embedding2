# -*- coding: utf-8 -*-
"""
tassonomia_sofood.py
=====================
SINGOLA FONTE DI VERITA' per la classificazione merceologica del catalogo
So Food, generata automaticamente da Tassonomia.xlsx (standard ECR Grocery)
con genera_tassonomia_sofood.py. NON modificare questo file a mano: quando
Tassonomia.xlsx cambia, rilancia genera_tassonomia_sofood.py.

Nota importante: Reparto e la vecchia "Categoria Prodotto" sono ORA LO
STESSO CAMPO (unificati nel nuovo Excel): 6 macro-aree, sempre in MAIUSCOLO
-> CARNE, DISPENSA, FORMAGGI, GELO, MARE, SALUMI.

TASSONOMIA_LISTA contiene le combinazioni Reparto > Categoria Tassonomia >
Sottocategoria realmente presenti a catalogo. Il campo "n" è il numero di
prodotti reali con quella esatta combinazione nell'ultima Tassonomia.xlsx
caricata: serve a MAPPA_SOTTOCATEGORIA qui sotto per scegliere, quando una
sottocategoria compare sotto più di una Categoria Tassonomia (capita per una
manciata di voci: è un'imprecisione ereditata dalla classificazione LLM di
origine, non un bug di questo file), la combinazione più frequente invece
che la prima in ordine alfabetico.
"""

TASSONOMIA_LISTA = [
    {"sottocategoria": 'ALTRE CARNI', "categoria": 'ALTRE CARNI', "reparto": 'CARNE', "n": 2},
    {"sottocategoria": 'POLLO', "categoria": 'AVICUNICOLO', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'COSCIA', "categoria": 'BOVINO ADULTO', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'ALTRI ELABORATI CRUDI', "categoria": 'III LAVORAZIONE', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'HAMBURGER', "categoria": 'III LAVORAZIONE', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'MACINATO', "categoria": 'III LAVORAZIONE', "reparto": 'CARNE', "n": 2},
    {"sottocategoria": 'ALTRI', "categoria": 'SCOTTONA', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'ANTERIORE', "categoria": 'SCOTTONA', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'COSTATA', "categoria": 'SCOTTONA', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'LOMBATA', "categoria": 'SCOTTONA', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'PROSCIUTTO', "categoria": 'SUINO', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'SPALLA', "categoria": 'SUINO', "reparto": 'CARNE', "n": 1},
    {"sottocategoria": 'BIRRE ALCOLICHE', "categoria": 'BIRRE', "reparto": 'DISPENSA', "n": 6},
    {"sottocategoria": 'CEREALI', "categoria": 'CEREALI E ZUPPE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'ALTRE CONSERVE PESCE', "categoria": 'CONSERVE ANIMALI E FORMAGGI', "reparto": 'DISPENSA', "n": 3},
    {"sottocategoria": "PATE' E SPALMABILI SALATI", "categoria": 'CONSERVE ANIMALI E FORMAGGI', "reparto": 'DISPENSA', "n": 8},
    {"sottocategoria": 'PASSATA DI POMODORO', "categoria": 'DERIVATI DEL POMODORO', "reparto": 'DISPENSA', "n": 9},
    {"sottocategoria": 'PELATI E POMODORINI', "categoria": 'DERIVATI DEL POMODORO', "reparto": 'DISPENSA', "n": 46},
    {"sottocategoria": 'POLPA DI POMODORO', "categoria": 'DERIVATI DEL POMODORO', "reparto": 'DISPENSA', "n": 4},
    {"sottocategoria": 'BASI PER PIZZA E IMPASTI', "categoria": 'DOLCI E PRODOTTI DA FORNO', "reparto": 'DISPENSA', "n": 3},
    {"sottocategoria": "BISCOTTI ALL'UOVO", "categoria": 'DROGHERIA ALIMENTARE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'DERIVATI DEL POMODORO', "categoria": 'DROGHERIA ALIMENTARE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'FRUTTA SECCA SENZA GUSCIO', "categoria": 'DROGHERIA ALIMENTARE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'INSAPORITORI', "categoria": 'DROGHERIA ALIMENTARE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'OLIO, ACETO E SUCCO LIMONE', "categoria": 'DROGHERIA ALIMENTARE', "reparto": 'DISPENSA', "n": 5},
    {"sottocategoria": 'SNACK DOLCI', "categoria": 'DROGHERIA ALIMENTARE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'SUGHI,SALSE E CONDIMENTI', "categoria": 'DROGHERIA ALIMENTARE', "reparto": 'DISPENSA', "n": 4},
    {"sottocategoria": 'VEGETALI CONSERVATI', "categoria": 'DROGHERIA ALIMENTARE', "reparto": 'DISPENSA', "n": 6},
    {"sottocategoria": 'FRUTTA CONSERVATA', "categoria": 'FRUTTA CONSERVATA E SOTTO SPIRITO', "reparto": 'DISPENSA', "n": 5},
    {"sottocategoria": "FRUTTA E BABA' SOTTO SPIRITO", "categoria": 'FRUTTA CONSERVATA E SOTTO SPIRITO', "reparto": 'DISPENSA', "n": 6},
    {"sottocategoria": 'MOSTARDA', "categoria": 'FRUTTA CONSERVATA E SOTTO SPIRITO', "reparto": 'DISPENSA', "n": 2},
    {"sottocategoria": 'FRUTTA ESSICCATA/DISIDR/RICOP', "categoria": 'FRUTTA E VEGETALI SECCHI', "reparto": 'DISPENSA', "n": 4},
    {"sottocategoria": 'FRUTTA SECCA SENZA GUSCIO', "categoria": 'FRUTTA E VEGETALI SECCHI', "reparto": 'DISPENSA', "n": 6},
    {"sottocategoria": 'SEMI', "categoria": 'FRUTTA E VEGETALI SECCHI', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'VEGETALI SECCHI', "categoria": 'FRUTTA E VEGETALI SECCHI', "reparto": 'DISPENSA', "n": 5},
    {"sottocategoria": 'PATATINE', "categoria": 'FUORI PASTO SALATI', "reparto": 'DISPENSA', "n": 3},
    {"sottocategoria": 'FARINE E MISCELE', "categoria": 'INGREDIENTI BASE', "reparto": 'DISPENSA', "n": 31},
    {"sottocategoria": 'INGREDIENTI PER PASTICCERIA', "categoria": 'INGREDIENTI BASE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'AROMI E SPEZIE', "categoria": 'INSAPORITORI', "reparto": 'DISPENSA', "n": 94},
    {"sottocategoria": 'SALE', "categoria": 'INSAPORITORI', "reparto": 'DISPENSA', "n": 5},
    {"sottocategoria": 'SOLUZIONI CULINARIE', "categoria": 'INSAPORITORI', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'ALTRI LIQUORI', "categoria": 'LIQUORI', "reparto": 'DISPENSA', "n": 9},
    {"sottocategoria": 'ACETO', "categoria": 'OLIO, ACETO E SUCCO LIMONE', "reparto": 'DISPENSA', "n": 12},
    {"sottocategoria": 'ALTRI CONDIMENTI', "categoria": 'OLIO, ACETO E SUCCO LIMONE', "reparto": 'DISPENSA', "n": 7},
    {"sottocategoria": 'OLIO DI SEMI', "categoria": 'OLIO, ACETO E SUCCO LIMONE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'OLIO EXTRAVERGINE DI OLIVA', "categoria": 'OLIO, ACETO E SUCCO LIMONE', "reparto": 'DISPENSA', "n": 50},
    {"sottocategoria": 'GALLETTE', "categoria": 'PANE E SOSTITUTIVI', "reparto": 'DISPENSA', "n": 2},
    {"sottocategoria": 'GRISSINI', "categoria": 'PANE E SOSTITUTIVI', "reparto": 'DISPENSA', "n": 7},
    {"sottocategoria": 'PANETTI CROCCANTI', "categoria": 'PANE E SOSTITUTIVI', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'PANINI', "categoria": 'PANE E SOSTITUTIVI', "reparto": 'DISPENSA', "n": 3},
    {"sottocategoria": 'PIADINE', "categoria": 'PANE E SOSTITUTIVI', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": "SPECIALITA' CROCCANTI", "categoria": 'PANE E SOSTITUTIVI', "reparto": 'DISPENSA', "n": 6},
    {"sottocategoria": "SPECIALITA' MORBIDE", "categoria": 'PANE E SOSTITUTIVI', "reparto": 'DISPENSA', "n": 2},
    {"sottocategoria": 'TARALLI', "categoria": 'PANE E SOSTITUTIVI', "reparto": 'DISPENSA', "n": 2},
    {"sottocategoria": "PASTA ALL'UOVO", "categoria": 'PASTA', "reparto": 'DISPENSA', "n": 9},
    {"sottocategoria": 'PASTA DI SEMOLA', "categoria": 'PASTA', "reparto": 'DISPENSA', "n": 70},
    {"sottocategoria": 'PASTA INT/FAR/KAMUT/LEG/MAIS', "categoria": 'PASTA', "reparto": 'DISPENSA', "n": 12},
    {"sottocategoria": 'PIZZE E PREPARATI', "categoria": 'PREPARATI E PIATTI PRONTI', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'PREPARATI PRIMI PIATTI', "categoria": 'PREPARATI E PIATTI PRONTI', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'BISCOTTI TRADIZIONALI', "categoria": 'PRODOTTI FORNO E CEREALI', "reparto": 'DISPENSA', "n": 5},
    {"sottocategoria": 'PASTICCERIA', "categoria": 'PRODOTTI FORNO E CEREALI', "reparto": 'DISPENSA', "n": 6},
    {"sottocategoria": 'ALTRI PRODOTTI RICORRENZA', "categoria": 'RICORRENZE', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'RISO BIANCO', "categoria": 'RISO', "reparto": 'DISPENSA', "n": 14},
    {"sottocategoria": 'RISO PARBOILED', "categoria": 'RISO', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": "SPECIALITA' RISO", "categoria": 'RISO', "reparto": 'DISPENSA', "n": 2},
    {"sottocategoria": 'CONFETTURE/SPALMABILI FRUTTA', "categoria": 'SPALMABILI DOLCI', "reparto": 'DISPENSA', "n": 46},
    {"sottocategoria": 'CREME SPALMABILI DOLCI', "categoria": 'SPALMABILI DOLCI', "reparto": 'DISPENSA', "n": 3},
    {"sottocategoria": 'MIELE', "categoria": 'SPALMABILI DOLCI', "reparto": 'DISPENSA', "n": 1},
    {"sottocategoria": 'MAIONESE', "categoria": 'SUGHI,SALSE E CONDIMENTI', "reparto": 'DISPENSA', "n": 7},
    {"sottocategoria": 'SALSE/SPALMABILI VEGETALI', "categoria": 'SUGHI,SALSE E CONDIMENTI', "reparto": 'DISPENSA', "n": 23},
    {"sottocategoria": 'SUGHI PRONTI E BASI', "categoria": 'SUGHI,SALSE E CONDIMENTI', "reparto": 'DISPENSA', "n": 25},
    {"sottocategoria": 'ALTRI LEGUMI/VEGETALI/CEREALI', "categoria": 'VEGETALI CONSERVATI', "reparto": 'DISPENSA', "n": 36},
    {"sottocategoria": 'FAGIOLI CONSERVATI', "categoria": 'VEGETALI CONSERVATI', "reparto": 'DISPENSA', "n": 2},
    {"sottocategoria": 'OLIVE', "categoria": 'VEGETALI CONSERVATI', "reparto": 'DISPENSA', "n": 34},
    {"sottocategoria": 'SOTTACETI', "categoria": 'VEGETALI CONSERVATI', "reparto": 'DISPENSA', "n": 19},
    {"sottocategoria": 'SOTTOLI', "categoria": 'VEGETALI CONSERVATI', "reparto": 'DISPENSA', "n": 63},
    {"sottocategoria": 'ALTRE PASTE FILATE FRESCHE', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 1},
    {"sottocategoria": 'ALTRI FORM FRESCHI TRADIZ', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 36},
    {"sottocategoria": 'CACIOTTE E ITALICI', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 1},
    {"sottocategoria": 'FORMAGGI DA TAV. INTERI/PORZION.', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 37},
    {"sottocategoria": 'FORMAGGI ELABORATI', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 3},
    {"sottocategoria": 'FORMAGGI FRESCHI INDUSTRIALI', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 4},
    {"sottocategoria": 'FORMAGGI FUSI', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 4},
    {"sottocategoria": 'GORGONZOLA', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 1},
    {"sottocategoria": 'GRANA E SIMILI', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 6},
    {"sottocategoria": 'MOZZARELLE', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 7},
    {"sottocategoria": 'PASTE FILATE STAGIONATE', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 2},
    {"sottocategoria": 'PASTE FILATE USO CUCINA', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 1},
    {"sottocategoria": 'PECORINO', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 10},
    {"sottocategoria": 'RICOTTA', "categoria": 'FORMAGGI', "reparto": 'FORMAGGI', "n": 1},
    {"sottocategoria": 'BUFALA', "categoria": 'MOZZARELLE', "reparto": 'FORMAGGI', "n": 4},
    {"sottocategoria": 'GELATI DESSERT', "categoria": 'GELATI', "reparto": 'GELO', "n": 6},
    {"sottocategoria": 'GELATI VASCHETTE', "categoria": 'GELATI', "reparto": 'GELO', "n": 15},
    {"sottocategoria": 'SORBETTO DA BERE', "categoria": 'GELATI DESSERT', "reparto": 'GELO', "n": 6},
    {"sottocategoria": '> 1000 GR', "categoria": 'GELATI VASCHETTE', "reparto": 'GELO', "n": 6},
    {"sottocategoria": 'SURG DOLCI/PASTICCERIA', "categoria": 'SURGELATI', "reparto": 'GELO', "n": 1},
    {"sottocategoria": 'SURG PESCE NATURALE', "categoria": 'SURGELATI', "reparto": 'GELO', "n": 2},
    {"sottocategoria": 'SURG PIATTI PRONTI', "categoria": 'SURGELATI', "reparto": 'GELO', "n": 2},
    {"sottocategoria": "SURG SPECIALITA' SALATE", "categoria": 'SURGELATI', "reparto": 'GELO', "n": 5},
    {"sottocategoria": 'SURG VEGETALI NATURALI/FRUTTA', "categoria": 'SURGELATI', "reparto": 'GELO', "n": 8},
    {"sottocategoria": 'SURG VEGETALI PREPARATI', "categoria": 'SURGELATI', "reparto": 'GELO', "n": 19},
    {"sottocategoria": 'ALTRI', "categoria": 'ALTRI PRODOTTI', "reparto": 'MARE', "n": 12},
    {"sottocategoria": 'ALTRI PRODOTTI', "categoria": 'ITTICO', "reparto": 'MARE', "n": 96},
    {"sottocategoria": 'III LAVORAZIONI FRESCHE CONFEZIONATE', "categoria": 'ITTICO', "reparto": 'MARE', "n": 2},
    {"sottocategoria": 'ITTICO FRESCO CONFEZIONATO', "categoria": 'ITTICO', "reparto": 'MARE', "n": 43},
    {"sottocategoria": 'PESCE DECONGELATO', "categoria": 'ITTICO', "reparto": 'MARE', "n": 5},
    {"sottocategoria": 'SALMONE FRESCO CONFEZIONATO', "categoria": 'ITTICO FRESCO CONFEZIONATO', "reparto": 'MARE', "n": 6},
    {"sottocategoria": 'PROSC COTTO', "categoria": 'AFFETTATI', "reparto": 'SALUMI', "n": 5},
    {"sottocategoria": 'SALAME', "categoria": 'AFFETTATI', "reparto": 'SALUMI', "n": 1},
    {"sottocategoria": 'AFFETTATI', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 4},
    {"sottocategoria": 'ALTRI', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 4},
    {"sottocategoria": 'ARROSTI', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 3},
    {"sottocategoria": 'MORTADELLA', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 3},
    {"sottocategoria": 'PANCETTA', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 1},
    {"sottocategoria": 'PROSC CRUDO', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 4},
    {"sottocategoria": 'SALAME', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 9},
    {"sottocategoria": 'SALAMI', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 4},
    {"sottocategoria": 'SALUMI INTERI/TRANCI', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 67},
    {"sottocategoria": 'SALUMI QUADRETTATI', "categoria": 'SALUMI', "reparto": 'SALUMI', "n": 1},
    {"sottocategoria": 'ARROSTI', "categoria": 'SALUMI INTERI/TRANCI', "reparto": 'SALUMI', "n": 2},
    {"sottocategoria": 'BRESAOLA', "categoria": 'SALUMI INTERI/TRANCI', "reparto": 'SALUMI', "n": 2},
    {"sottocategoria": 'PROSC COTTO', "categoria": 'SALUMI INTERI/TRANCI', "reparto": 'SALUMI', "n": 1},
    {"sottocategoria": 'PROSC CRUDO', "categoria": 'SALUMI INTERI/TRANCI', "reparto": 'SALUMI', "n": 1},

]

# ============================================================================
# Strutture derivate (calcolate automaticamente da TASSONOMIA_LISTA, non
# toccarle a mano)
# ============================================================================
REPARTI = sorted({v["reparto"] for v in TASSONOMIA_LISTA})
SOTTOCATEGORIE_NOMI = sorted({v["sottocategoria"] for v in TASSONOMIA_LISTA})

# Mappa sottocategoria (lowercase) -> dict completo {sottocategoria, categoria, reparto}.
# Quando una sottocategoria compare sotto più di una combinazione categoria/
# reparto, si tiene quella con più prodotti reali (campo "n"), non la prima
# trovata in ordine alfabetico.
MAPPA_SOTTOCATEGORIA = {}
for _voce in sorted(TASSONOMIA_LISTA, key=lambda v: v["n"], reverse=True):
    _chiave = _voce["sottocategoria"].strip().lower()
    if _chiave not in MAPPA_SOTTOCATEGORIA:
        MAPPA_SOTTOCATEGORIA[_chiave] = _voce

# Mappa Categoria Tassonomia (lowercase) -> reparto (comodo per filtri ampi).
MAPPA_CATEGORIA_REPARTO = {}
for _voce in TASSONOMIA_LISTA:
    _chiave = _voce["categoria"].strip().lower()
    MAPPA_CATEGORIA_REPARTO.setdefault(_chiave, _voce["reparto"])


# ============================================================================
# CLASSIFICAZIONE "TERRA / MARE" (usata per il consiglio commerciale, non per
# filtri di esclusione: un locale di mare NON deve vedersi negare la carne,
# vedi regole in system_prompt_v2.py e retrieval_utils.py)
# ============================================================================
REPARTI_MARE = {"MARE"}
REPARTI_TERRA = {"CARNE", "SALUMI"}
REPARTI_NEUTRI = {"FORMAGGI", "DISPENSA", "GELO"}


def classifica_terra_mare(reparto: str) -> str:
    """Ritorna 'mare', 'terra' o 'neutro' a partire dal reparto ufficiale del
    prodotto. Usata per capire quali referenze spingere per primi in un
    locale di mare (senza per questo vietare le altre)."""
    r = (reparto or "").strip().upper()
    if r in REPARTI_MARE:
        return "mare"
    if r in REPARTI_TERRA:
        return "terra"
    return "neutro"
