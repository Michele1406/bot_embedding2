# -*- coding: utf-8 -*-
"""
Anagrafica dei fornitori: riconosce un produttore nominato dal cliente e aggancia la sua scheda azienda.

Prima la ricerca usava una lista di alias scritta a mano (core/ricerca_base._ALIAS_FORNITORI) e la scheda azienda
si cercava per nome esatto: "masciarelli", "italfish" (108 prodotti), "la valletta", "san salvatore" non venivano
riconosciuti, e la storia di Italfish, Gentile, San Salvatore, Farino non veniva mai mostrata.

Qui gli alias si CALCOLANO dai nomi reali del catalogo e delle schede:
  "Amodio | Conserve Gentile | Forni Gentile | Pastificio Gentile" -> amodio, conserve gentile, forni gentile, ...
  "PASTIFICIO MASCIARELLI"                                          -> pastificio masciarelli, masciarelli
  "ITALFISH/BALTIK/SALUMI DI MARE"                                  -> italfish, baltik, salumi di mare (no: generico)
Una parola da sola diventa alias solo se e' distintiva (>= 5 lettere, non generica, non una parola comune
del catalogo o della lingua: "gentile", "boschi", "delfino", "esca" valgono solo dentro il nome completo).

  costruisci(nomi_catalogo, vocabolario)   indice alias -> nomi fornitore
  fornitori_in_testo(testo)               nomi fornitore (come nel catalogo) citati nel testo
  stesso_fornitore(a, b)                  True se due nomi indicano la stessa azienda (scheda <-> catalogo)
"""
import re
import unicodedata

# Parole che descrivono il tipo di azienda o sono troppo comuni per identificarla da sole
_GENERICHE = {
    "azienda", "agricola", "agricole", "pastificio", "salumificio", "caseificio", "oleificio", "birrificio", "forni",
    "forno", "conserve", "salumi", "formaggeria", "fattoria", "casa", "gruppo", "societa", "srl", "spa", "snc", "sas",
    "fratelli", "f.lli", "figli", "il", "la", "lo", "le", "i", "gli", "di", "de", "del", "della", "dei", "da", "e",
    "and", "food", "foods", "italia", "italiana", "mare", "terra", "logistica", "interna", "so", "bottega", "sapori",
    "carne", "fresco", "nero", "latte", "pesto", "valle", "san", "santa",
}
# Parole distintive SOLO dentro il nome completo ("la casera", "latte nobile", "san salvatore"): da sole sono
# parole comuni o nomi di persona e non identificano l'azienda
_SOLO_NOME_COMPLETO = {
    "mediterranei", "centro", "riserva", "nobile", "rossi", "ghianda", "nicchia", "convento", "casera", "trulli",
    "gresta", "lodigiana", "piada", "fermento", "abbondanza", "salvatore", "marino", "mulino", "giuseppe", "antonio",
    "francesco", "maria", "nino", "dante", "galli", "cecca", "stella",
    # saluti, citta', regioni e nomi propri che coincidono con un pezzo del nome dell'azienda
    "buongiorno", "toscana", "martina", "franca", "messina", "pisani", "dossi",
}
# Nomi di fornitore che sono anche parole comuni: riconosciuti solo con il nome completo
_AMBIGUI = {"gentile", "boschi", "delfino", "esca", "recco", "solera", "smeralda", "evergreen", "capriz", "zucchi",
            "branchi", "oberto", "guglielmi", "capuano"}
_AMBIGUI_OK_DA_SOLI = {"guglielmi", "capuano", "oberto", "branchi", "zucchi", "capriz", "smeralda", "solera", "evergreen"}


def norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", t)).strip()


def _parti(nome: str) -> list:
    """'Amodio | Conserve Gentile' -> ['amodio', 'conserve gentile']; 'ITALFISH/BALTIK' -> ['italfish', 'baltik']."""
    return [norm(p) for p in re.split(r"[|/;]|\(|\)", str(nome or "")) if norm(p)]


def alias_di(nome: str, vocabolario: "dict | None" = None) -> set:
    """Alias riconoscibili di un fornitore. `vocabolario` = {parola: n. di FORNITORI diversi nei cui prodotti compare} (opzionale):
    una parola usata da piu' fornitori (es. 'latte', 'pesto', 'marino') non diventa alias da sola."""
    out = set()
    for parte in _parti(nome):
        parole = parte.split()
        senza_anni = " ".join(w for w in parole if not re.fullmatch(r"\d{4}", w))
        significative = [w for w in parole if w not in _GENERICHE and not w.isdigit() and len(w) >= 4]
        if len(senza_anni) >= 4 and significative:
            out.add(senza_anni)  # nome intero ("la casera", "riserva del mare"), mai se fatto solo di parole generiche
        if significative and len(parole) > 1:
            # nome senza le parole di tipo azienda ("pastificio masciarelli" -> "masciarelli")
            cuore = " ".join(w for w in parole if w not in _GENERICHE and not re.fullmatch(r"\d{4}", w))
            if len(cuore) >= 5 and cuore not in _SOLO_NOME_COMPLETO:
                out.add(cuore)
        for w in significative:
            if len(w) < 5 or w in _SOLO_NOME_COMPLETO:
                continue
            if w in _AMBIGUI and w not in _AMBIGUI_OK_DA_SOLI:
                continue
            if vocabolario and vocabolario.get(w, 0) > 2 and w not in _AMBIGUI_OK_DA_SOLI:
                continue  # parola comune nei testi dei prodotti: non identifica un'azienda
            out.add(w)
    # alias ambigui: tenuti solo se composti ("conserve gentile") — gia' inclusi sopra come parte intera
    out = {a for a in out if a and (a not in _AMBIGUI or a in _AMBIGUI_OK_DA_SOLI)}
    if not out:
        # nessuna parola distintiva ("Forno A", "Esca"): resta riconoscibile solo con il nome intero, se non ambiguo
        intero = norm(nome)
        if len(intero) >= 4 and intero not in _AMBIGUI and intero not in _GENERICHE:
            out.add(intero)
    return out


class Anagrafica:
    def __init__(self):
        self.alias: dict = {}       # alias normalizzato -> set(nomi fornitore originali)
        self._re = None

    def costruisci(self, nomi: list, vocabolario: "dict | None" = None, alias_manuali: "dict | None" = None) -> "Anagrafica":
        self.alias = {}
        for nome in nomi or []:
            if not str(nome or "").strip():
                continue
            for a in alias_di(nome, vocabolario):
                self.alias.setdefault(a, set()).add(nome)
        # alias scritti a mano (refusi, sinonimi): alias -> frammenti del nome fornitore
        for a, targets in (alias_manuali or {}).items():
            for nome in nomi or []:
                if any(norm(t) in norm(nome) for t in targets):
                    self.alias.setdefault(norm(a), set()).add(nome)
        voci = sorted(self.alias, key=len, reverse=True)
        self._re = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(v) for v in voci) + r")(?![a-z0-9])") if voci else None
        return self

    def fornitori_in_testo(self, testo: str) -> list:
        """Nomi dei fornitori (come nel catalogo) citati nel testo, nell'ordine in cui compaiono."""
        if not self._re:
            return []
        out = []
        for m in self._re.finditer(norm(testo)):
            for nome in sorted(self.alias.get(m.group(1), ())):
                if nome not in out:
                    out.append(nome)
        return out

    def alias_trovati(self, testo: str) -> list:
        return [m.group(1) for m in self._re.finditer(norm(testo))] if self._re else []


def nome_breve(nome_fornitore: str, documento: str = "") -> str:
    """Nome da mostrare al cliente: per i gruppi ("Amodio | Conserve Gentile | Forni Gentile | Pastificio Gentile",
    "San Salvatore | Azienda Agricola San Salvatore 1988") la parte citata nella scheda del prodotto, altrimenti la
    prima. Per i nomi semplici, il nome cosi' com'e'."""
    parti = [p.strip() for p in re.split(r"[|]", str(nome_fornitore or "")) if p.strip()]
    if len(parti) <= 1:
        return _leggibile(str(nome_fornitore or "").strip())
    doc = norm(documento)
    for p in sorted(parti, key=len, reverse=True):
        if norm(p) and norm(p) in doc:
            return _leggibile(p)
    return _leggibile(parti[0])


def _leggibile(nome: str) -> str:
    """"FRANCHI SALUMI" -> "Franchi Salumi" (in chat il maiuscolo sembra urlato); le sigle corte restano ("BBS")."""
    return nome.title().replace("'S ", "'s ") if nome.isupper() and len(nome) > 4 else nome


def stesso_fornitore(a: str, b: str) -> bool:
    """True se due nomi indicano la stessa azienda ('ITALFISH/BALTIK/SALUMI DI MARE' ~ 'Italfish',
    'Forni Farino' ~ 'Farino', 'Amodio | Conserve Gentile | ...' ~ 'CONSERVE GENTILE')."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    aa, ab = alias_di(a) | set(_parti(a)), alias_di(b) | set(_parti(b))
    return bool(aa & ab)


ANAGRAFICA = Anagrafica()
