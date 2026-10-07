# -*- coding: utf-8 -*-
"""
Panoramica: "che prodotti avete di Mongetto?", "che pasta avete?", "che salumi avete?".

Debug 2026-10-06: per queste domande la ricerca passava al modello 11-33 schede scelte dal vettoriale ("che pasta avete"
-> pasta mista e maltagliati) e il modello ne citava 2-5 a caso, senza sapere quante referenze ci sono davvero. Qui il
quadro si costruisce dal CATALOGO intero: quante referenze, divise per tipo (o per produttore), con alcuni nomi
d'esempio senza doppioni di formato. Nessuna chiamata API.

  e_panoramica(testo, tipo_richiesta)   -> True per domande d'insieme
  blocco(testo, record, indice_testuale) -> "[PANORAMICA ...]" o ""
"""
import re
from collections import defaultdict

_RE_PANORAMICA = re.compile(r"\b(che|quali|cosa|quanti|quante)\b.{0,30}\b(avete|hai|tenete|fate|trattate|vendete|"
                            r"proponete)\b|\b(catalogo|referenze|assortimento|gamma|linea|tutti i|tutte le|elenco|lista)\b|"
                            r"^\W*(avete|hai|tenete|vendete|trattate)\b",  # "avete olio extravergine?"
                            re.IGNORECASE)
MAX_ESEMPI = 6
MAX_GRUPPI = 8


def e_panoramica(testo: str, tipo_richiesta: str = "") -> bool:
    return tipo_richiesta == "panoramica_catalogo" or bool(_RE_PANORAMICA.search(testo or ""))


def _nome(p: dict) -> str:
    from core.contesto_prodotti import pulisci_nome_commerciale
    from core.testo_prodotto import prima_riga
    return pulisci_nome_commerciale(prima_riga(p.get("document", "")), (p.get("metadata") or {}).get("nome_fornitore") or "")


def _gruppi(prodotti: list, chiave) -> list:
    """[(gruppo, n_referenze, [nomi distinti])], gruppi piu' numerosi prima; i formati dello stesso prodotto contano una volta."""
    from core.second_brain import chiave_formato
    g = defaultdict(dict)
    for p in prodotti:
        g[chiave(p)].setdefault(chiave_formato(p), _nome(p))
    return sorted(((k, len(v), list(v.values())) for k, v in g.items() if k), key=lambda x: -x[1])


def rappresentanti(testo: str, record: list, indice_testuale: list, n: int = 6) -> list:
    """Prodotti da raccontare in una panoramica: uno per gruppo (tipo o produttore), poi un secondo, fino a n; cosi'
    le schede complete coprono la varieta' invece dei primi risultati della ricerca ("che pasta avete" -> pasta mista)."""
    gruppi = _gruppi_prodotti(testo, record, indice_testuale)
    if not gruppi:
        return []
    out = []
    for giro in range(3):
        for _g, prodotti in gruppi:
            if len(prodotti) > giro and len(out) < n:
                out.append(prodotti[giro])
    return out


def _gruppi_prodotti(testo: str, record: list, indice_testuale: list) -> list:
    """[(gruppo, [prodotti distinti, senza doppioni di formato])] come nel blocco."""
    from core.second_brain import chiave_formato
    sel = _selezione(testo, record, indice_testuale)
    if not sel:
        return []
    prodotti, chiave = sel[1], sel[2]
    g = defaultdict(dict)
    for p in prodotti:
        g[chiave(p)].setdefault(chiave_formato(p), p)
    # nel gruppo prima i prodotti "base" (un olio EVO prima degli aromatizzati, gli spaghetti prima di quelli al tartufo)
    speciali = ("aromatizzat", "condimento", "spray", "tartuf", "limone", "piccant", "integral", "farro", "souvenir", "espositor")
    parole_q = {w[:5] for w in re.findall(r'[a-zàèéìòù]{4,}', (testo or '').lower())}
    base = lambda p: (not parole_q & {w[:5] for w in re.findall(r'[a-zàèéìòù]{4,}', _nome(p).lower())},  # nome con le parole chieste
                      sum(1 for s in speciali if s in _nome(p).lower()))
    return sorted(((k, sorted(v.values(), key=base)) for k, v in g.items() if k), key=lambda x: -len(x[1]))


def _selezione(testo: str, record: list, indice_testuale: list):
    """(titolo, prodotti del catalogo, chiave di raggruppamento) o None."""
    from core.anagrafica_fornitori import ANAGRAFICA, nome_breve, stesso_fornitore
    from core import ontologia
    from core.vincoli_dieta import _DIETA_CORRENTE, prodotto_compatibile_con_dieta
    dieta = _DIETA_CORRENTE.get()

    def ammesso(p):
        return (not dieta or prodotto_compatibile_con_dieta(p["metadata"], dieta)) and \
            not ontologia.prodotto_escluso_da_cliente(p["metadata"], p.get("document", ""))

    produttori = ANAGRAFICA.fornitori_in_testo(testo)
    if produttori:
        prodotti = [p for p in indice_testuale if ammesso(p)
                    and any(stesso_fornitore(p["metadata"].get("nome_fornitore") or "", f) for f in produttori)]
        if not prodotti:
            return None
        return (nome_breve(prodotti[0]["metadata"].get("nome_fornitore"), prodotti[0].get("document", "")), prodotti,
                lambda p: str(p["metadata"].get("sottocategoria") or "").capitalize())
    subs = defaultdict(int)
    for r in record[:8]:
        subs[str(r["metadata"].get("sottocategoria") or "")] += 1
    principali = {s for s, n in subs.items() if s and n >= 2}
    # sottocategorie NOMINATE dal cliente ("avete olio extravergine?" -> OLIO EXTRAVERGINE DI OLIVA, non i carciofi
    # in olio che la ricerca trovava per parole simili): la prima parola deve combaciare, vince chi ne ha di piu'
    parole = {w[:5] for w in re.findall(r"[a-zàèéìòù]{4,}", (testo or "").lower())}
    punti = {}
    for s in {str(p["metadata"].get("sottocategoria") or "") for p in indice_testuale}:
        ws = [w[:5] for w in re.findall(r"[a-zàèéìòù]{4,}", s.lower())]
        if ws and ws[0] in parole:
            punti[s] = sum(1 for w in ws if w in parole)
    if punti:
        massimo = max(punti.values())
        nominate = {s for s, v in punti.items() if v == massimo}
        principali = nominate | (principali if massimo == 1 else set())
    if not principali:
        return None
    prodotti = [p for p in indice_testuale if str(p["metadata"].get("sottocategoria") or "") in principali and ammesso(p)]
    if len(prodotti) <= len(record[:8]):
        return None  # pochi prodotti: le schede bastano
    return (", ".join(sorted(s.capitalize() for s in principali)), prodotti,
            lambda p: nome_breve(p["metadata"].get("nome_fornitore"), p.get("document", "")))


def blocco(testo: str, record: list, indice_testuale: list) -> str:
    sel = _selezione(testo, record, indice_testuale)
    if not sel:
        return ""
    titolo, prodotti, chiave = sel
    gruppi = _gruppi(prodotti, chiave)
    distinti = sum(n for _g, n, _e in gruppi)
    righe = [f"- {g}: {n} {'referenza' if n == 1 else 'referenze'} (es. {', '.join(e[:MAX_ESEMPI])}"
             + (", ..." if n > MAX_ESEMPI else "") + ")" for g, n, e in gruppi[:MAX_GRUPPI]]
    if len(gruppi) > MAX_GRUPPI:
        righe.append(f"- altri {len(gruppi) - MAX_GRUPPI} gruppi minori")
    return (f"[PANORAMICA DAL CATALOGO: {titolo} - {distinti} prodotti diversi ({len(prodotti)} codici contando i formati). "
            "Usala per dare il quadro (quanti e di che tipo); racconta 4-6 prodotti di gruppi diversi usando le schede "
            "qui sotto e offri di approfondire un gruppo. I nomi d'esempio senza scheda citali solo come elenco.]\n"
            + "\n".join(righe))
