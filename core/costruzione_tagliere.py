# -*- coding: utf-8 -*-
"""
Costruzione del tagliere INSIEME al cliente, passo passo (richiesta del cliente So Food, 2026-10-06).

  1. Nino propone un tagliere. Se il cliente non e' convinto ("non mi piace", "non mi convince") -> Nino chiede se
     vuole costruirlo insieme e quanti salumi/formaggi (se li aveva gia' detti, li conferma invece di richiederli).
  2. Nino propone una lista numerata di salumi (10, di tipologie diverse); il cliente sceglie ("1, 3 e 5", "il
     primo", o per nome). Le scelte restano; se ne mancano, Nino propone ALTRI salumi mai mostrati, fino al numero.
  3. Stesso giro per i formaggi.
  4. Riepilogo della composizione scelta, raccontata, con gli abbinamenti verificati.

Tutto deterministico (nessuna chiamata API): lo stato vive in stato["costruzione"] (serializzabile: solo id).
Le liste numerate e le domande sono testi FISSI ("testo_fisso": numeri e nomi esatti, nessuna chiamata API: in prova
il modello rinumerava le liste); solo il riepilogo finale lo scrive il modello dal blocco [COSTRUZIONE TAGLIERE INSIEME].

  turno(stato, testo, indice_testuale) -> None (il messaggio non riguarda la costruzione) oppure
                                           {"fase", "prodotti", "testo_fisso" | "contesto"}
"""
import re

N_PROPOSTE = 10
RUOLI = ("salumi", "formaggi")

_RE_NON_CONVINCE = re.compile(
    r"\b(non mi (piace|piacciono|convince|convincono|soddisfa|garba|ispira|entusiasma)|non (e'|è) quello che|"
    r"non va bene|non vanno bene|non fa per me|cambia(mo)? tutto|rifacciamo|da rifare|bocciat[oa]|per niente|"
    r"neanche un po|non ci siamo|non mi torna)\b", re.IGNORECASE)
_RE_COSTRUIRE = re.compile(r"\b(costruiamol[oa]|creiamol[oa]|facciamol[oa]|componiamol[oa]|scegliamo(l[oa])?|"
                           r"lo (facciamo|costruiamo|creiamo) insieme|aiutami a scegliere|passo passo)\b", re.IGNORECASE)
_RE_SI = re.compile(r"^\s*(s[iì]|ok|okay|va bene|certo|perfetto|d'accordo|volentieri|esatto|confermo|giusto|dai|"
                    r"s[iì] grazie|ottimo|benissimo)\b", re.IGNORECASE)
_RE_NO = re.compile(r"^\s*(no|non serve|lascia stare|fai tu|decidi tu|scegli tu|pensaci tu)\b", re.IGNORECASE)
_RE_ALTRI = re.compile(r"\b(altr[ie]|nessuno|nessuna|non mi piace nessun|fammene vedere|mostrami|cambiali|diversi)\b",
                       re.IGNORECASE)
_RE_USCITA = re.compile(r"\b(primo|secondo piatto|pizza|panino|dolce|dessert|menu|tris|ordin\w*|consegn\w*)\b",
                        re.IGNORECASE)
_NUM = {"un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7, "otto": 8,
        "nove": 9, "dieci": 10}
_ORDINALI = {"primo": 1, "prima": 1, "secondo": 2, "seconda": 2, "terzo": 3, "terza": 3, "quarto": 4, "quarta": 4,
             "quinto": 5, "quinta": 5, "sesto": 6, "sesta": 6, "settimo": 7, "settima": 7, "ottavo": 8, "ottava": 8,
             "nono": 9, "nona": 9, "decimo": 10, "decima": 10, "ultimo": -1, "ultima": -1}
_N = r"(\d+|un|uno|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)"


def _num(t: str) -> int:
    return int(t) if t.isdigit() else _NUM.get(t.lower(), 0)


def non_convince(testo: str) -> bool:
    return bool(_RE_NON_CONVINCE.search(testo or ""))


def vuole_costruire(testo: str) -> bool:
    return bool(_RE_COSTRUIRE.search(testo or ""))


def conteggi_espliciti(testo: str, attesa_coppia: bool = False) -> tuple:
    """(salumi, formaggi) SOLO se detti: "4 salumi e 2 formaggi", "salumi e formaggi 3 e 3", "2 e 5" (quando Nino ha
    appena chiesto i numeri). None dove non detto (mai valori di default: li si chiede)."""
    t = (testo or "").lower()
    s = re.search(_N + r"\s+(?:tipi di\s+|referenze di\s+)?(?:salum|prosciutt|affettat)", t)
    f = re.search(_N + r"\s+(?:tipi di\s+|referenze di\s+)?(?:formagg|caci)", t)
    sal, form = (_num(s.group(1)) if s else None), (_num(f.group(1)) if f else None)
    coppia = re.search(r"salumi e formaggi\W{0,3}" + _N + r"\s+e\s+" + _N, t)
    if not coppia and attesa_coppia:
        coppia = re.search(r"(?<![\w])" + _N + r"\s+e\s+" + _N + r"(?![\w])", t)
    if coppia and sal is None and form is None:
        sal, form = _num(coppia.group(1)), _num(coppia.group(2))
    if re.search(r"\b(niente|senza|no) formagg", t):
        form = 0
    if re.search(r"\b(niente|senza|no) salum", t):
        sal = 0
    return sal, form


def scelte(testo: str, lista: list, nomi: dict) -> list:
    """Id scelti dalla lista numerata: numeri ("1, 3 e 5"), ordinali ("il primo e l'ultimo") o nomi ("la coppa")."""
    t = (testo or "").lower()
    out = []
    if re.search(r"\b(tutti|tutte)\b", t):
        return list(lista)
    for m in re.finditer(r"(?<![\w])(?<![.,]\d)(\d{1,2})(?![\w]|[.,]\d|\s*(?:salum|formagg|kg|g\b|gr))", t):
        k = int(m.group(1))
        if 1 <= k <= len(lista):
            out.append(lista[k - 1])
    for w, k in _ORDINALI.items():
        if re.search(r"\b" + w + r"\b", t) and lista:
            out.append(lista[k - 1] if k > 0 else lista[-1])
    if not out:
        parole_t = {w[:5] for w in re.findall(r"[a-zàèéìòù]{4,}", t)}
        for i in lista:
            proprie = {w[:5] for w in re.findall(r"[a-zàèéìòù]{4,}", nomi.get(i, "").lower())}
            altre = set().union(*[{w[:5] for w in re.findall(r"[a-zàèéìòù]{4,}", nomi.get(j, "").lower())}
                                  for j in lista if j != i]) if len(lista) > 1 else set()
            if (proprie - altre) & parole_t:
                out.append(i)
    return list(dict.fromkeys(out))


_RE_DOMANDA = re.compile(r"\?|^\s*(com'?\s?[eè]|cos'?\s?[eè]|che |quale|qual |dimmi|parlami|raccontami|descrivi|"
                         r"[eè] |sono |ha |hanno |quanto)", re.IGNORECASE)
_RE_SCELGO = re.compile(r"\b(prendo|scelgo|voglio|metti|mettiamo|vanno bene|va bene|ok|vada per|teniamo|tengo|"
                        r"preferisco|mi piac\w+)\b", re.IGNORECASE)


def e_domanda(testo: str) -> bool:
    """"Com'e' il terzo?" chiede informazioni, non sceglie (salvo "prendo il terzo, com'e' fatto?")."""
    return bool(_RE_DOMANDA.search(testo or "")) and not _RE_SCELGO.search(testo or "")


def nominati(stato: dict, testo: str, indice_testuale: list) -> list:
    """Prodotti della lista numerata a cui si riferisce una domanda ("com'e' il terzo?", "il 2 e' piccante?")."""
    c = stato.get("costruzione")
    if not c or not c.get("lista") or not e_domanda(testo):
        return []
    per_id = {p["id"]: p for p in indice_testuale}
    ids = scelte(testo, c["lista"], {i: _nome(per_id[i]) for i in c["lista"] if i in per_id})
    return [per_id[i] for i in ids if i in per_id]


def _conteggi_dalla_chat(stato: dict) -> tuple:
    """Numeri gia' detti dal cliente nei messaggi recenti (il piu' recente vince)."""
    sal = form = None
    for m in reversed([x for x in stato.get("log_chat", []) if x.get("ruolo") == "utente"][-8:]):
        s, f = conteggi_espliciti(m.get("testo", ""), attesa_coppia=bool(re.search(r"salum|formagg|taglier", m.get("testo", ""), re.I)))
        sal = s if sal is None else sal
        form = f if form is None else form
        if sal is not None and form is not None:
            break
    return sal, form


def _regione_dalla_chat(stato: dict):
    from core import territorio
    for m in reversed([x for x in stato.get("log_chat", []) if x.get("ruolo") == "utente"][-8:]):
        if "taglier" in m.get("testo", "").lower():
            return territorio.regione_richiesta(m["testo"])
    return None


def candidati(ruolo: str, c: dict, indice_testuale: list, n: int = N_PROPOSTE) -> list:
    """Fino a n prodotti da tagliere del ruolo, mai proposti prima, di tipologie diverse (stesse regole del tagliere:
    dieta, esclusioni, allergie, zona); se c'e' una regione, prima i suoi prodotti."""
    from core import ricettario, territorio
    gia = set(c["proposti"][ruolo]) | set(c["scelti"][ruolo])
    pool = ricettario._complemento_tagliere(ruolo, indice_testuale, gia, ricettario._QUALITA_ESCLUSIONI_TAGLIERE.get(ruolo, ()),
                                            "", None, set())
    pool = territorio.ordina_per_regione(pool, c.get("regione"))
    # prima le tipologie che il cliente non ha ancora scelto (ha preso un crudo: la rosa successiva parte da altro)
    from core import famiglie_tagliere as ft
    fam_scelte = set()
    for p in indice_testuale:
        if p["id"] in c["scelti"][ruolo]:
            fam_scelte.add(ft.famiglia_prodotto(p["metadata"], p.get("document", "")))
    nuove = [p for p in pool if ft.famiglia_prodotto(p["metadata"], p.get("document", "")) not in fam_scelte]
    # niente doppioni dello stesso prodotto (stesso nome, altro codice o formato: "Bastardo Maremmano" due volte)
    visti, unici = {_nome(p).lower() for p in indice_testuale if p["id"] in gia}, []
    for p in pool:
        if _nome(p).lower() not in visti:
            visti.add(_nome(p).lower())
            unici.append(p)
    pool = unici
    nuove = [p for p in pool if ft.famiglia_prodotto(p["metadata"], p.get("document", "")) not in fam_scelte]
    scelti = ricettario._scegli_vari(nuove, n, ruolo, "", 2, False)
    if len(scelti) < n:
        resto = [p for p in pool if p not in scelti]
        scelti += ricettario._scegli_vari(resto, n - len(scelti), ruolo, "", 2, False)
    return scelti


def _nuovo(stato: dict, testo: str) -> dict:
    sal, form = conteggi_espliciti(testo)
    if sal is None and form is None:
        sal, form = _conteggi_dalla_chat(stato)
    return {"fase": "proposta_insieme", "n": {"salumi": sal, "formaggi": form}, "scelti": {"salumi": [], "formaggi": []},
            "proposti": {"salumi": [], "formaggi": []}, "lista": [], "regione": _regione_dalla_chat(stato)}


def _prossimo_ruolo(c: dict):
    for r in RUOLI:
        if (c["n"].get(r) or 0) > len(c["scelti"][r]):
            return r
    return None


def _nome(p: dict) -> str:
    from core.contesto_prodotti import pulisci_nome_commerciale
    from core.testo_prodotto import prima_riga
    return pulisci_nome_commerciale(prima_riga(p.get("document", "")), (p.get("metadata") or {}).get("nome_fornitore") or "")


def turno(stato: dict, testo: str, indice_testuale: list, ultimo_piatto: "dict | None" = None) -> "dict | None":
    per_id = {p["id"]: p for p in indice_testuale}
    c = stato.get("costruzione")
    tagliere_attivo = bool(ultimo_piatto and str(ultimo_piatto.get("categoria") or "").lower() == "tagliere")
    if not c:
        parla_di_tagliere = tagliere_attivo or "taglier" in (testo or "").lower()
        if parla_di_tagliere and (non_convince(testo) or vuole_costruire(testo)):
            c = stato["costruzione"] = _nuovo(stato, testo)
            if vuole_costruire(testo) and c["n"]["salumi"] is not None and c["n"]["formaggi"] is not None:
                return _lista(stato, c, per_id, indice_testuale, nota="")  # numeri gia' noti: si parte subito
            return _chiedi_numeri(c)
        return None

    if _RE_USCITA.search(testo or "") and not scelte(testo, c.get("lista") or [], {}):
        stato["costruzione"] = None  # il cliente e' passato ad altro
        return None

    if c["fase"] == "proposta_insieme":
        sal, form = conteggi_espliciti(testo, attesa_coppia=True)
        if sal is not None:
            c["n"]["salumi"] = sal
        if form is not None:
            c["n"]["formaggi"] = form
        if _RE_NO.search(testo or "") and sal is None and form is None:
            stato["costruzione"] = None
            return None
        if c["n"]["salumi"] is None or c["n"]["formaggi"] is None:
            return _chiedi_numeri(c)
        return _lista(stato, c, per_id, indice_testuale, nota="")

    ruolo = c["fase"]
    if ruolo in RUOLI and e_domanda(testo):
        return None  # domanda su una proposta: risponde il flusso normale (app.py usa nominati()), la costruzione resta
    if ruolo in RUOLI:
        sal, form = conteggi_espliciti(testo)
        if sal is not None:
            c["n"]["salumi"] = sal
        if form is not None:
            c["n"]["formaggi"] = form
        nomi = {i: _nome(per_id[i]) for i in c["lista"] if i in per_id}
        presi = [i for i in scelte(testo, c["lista"], nomi) if i not in c["scelti"][ruolo]]
        mancano = max((c["n"].get(ruolo) or 0) - len(c["scelti"][ruolo]), 0)
        if presi:
            tenuti = presi[:mancano]
            c["scelti"][ruolo] += tenuti
            nota = "Perfetto, segno " + _elenco([f"**{nomi[i]}**" for i in tenuti]) + "."
            if len(presi) > mancano:
                nota += f" Ne servivano {mancano}, quindi ho tenuto i primi: se preferisci cambiare dimmelo."
            return _lista(stato, c, per_id, indice_testuale, nota=nota)
        if _RE_ALTRI.search(testo or "") or non_convince(testo):
            return _lista(stato, c, per_id, indice_testuale, nota="Nessun problema, ecco altre proposte.", nuova=True)
        if sal is not None or form is not None:
            return _lista(stato, c, per_id, indice_testuale,
                          nota=f"D'accordo, facciamo {_qt(c['n']['salumi'], 'salum')} e {_qt(c['n']['formaggi'], 'formagg')}.")
        return None  # messaggio non riconosciuto: risponde il flusso normale, la costruzione resta
    return None


def _qt(n, radice: str) -> str:
    """'1 formaggio', '2 formaggi', '1 salume'."""
    return f"{n} {radice}{'e' if radice == 'salum' else 'io'}" if n == 1 else f"{n} {radice}{'i'}"


def _elenco(voci: list) -> str:
    return voci[0] if len(voci) == 1 else ", ".join(voci[:-1]) + " e " + voci[-1] if voci else ""


def _chiedi_numeri(c: dict) -> dict:
    s, f = c["n"]["salumi"], c["n"]["formaggi"]
    intro = ("Va bene, allora costruiamolo insieme, passo passo: ti propongo una rosa di salumi, tu scegli quelli che ti "
             "piacciono, e poi facciamo lo stesso con i formaggi.")
    if s is not None and f is not None:
        domanda = f"Restiamo su {_qt(s, 'salum')} e {_qt(f, 'formagg')}, come mi avevi detto?"
    elif s is not None:
        domanda = f"Mi avevi detto {_qt(s, 'salum')}: quanti formaggi vuoi?"
    elif f is not None:
        domanda = f"Mi avevi detto {_qt(f, 'formagg')}: quanti salumi vuoi?"
    else:
        domanda = "Quanti salumi e quanti formaggi vuoi nel tagliere?"
    return {"fase": "proposta_insieme", "prodotti": [], "testo_fisso": intro + "\n\n" + domanda}


_TIPOLOGIE = {"crudo": "crudo", "insaccato": "insaccato", "muscolo_stagionato": "salume di muscolo intero", "cotto": "cotto",
              "manzo": "di manzo", "grasso": "lardo/pancetta", "spalmabile": "spalmabile", "duro": "pasta dura",
              "semiduro": "pasta semidura", "erborinato": "erborinato", "molle": "pasta molle", "fresco": "fresco",
              "aromatizzato": "aromatizzato"}


def _frase(p: dict) -> str:
    """Prima frase della descrizione della scheda (il "racconto"), max ~170 caratteri."""
    from core.testo_prodotto import sezioni_scheda
    descr = next((t for s, t in sezioni_scheda(p.get("document", "")) if s == "DESCRIZIONE"), "")
    frase = re.split(r"(?<=[.!?])\s", descr, maxsplit=1)[0].strip()
    return frase if len(frase) <= 170 else frase[:frase.rfind(" ", 0, 165)].rstrip(",;:") + "..."


def _riga(k: int, p: dict) -> str:
    from core import famiglie_tagliere as ft
    from core.anagrafica_fornitori import nome_breve
    fam = ft.famiglia_prodotto(p["metadata"], p.get("document", ""))
    latte = ft.latte(p["metadata"], p.get("document", ""))
    tip = ", ".join(x for x in (_TIPOLOGIE.get(fam or "", ""), f"latte {latte}" if latte and latte != "vaccino" else "") if x)
    frase = _frase(p)
    return (f"{k}. **{_nome(p)}** di {nome_breve(p['metadata'].get('nome_fornitore'), p.get('document', ''))}"
            + (f" ({tip})" if tip else "") + (f": {frase}" if frase else ""))


def _lista(stato: dict, c: dict, per_id: dict, indice_testuale: list, nota: str, nuova: bool = False) -> dict:
    ruolo = _prossimo_ruolo(c)
    scelti_tutti = [per_id[i] for r in RUOLI for i in c["scelti"][r] if i in per_id]
    if ruolo is None:
        stato["costruzione"] = None
        righe = [f"- {_nome(p)} ({'salume' if p['metadata'].get('reparto') == 'SALUMI' else 'formaggio'})" for p in scelti_tutti]
        return {"fase": "fatto", "prodotti": scelti_tutti,
                "contesto": ("[COSTRUZIONE TAGLIERE INSIEME - completato] Il cliente ha appena finito di scegliere. "
                             "Composizione scelta (solo questi prodotti):\n" + "\n".join(righe) + "\nRiepiloga il tagliere "
                             "raccontandolo come percorso di degustazione (dal piu' delicato al piu' intenso), con il "
                             "Racconto di ogni prodotto; se ci sono abbinamenti verificati proponine uno. Chiudi chiedendo se "
                             "vuole aggiungere accompagnamenti o procedere con l'ordine.")}
    cambio_ruolo = c.get("fase") in RUOLI and c["fase"] != ruolo
    c["fase"] = ruolo
    cand = [p for p in candidati(ruolo, c, indice_testuale) if _nome(p) and not _nome(p).lower().startswith("descrizione")]
    c["lista"] = [p["id"] for p in cand]
    c["proposti"][ruolo] += c["lista"]
    mancano = (c["n"].get(ruolo) or 0) - len(c["scelti"][ruolo])
    parti = [nota] if nota else []
    if cambio_ruolo:
        prec = "salumi" if ruolo == "formaggi" else "formaggi"
        parti.append(f"I {prec} sono a posto: " + _elenco([f"**{_nome(per_id[i])}**" for i in c["scelti"][prec] if i in per_id]) + ".")
    if not cand:
        parti.append(f"Non ho altri {ruolo} a catalogo oltre a quelli che ti ho gia' mostrato. Vuoi sceglierne tra quelli "
                     f"gia' proposti o preferisci ridurre il numero?")
        return {"fase": ruolo, "prodotti": [], "testo_fisso": " ".join(parti)}
    quanti = f"te ne {'serve' if mancano == 1 else 'servono'} ancora {mancano}" if c["scelti"][ruolo] else \
        f"ne scegliamo {mancano}"
    nuove = " diversi da quelli di prima" if (nuova or c["scelti"][ruolo]) else ""
    parti.append(f"Ecco una rosa di {ruolo}{nuove} ({quanti}), di tipologie diverse:")
    testo = " ".join(parti[:-1]) + ("\n\n" if len(parti) > 1 else "") + parti[-1] + "\n\n" + \
        "\n".join(_riga(k, p) for k, p in enumerate(cand, 1)) + \
        (f"\n\nDimmi i numeri di quelli che ti piacciono (per esempio \"1 e 4\"); se vuoi saperne di piu' su uno chiedimelo, "
         f"e se nessuno ti convince te ne propongo altri.")
    return {"fase": ruolo, "prodotti": cand, "testo_fisso": testo}
