# -*- coding: utf-8 -*-
"""
guardrail_output.py
===================
Verifica A VALLE che i prodotti citati da Nino esistano davvero a catalogo (Fase 5 di
implementationplan.md, requisito "zero allucinazioni").

Perche': il prompt dice "non inventare" ma nulla lo controllava. Nel log chat 255b9688 Nino ha proposto
"Arancini di Riso Classici di Arancileria", "Crocchette di Patate di Delizie di Calabria" e "Olive
all'Ascolana di Sapori Marchigiani": nessuno dei tre (ne' i produttori) esiste nel database.

Come funziona (deterministico, nessuna chiamata LLM):
  1. estrae i nomi di prodotto citati (testo in **grassetto**, come chiede il prompt);
  2. cerca ciascun nome nel catalogo completo (nome + produttore): se nessun prodotto lo copre
     abbastanza -> NON VERIFICATO;
  3. se il cliente ha una dieta (vegano...) e il prodotto trovato non la rispetta -> INCOMPATIBILE;
  4. restituisce le citazioni problematiche, le righe da togliere dallo storico e una nota di correzione.
Il testo e' gia' stato mostrato in streaming: la correzione viene accodata come messaggio.
"""

import re
import unicodedata

# Parole che compaiono in grassetto ma non sono nomi di prodotto
_GENERICI = {
    "salumi", "formaggi", "mare", "pesce", "sottoli", "dispensa", "carne", "pane", "olive", "dolci", "antipasti",
    "primi", "secondi", "contorni", "bevande", "proposta", "tagliere", "tris", "aperitivo", "finger", "food",
    "nota", "attenzione", "consiglio", "totale", "quantita", "formato", "produttore", "codice", "prezzo",
}
_STOP = {"della", "delle", "degli", "dello", "dalla", "dalle", "nella", "nelle", "come", "alla", "alle", "agli",
         "sono", "sulla", "sulle", "senza", "oppure", "ideale", "ottimo", "ottima", "perfetto", "perfetta"}

_AZIENDE = {"salumificio", "caseificio", "pastificio", "azienda", "fornitore", "gruppo", "oleificio", "birrificio"}
SOGLIA_ASSENTE = 0.5     # sotto: il prodotto non esiste a catalogo -> si corregge
SOGLIA_COPERTURA = 0.7   # tra 0.5 e 0.7: probabile parafrasi del nome -> solo log, nessuna correzione al cliente

# Parole tipiche dei titoli di sezione e delle etichette generiche in grassetto
_TITOLI = {"selezione", "selezionati", "selezionata", "linea", "accompagnamento", "accompagnamenti", "retail", "horeca",
           "proposta", "struttura", "riepilogo", "nota", "servizio", "artigianale", "artigianali", "salume", "formaggio",
           "salumi", "formaggi", "dispensa", "affini", "verdure", "degustazione", "taglio", "croccante", "consigliato",
           "dolci", "contorno", "contorni", "abbinamento", "abbinamenti", "salumificio", "caseificio", "pastificio",
           "azienda", "fornitore", "gruppo", "produttore",
           # titoli di risposte su consegne/ordini e parole di comando ("**Ordine minimo e spedizione**", "**CONFERMO**")
           "tempi", "consegna", "consegne", "ordine", "ordini", "minimo", "spedizione", "spedizioni", "modalita",
           "pagamento", "pagamenti", "condizioni", "costi", "costo", "zona", "zone", "contatti", "confermo", "conferma",
           "opzione", "opzioni", "alternativa", "alternative", "sostituzioni", "consiglio", "chef", "upselling", "perche",
           "funziona", "allergeni", "logistica", "deposito", "sede", "orari", "riepilogo", "totale", "importante",
           # etichette delle schede ("**Temperatura**: refrigerato" era segnalato come prodotto inesistente, chat reale)
           "temperatura", "descrizione", "ingredienti", "conservazione", "formato", "formati", "peso", "origine",
           "caratteristiche", "gusto", "consistenza", "stagionatura", "utilizzo", "impiego", "uso", "dettagli", "scheda",
           "tecnica", "produzione", "lavorazione", "provenienza", "abbinamento", "tipologia", "pezzatura", "confezione",
           # titoli di struttura ("**Prodotto 1 (Crudo)**", "**Scelta principale**")
           "prodotto", "prodotti", "scelta", "scelte", "principale", "componente", "componenti", "crudo", "insaccato",
           "insaccati", "cotto", "cotti", "muscolo", "stagionato", "semiduro", "duro", "molle", "fresco", "erborinato",
           "base", "forno", "sfiziosita", "snack", "aperitivo", "struttura", "guidata", "completa"}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", t)


def _token(t: str) -> set:
    return {w for w in _norm(t).split() if len(w) >= 4 and w not in _STOP and w not in _TITOLI}


_CACHE: dict = {}


def _indice_token(indice_testuale: list) -> list:
    chiave = (id(indice_testuale), len(indice_testuale))
    if chiave not in _CACHE:
        from core.testo_prodotto import nome_prodotto
        voci = []
        for p in indice_testuale:
            m = p["metadata"]
            voci.append((_token(nome_prodotto(p["document"]) + " " + str(m.get("nome_fornitore") or "")
                                + " " + str(m.get("tipo_prodotto") or "")), p))
        _CACHE.clear()
        _CACHE[chiave] = voci
    return _CACHE[chiave]


def estrai_citazioni(testo: str) -> list:
    """Segmenti in **grassetto** che sembrano nomi di prodotto: (testo_riga, citazione)."""
    out = []
    for riga in (testo or "").splitlines():
        for m in re.finditer(r"\*\*([^*\n]{3,120}?)\*\*", riga):
            c = m.group(1).strip().strip(":").strip()
            if not c or c[0].islower():
                continue  # "pasta all'uovo", "olive da tavola": etichette generiche, non nomi di prodotto
            if re.search(r"[a-z]\d+\.\s|\s\d+\.\s", c) or len(c) > 90:
                continue  # markdown rotto ("Taralli Caserecci2. Olive"): non e' un prodotto inventato, non si cancella
            if riga.strip().strip("*").strip().rstrip(":").strip() == c or m.group(1).rstrip().endswith(":"):
                continue  # riga di sola intestazione ("**I Salumi:**")
            parole = [w for w in _norm(c).split() if len(w) >= 3]
            if parole and all(w in _TITOLI or w in _GENERICI for w in parole):
                continue  # solo parole da titolo di sezione ("Salume Artigianale")
            if parole and parole[0] in _AZIENDE:
                continue  # "Salumificio BBS": nome di azienda, non di prodotto
            tok = _token(c)
            if len(tok) < 2 and _norm(c).strip() in _GENERICI:
                continue
            if len(tok) == 0 or _norm(c).strip() in _GENERICI:
                continue
            # titoli di sezione tipo "I Salumi:" / elenchi di parole generiche
            if all(w in _GENERICI for w in _norm(c).split() if len(w) >= 3):
                continue
            out.append((riga, c))
    return out


def miglior_prodotti(citazione: str, indice_testuale: list):
    """(copertura 0-1, [prodotti a pari merito]) dei prodotti che meglio coprono i token della citazione.
    I pari merito contano: "Verdure al Naturale - Spicchi di Carciofi" coincide con due prodotti diversi."""
    tok = _token(citazione)
    if not tok:
        return 0.0, []
    migliore, best = 0.0, []
    for toks_p, p in _indice_token(indice_testuale):
        cop = len(tok & toks_p) / len(tok)
        if cop > migliore + 1e-9:
            migliore, best = cop, [p]
        elif cop > 0 and abs(cop - migliore) <= 1e-9:
            best.append(p)
    return migliore, best


def miglior_prodotto(citazione: str, indice_testuale: list):
    """(copertura 0-1, primo prodotto a pari merito)"""
    cop, best = miglior_prodotti(citazione, indice_testuale)
    return cop, (best[0] if best else None)


def verifica_prodotti_citati(testo: str, indice_testuale: list, dieta: "str | None" = None,
                             allergie: "list | None" = None) -> dict:
    """{"non_verificati": [(riga, citazione)], "incompatibili": [(riga, citazione, motivo)], "verificati": n}"""
    from core.retrieval_utils import prodotto_compatibile_con_dieta
    from core.allergeni import rischio

    non_ver, incomp, dubbi, ok, certi = [], [], [], 0, []
    visti = set()
    for riga, cit in estrai_citazioni(testo):
        if cit.lower() in visti:
            continue
        visti.add(cit.lower())
        cop, candidati = miglior_prodotti(cit, indice_testuale)
        if cop < SOGLIA_ASSENTE or not candidati:
            non_ver.append((riga, cit))
            continue
        if cop < SOGLIA_COPERTURA:
            dubbi.append((riga, cit, candidati[0]["id"]))
        ok += 1
        if cop >= SOGLIA_COPERTURA and len(candidati) == 1:
            certi.append((cit, candidati[0]["id"]))  # prodotto identificato senza ambiguita' (foto, focus)
        # incompatibile solo se NESSUN prodotto a pari merito rispetta la dieta
        if dieta and not any(prodotto_compatibile_con_dieta(c["metadata"], dieta) for c in candidati):
            incomp.append((riga, cit, f"non compatibile con la dieta {dieta}"))
        elif allergie and all(rischio(c["metadata"], c.get("document") or "", allergie) for c in candidati):
            incomp.append((riga, cit, "non sicuro per le allergie del cliente: "
                           + str(rischio(candidati[0]["metadata"], candidati[0].get("document") or "", allergie))))
    return {"non_verificati": non_ver, "incompatibili": incomp, "dubbi": dubbi, "verificati": ok, "certi": certi}


def ripulisci_testo(testo: str, esito: dict) -> str:
    """Toglie dallo storico le righe che citano prodotti non verificati o incompatibili
    (cosi' il modello non le considera 'gia' proposte' nei turni successivi)."""
    da_togliere = {riga for riga, _ in esito["non_verificati"]} | {riga for riga, *_ in esito["incompatibili"]}
    if not da_togliere:
        return testo
    return "\n".join(r for r in testo.splitlines() if r not in da_togliere)


def nota_correzione(esito: dict) -> str:
    """Messaggio da accodare alla risposta gia' mostrata (stringa vuota se tutto ok)."""
    parti = []
    if esito["non_verificati"]:
        nomi = ", ".join(f"«{c}»" for _, c in esito["non_verificati"])
        parti.append(f"Correzione: {nomi} non risulta nel nostro catalogo e non va considerato.")
    if esito["incompatibili"]:
        nomi = ", ".join(f"«{c}»" for _, c, _m in esito["incompatibili"])
        allergia = any("allergi" in m for _, _c, m in esito["incompatibili"])
        parti.append(f"Attenzione: {nomi} non e' adatto " + ("alle allergie che mi hai indicato" if allergia else "alla dieta richiesta")
                     + ", non considerarlo.")
    if not parti:
        return ""
    return "\n\n" + " ".join(parti) + " Dimmi pure se vuoi che cerchi un'alternativa tra i prodotti realmente disponibili."


# ---------------------------------------------------------------------------------------------
# Formati e canale (T3): "cartoni HORECA" detto di un prodotto da 300 g (caso reale)
# ---------------------------------------------------------------------------------------------
_RE_QTA = re.compile(r"(\d+(?:[.,]\d+)?)\s?(kg|chili|g|gr|grammi|l|lt|litri|litro|ml|cl)\b", re.IGNORECASE)


def _qta_base(valore: str, unita: str) -> float:
    v = float(valore.replace(",", "."))
    u = unita.lower()
    if u in ("kg", "chili"):
        return v * 1000
    if u in ("l", "lt", "litri", "litro"):
        return v * 1000
    if u == "cl":
        return v * 10
    return v


def _quantita_ammesse(p: dict) -> set:
    """Quantita' (g/ml) legittime per il prodotto: formato principale, multipli di pezzi, formati delle varianti."""
    from core.parse_formato import formato_prodotto
    m, doc = p["metadata"], p.get("document") or ""
    ammesse = set()
    f = formato_prodotto(m, doc)
    if f.get("valore"):
        ammesse.add(round(float(f["valore"]), 1))
        if f.get("pezzi"):
            ammesse.add(round(float(f["valore"]) * float(f["pezzi"]), 1))
    fonti = [str(m.get("formato_variante_liv5") or ""), str(m.get("varianti_prodotto") or ""),
             (doc.splitlines() or [""])[0]]
    for riga in doc.splitlines()[:12]:
        if riga.upper().startswith(("PESO", "FORMATO", "CONFEZION")):
            fonti.append(riga)
    for t in fonti:
        for mm in _RE_QTA.finditer(t):
            ammesse.add(round(_qta_base(mm.group(1), mm.group(2)), 1))
    return ammesse


_RE_NUTRIENTE = re.compile(r"(grass|protein|zuccher|carboidrat|\bsale\b|sodio|fibr|calori|kcal|energi|per cento grammi|"
                           r"per 100|ogni 100|su 100|valori nutrizional)", re.IGNORECASE)
_RE_PORZIONE = re.compile(r"(porzion|fett|assaggi|a testa|per persona|a persona|dose|dosi|taglio|pezzi? da|ciotolin)", re.IGNORECASE)


def _segmento_dopo(riga: str, cit: str) -> str:
    """Testo della riga che riguarda `cit`: dalla fine del suo grassetto al grassetto successivo, piu' una breve
    parte precedente (max 40 caratteri, solo se non contiene un altro grassetto)."""
    m = re.search(r"\*\*" + re.escape(cit) + r"\*\*", riga)
    if not m:
        return riga
    dopo = riga[m.end():]
    nxt = re.search(r"\*\*", dopo)
    if nxt:
        dopo = dopo[:nxt.start()]
    prima = riga[max(0, m.start() - 40):m.start()]
    if "**" in prima:
        prima = prima[prima.rindex("**") + 2:]
    return prima + " " + dopo


def verifica_formati(testo: str, indice_testuale: list) -> list:
    """Per ogni riga con un prodotto in grassetto verifica le frasi su canale (HORECA/RETAIL) e quantita' (g/kg/l).
    Ritorna [(riga, citazione, messaggio_corretto)]. Solo se il prodotto e' identificato senza ambiguita'."""
    from core.parse_formato import formato_prodotto
    out = []
    for riga, cit in estrai_citazioni(testo):
        cop, cand = miglior_prodotti(cit, indice_testuale)
        if cop < SOGLIA_COPERTURA or len(cand) != 1:
            continue
        p = cand[0]
        f = formato_prodotto(p["metadata"], p.get("document") or "")
        if not f.get("valore"):
            continue
        resto = _segmento_dopo(riga, cit)
        problemi = []
        # prodotti quasi identici per nome (es. stesso datterino in vasetto da 350 g e in latta da 400 g): valgono tutti
        simili = [q for toks_q, q in _indice_token(indice_testuale)
                  if q is p or len(_token(cit) & toks_q) / max(len(_token(cit)), 1) >= cop - 0.15]
        ammesse = set()
        for q in simili:
            ammesse |= _quantita_ammesse(q)
        tol = 0.35 if p["metadata"].get("peso_variabile") or f.get("peso_variabile") else 0.02
        for mm in _RE_QTA.finditer(resto):
            if (_RE_PORZIONE.search(resto[max(0, mm.start() - 28):mm.start()])
                    or re.match(r"\W*(a|per|di)\s+(persona|testa|porzione|commensale|coperto)", resto[mm.end():mm.end() + 25], re.IGNORECASE)):
                continue  # "porzioni da 40 g": e' una porzione, non il formato di vendita
            if _RE_NUTRIENTE.search(resto[mm.end():mm.end() + 40]) or _RE_NUTRIENTE.search(resto[max(0, mm.start() - 30):mm.start()]):
                continue  # "3,2 grammi di grassi per cento grammi": valori nutrizionali, non il formato
            q = round(_qta_base(mm.group(1), mm.group(2)), 1)
            if ammesse and not any(abs(q - a) <= max(1.0, a * tol) for a in ammesse):
                problemi.append(f"{mm.group(0)}")
        canale = f.get("canale_formato")
        if any(formato_prodotto(q["metadata"], q.get("document") or "").get("canale_formato") != canale for q in simili):
            canale = None  # canali diversi tra i quasi-omonimi: non si contesta
        r_low = resto.lower()
        if canale == "retail" and re.search(r"\bhoreca\b", r_low) and not re.search(r"\bretail\b", r_low):
            problemi.append("formato HORECA")
        if canale == "horeca" and re.search(r"\bretail\b", r_low) and not re.search(r"\bhoreca\b", r_low):
            problemi.append("formato RETAIL")
        if problemi:
            u = f.get("unita") or "g"
            val = f["valore"]
            descr = f"{val/1000:g} kg" if u == "g" and val >= 1000 else (f"{val/1000:g} L" if u == "ml" and val >= 1000 else f"{val:g} {u}")
            tipo = {"retail": "formato da banco/retail", "horeca": "formato HORECA"}.get(canale, "formato")
            out.append((riga, cit, f"{cit}: il formato a catalogo e' {descr} ({tipo}), non {', '.join(problemi)}"))
    return out


def nota_formati(errori: list) -> str:
    if not errori:
        return ""
    return "\n\nPrecisazione sui formati: " + "; ".join(m for _, _, m in errori) + "."


# ---------------------------------------------------------------------------------------------
# Produttori citati nel testo (T4): "prodotto da X", "firmato X", "(produttore X)" con X inesistente
# ---------------------------------------------------------------------------------------------
_RE_PRODUTTORE = re.compile(
    r"(?:prodott[oi]\s+da|prodotta\s+da|firmat[oi]\s+(?:da\s+)?|produttore\s*:?\s*|dell['’]azienda\s+|della\s+casa\s+|di\s+casa\s+)"
    r"\s*([A-Z][\w'’&.-]+(?:\s+(?:[A-Z][\w'’&.-]+|di|de|del|della|dei|e|&)){0,3})")
_PAROLE_NON_PRODUTTORE = {"italia", "puglia", "sud", "nord", "so", "food", "nostra", "nostro", "catalogo", "tradizione"}


def _nomi_fornitori(indice_testuale: list) -> set:
    return {_norm(str(p["metadata"].get("nome_fornitore") or "")).strip() for p in indice_testuale} - {""}


def verifica_produttori(testo: str, indice_testuale: list) -> list:
    """[(riga, nome_citato)] produttori citati che non esistono tra i fornitori del catalogo."""
    fornitori = _nomi_fornitori(indice_testuale)
    tok_forn = set()
    for f in fornitori:
        tok_forn |= {w for w in f.split() if len(w) >= 3}
    out, visti = [], set()
    for riga in (testo or "").splitlines():
        for m in _RE_PRODUTTORE.finditer(riga):
            nome = m.group(1).strip(" .,;:)")
            n = _norm(nome).strip()
            parole = [w for w in n.split() if len(w) >= 3 and w not in _STOP]
            if not parole or n in visti:
                continue
            if any(w in _PAROLE_NON_PRODUTTORE for w in parole):
                continue
            # esiste se almeno una parola significativa coincide con un fornitore del catalogo
            if any(w in tok_forn for w in parole) or any(n in f or f in n for f in fornitori):
                continue
            visti.add(n)
            out.append((riga, nome))
    return out
