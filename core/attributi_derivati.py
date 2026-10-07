# -*- coding: utf-8 -*-
"""
attributi_derivati.py
=====================
Attributi calcolati con REGOLE (nessun LLM) dalla scheda prodotto: servono dove
la distinzione e' netta e verificabile nel testo, e l'errore costa caro.

  snack_tipo     famiglia da "ciotolina/aperitivo" del prodotto:
                 olive_salamoia | olive_olio | olive_altro | taralli | pane_croccante |
                 frutta_secca | patatine | snack_riso | ""  (non e' uno snack)
  conservazione  per le olive: salamoia | olio | candite | secche
  denocciolate   True se denocciolate/snocciolate

Richiesta del cliente (So Food): per "olive" si intendono SOLO quelle in
salamoia intere con nocciolo (tipo baresane, Cerignola, Peranzana), non le
denocciolate ne' quelle sott'olio, salvo richiesta esplicita.
"""

import re

from core.testo_prodotto import nome_prodotto

_RE_DENOCC = re.compile(r"denocciol|snocciol", re.IGNORECASE)
_RE_NON_OLIVA_DA_TAVOLA = re.compile(
    r"pat[eè]|crema|granell|cristalli|salsa|sugo|pesto|tapenade|pastell|in pastella|"
    r"filett|tonno|acciug|cernia|dentice|branzino|spada|ventresca|sgombro|palamita|tonnetto",
    re.IGNORECASE,
)


def sezione_ingredienti(doc: str) -> str:
    """Testo della sezione INGREDIENTI della scheda (vuoto se assente)."""
    m = re.search(r"INGREDIENTI:?\s*(.*?)(?:VALORI NUTRIZIONALI|CONSERVAZIONE|PRODUTTORE|ALLERGENI|$)",
                  doc or "", re.IGNORECASE | re.DOTALL)
    return (m.group(1) if m else "").replace("\n", " ").lower()


def _e_oliva_da_tavola(nome: str, meta: dict) -> bool:
    """Prodotto che E' (olive intere o denocciolate) e non un derivato/condimento."""
    n = nome.lower()
    if not re.search(r"\bolive\b", n):  # solo plurale: "olio oliva" (verdure sott'olio) non e' un'oliva
        return False
    return not _RE_NON_OLIVA_DA_TAVOLA.search(n)


def classifica_snack(meta: dict, doc: str) -> dict:
    nome = nome_prodotto(doc)
    n = nome.lower()
    sc = str(meta.get("sottocategoria") or "").upper()
    ing = sezione_ingredienti(doc)
    out = {"snack_tipo": "", "conservazione": "", "denocciolate": False}

    # --- olive -------------------------------------------------------------
    if meta.get("reparto") == "DISPENSA" and (sc in ("OLIVE", "SOTTOLI") or "oliv" in n) and _e_oliva_da_tavola(nome, meta):
        out["denocciolate"] = bool(_RE_DENOCC.search(n) or _RE_DENOCC.search(ing))
        if re.search(r"candit|zucchero", n + " " + ing):
            out["conservazione"], out["snack_tipo"] = "candite", "olive_altro"
        elif re.search(r"granell|essicc|cristalli", n + " " + ing):
            out["conservazione"], out["snack_tipo"] = "secche", "olive_altro"
        elif re.search(r"\bolio\b", ing) or re.search(r"sott[''’]?olio|sott[''’]?olii|in olio", n):
            out["conservazione"], out["snack_tipo"] = "olio", "olive_olio"
        elif re.search(r"\bacqua\b", ing) or "salamoia" in n:
            out["conservazione"], out["snack_tipo"] = "salamoia", "olive_salamoia"
        else:
            # ingredienti assenti o non leggibili: non si assume nulla (meglio escluderle dal default)
            out["conservazione"], out["snack_tipo"] = "", "olive_altro"
        return out

    # --- altri snack da ciotolina -----------------------------------------
    if sc == "TARALLI" or re.search(r"\btarall", n):
        out["snack_tipo"] = "taralli"
    elif sc in ("GRISSINI", "PANETTI CROCCANTI", "SPECIALITA' CROCCANTI") or re.search(r"grissin|crocchett|carasau|carasatu|guttiau|guttiàu|friselline|barchette", n):
        out["snack_tipo"] = "pane_croccante"
    elif sc == "PATATINE" or re.search(r"patatine|chips", n):
        out["snack_tipo"] = "patatine"
    elif sc == "GALLETTE" or re.search(r"\brise\b|gallette|soffiat", n):
        out["snack_tipo"] = "snack_riso"
    elif sc in ("FRUTTA SECCA SENZA GUSCIO", "FRUTTA SECCA") and not re.search(r"granella|pasta pura|crema", n):
        out["snack_tipo"] = "frutta_secca"
    elif re.search(r"\b(arachid|noccioline|anacard|mandorle tostate|pistacchi tostati)", n):
        out["snack_tipo"] = "frutta_secca"
    elif (meta.get("reparto") == "GELO"
          and sc.startswith(("SURG VEGETALI PREPARAT", "SURG PIATTI PRONTI", "SURG SPECIALITA"))
          and "parmigiana" not in n):
        # surgelati da friggere/rigenerare al banco (verdure pastellate, frittelline, pettole...)
        out["snack_tipo"] = "finger_food_caldo"
    return out


# ----------------------------------------------------------------------
# Verifica "vegano" sugli ingredienti (per i prodotti con flag SI* = dedotto)
# ----------------------------------------------------------------------
_RE_ANIMALI = re.compile(
    r"\b(latte|lattosio|siero|formaggi?o?|burro|panna|caseina|uova|uovo|albume|tuorlo|miele|carne|suino|maiale|bovino|"
    r"pollo|prosciutto|pesce|tonno|acciughe|alici|salmone|gelatina|strutto|lardo|gamberi|vongole|cozze|polpo|seppia|"
    r"calamari|crostacei|molluschi|yogurt|ricotta|mozzarella|pecorino|grana|parmigiano|cocciniglia|carminio|"
    r"cernia|dentice|branzino|orata|merluzzo|baccal[aà]|sgombro|tonnetto|granchio|aragosta|gamberi?|scampi?|"
    r"acciuga|sardine?|alice|insaccati|salame|speck|bresaola|mortadella|pancetta|guanciale|lonza|nduja)\b|\be(120|901|904|441)\b",
    re.IGNORECASE)
_RE_TRACCE_ANIMALI = re.compile(
    r"(puo|può)\s+contenere[^.]*\b(latte|uova|pesce|crostacei|molluschi)|tracce\s+di[^.]*\b(latte|uova|pesce|crostacei|molluschi)",
    re.IGNORECASE)


def vegano_ingredienti_ok(meta: dict, doc: str) -> bool:
    """True se il prodotto ha flag vegano 'SI*' (dedotto) E la lista ingredienti e' leggibile, senza
    ingredienti animali e senza 'tracce' di latte/uova/pesce. Serve all'opzione (spenta di default) di
    regole_cliente.yaml: regole_dieta.vegano.accetta_dedotto_se_ingredienti_vegetali."""
    if str(meta.get("vegano", "")).upper() != "SI*":
        return False
    ing = sezione_ingredienti(doc)
    if not ing.strip():
        return False
    ing_senza_tracce = re.sub(r"(puo|può)\s+contenere[^.]*|tracce[^.]*", "", ing, flags=re.IGNORECASE)
    if _RE_ANIMALI.search(ing_senza_tracce):
        return False
    return not _RE_TRACCE_ANIMALI.search((doc or "").lower())


# Fornitori inseriti senza i flag dietetici (nel DB risultano tutti "NO" per default, non per verifica).
# Per loro la dieta si deduce dagli ingredienti: "SI*" (dedotto) se la lista e' leggibile e senza ingredienti animali.
FORNITORI_SENZA_FLAG_DIETA = {"19010929", "19010930"}   # La Valletta, Pastificio Masciarelli


def completa_flag_dieta(meta: dict, doc: str) -> dict:
    """Ritorna i campi dieta da sovrascrivere per i fornitori senza flag (altrimenti {})."""
    if str(meta.get("codice_fornitore") or "") not in FORNITORI_SENZA_FLAG_DIETA:
        return {}
    if not vegano_ingredienti_ok({**meta, "vegano": "SI*"}, doc):
        return {}
    return {"vegano": "SI*", "vegetariano": "SI*"}


# ----------------------------------------------------------------------
# Flag vegano "SI" smentito dalla lista ingredienti (errore del dato sorgente)
# ----------------------------------------------------------------------
_RE_CARNE_PESCE = re.compile(r"\b(carne|suino|maiale|bovino|pollo|prosciutto|pesce|tonno|acciughe|alici|salmone|gamberi|vongole|"
                             r"cozze|polpo|seppia|calamari|crostacei|molluschi|mollusco|cernia|dentice|branzino|orata|"
                             r"merluzzo|sgombro|granchio|aragosta|salame|speck|bresaola|mortadella|pancetta|guanciale|nduja)\b", re.IGNORECASE)


def _ingredienti_animali(doc: str):
    """Primo ingrediente animale nella PRIMA parte della lista ingredienti (non nel testo di marketing che segue)."""
    ing = sezione_ingredienti(doc)[:350]
    ing = re.sub(r"(puo|può)\s+contenere[^.]*|tracce[^.]*", "", ing, flags=re.IGNORECASE)
    for m in _RE_ANIMALI.finditer(ing):
        if re.search(r"(senza|privo di|priva di|no|non contiene)\s+$", ing[max(0, m.start() - 18):m.start()], re.IGNORECASE):
            continue
        return m.group(0)
    return None


def correggi_flag_dieta(meta: dict, doc: str) -> dict:
    """Se il flag e' vegano 'SI' ma la lista ingredienti contiene un ingrediente animale, il dato sorgente e' sbagliato:
    si corregge a 'NO' (e vegetariano 'NO' se e' carne/pesce) e si segna `dato_dieta_corretto`.
    Meglio escludere un prodotto vegano dubbio che proporre al cliente vegano un prodotto con panna o seppia."""
    out = {}
    if str(meta.get("vegano") or "").upper() == "SI":
        a = _ingredienti_animali(doc)
        if a:
            out["vegano"] = "NO"
            out["dato_dieta_corretto"] = f"vegano SI -> NO: ingrediente '{a}'"
            if _RE_CARNE_PESCE.search(a) and str(meta.get("vegetariano") or "").upper().startswith("SI"):
                out["vegetariano"] = "NO"
    return out
