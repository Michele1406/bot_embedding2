# -*- coding: utf-8 -*-
"""
Territorio: regione dei produttori e regione chiesta dal cliente (REPORT_COLLEGAMENTI.md punto A4).

Prima la regione esisteva solo per i ~44 produttori di fornitori_config.py, e solo per alcuni ruoli (salumi,
formaggi...): "prodotti lucani" o "solo pugliesi" funzionavano a meta'. Qui la regione e' un DATO per produttore,
in sofood/regioni_produttori.csv (bozza generata da scripts/bozza_regioni_produttori.py, da verificare a mano).

  REGIONI                        chiave -> parole che la riconoscono (nel testo del cliente e nelle schede)
  regione_richiesta(testo)       -> chiave regione o None ("salumi lucani" -> "basilicata")
  regione_produttore(nome)       -> chiave regione o None (dal CSV; vale solo se confermata o con confidenza alta)
  nome_regione(chiave)           -> "Basilicata"
Le chiavi coincidono con quelle di fornitori_config.TEMI_REGIONALI dove esistono ("emilia", "trentino").
"""
import csv
import os
import re

from core import percorsi

_RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSV = percorsi.dati("regioni_produttori.csv")

# parole che riconoscono la regione; le radici corte vanno a parola intera. Niente aggettivi di "stile" che
# non dicono l'origine: alla genovese, alla bolognese, pinsa romana, alla napoletana, alla fiorentina.
REGIONI = {
    "puglia": ["puglia", "puglies", "salento", "salentin", "barese", "murge", "murgia", "foggia", "lecce", "taranto",
               "brindisi", "cerignola", "altamura", "martina franca", "bitetto", "gravina", "andria", "molfetta"],
    "basilicata": ["basilicata", "lucan", "potenza", "matera", "senise", "pollino"],
    "campania": ["campania", "campan", "napoli", "sorrento", "amalfi", "salerno", "caserta", "vesuvi",
                 "gragnano", "cilento", "avellino", "agerola", "montoro", "cetara", "pagani", "san marzano"],
    "calabria": ["calabria", "calabres", "tropea", "cosenza", "crotone", "sila", "spilinga"],
    "sicilia": ["sicilia", "sicilian", "bronte", "palermo", "catania", "trapani", "messina", "pachino", "ragusa",
                "siracusa", "giarratana", "etna"],
    "sardegna": ["sardegna", "sardo", "sarda", "sardi", "nuoro", "cagliari", "sassari", "oristano", "ogliastra"],
    "lazio": ["lazio", "laziale", "roma", "viterbo", "ariccia", "latina"],
    "abruzzo": ["abruzz", "teramo", "pescara", "chieti", "l'aquila"],
    "molise": ["molise", "molisan", "campobasso", "isernia"],
    "toscana": ["toscan", "firenze", "siena", "senese", "maremma", "livorno", "lucca", "colonnata",
                "chianti", "pisa", "arezzo", "grosseto", "prato"],
    "umbria": ["umbria", "umbro", "umbra", "norcia", "perugia", "castelluccio", "colfiorito", "spoleto"],
    "marche": ["marche", "marchigian", "ascoli", "ancona", "macerata", "pesaro", "fermo"],
    "emilia": ["emilia", "romagna", "romagnol", "emilian", "parma", "parmense", "modena", "modenese", "reggio emilia",
               "bologna", "piacenza", "ferrara", "zibello"],
    "lombardia": ["lombardia", "lombard", "milano", "lodi", "lodigian", "cremona", "mantova", "bergamo",
                  "brescia", "valtellin", "pavia"],
    "piemonte": ["piemont", "torino", "langhe", "cuneo", "asti", "novara", "biella", "roero", "monferrato", "vercell",
                 "valsesia", "ossola"],
    "liguria": ["liguria", "ligure", "genova", "taggia", "imperia", "savona"],
    "veneto": ["veneto", "veneta", "verona", "vicenza", "treviso", "padova", "asiago", "venezia"],
    "friuli": ["friuli", "friulan", "udine", "san daniele", "trieste", "pordenone", "carnia"],
    "trentino": ["trentino", "trentin", "alto adige", "altoatesin", "pusteria", "sudtirol", "südtirol", "bolzano", "trento",
                 "tirolese"],
    "valle d'aosta": ["valle d'aosta", "aosta", "valdostan"],
    "spagna": ["spagna", "spagnol", "cantabric", "iberic", "andalus"],
}
_NOMI = {"emilia": "Emilia-Romagna", "trentino": "Trentino-Alto Adige", "friuli": "Friuli-Venezia Giulia",
         "valle d'aosta": "Valle d'Aosta"}


_RE_SOLO_REGIONE = re.compile(r"\b(sol[oaie]|soltanto|esclusivament\w*|unicament\w*|rigorosament\w*)\b", re.IGNORECASE)


def solo_regione(testo: str) -> bool:
    """Il cliente vuole SOLO prodotti della regione ("tagliere di soli salumi pugliesi")."""
    return bool(_RE_SOLO_REGIONE.search(testo or ""))


def ordina_per_regione(record: list, regione: "str | None", testo: str = "") -> list:
    """Prima i prodotti di produttori della regione chiesta; con "solo/soli" restano solo quelli (se ce ne sono)."""
    if not regione:
        return record
    della = [r for r in record if di_regione(r, regione)]
    if della and solo_regione(testo):
        return della
    return della + [r for r in record if r not in della]


def di_regione(r: dict, regione: str) -> bool:
    """Il prodotto viene dalla regione/nazione: produttore di li', o (per l'estero) prodotto li' ("acciughe del
    Cantabrico" di un produttore piemontese, fatte in Spagna)."""
    if regione_produttore((r.get("metadata") or {}).get("nome_fornitore") or "") == regione:
        return True
    return regione in NAZIONI_ESTERE and nazione_prodotto(r.get("document", "")) == regione


def nome_regione(chiave: "str | None") -> str:
    return _NOMI.get(chiave or "", (chiave or "").capitalize())


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "").lower().replace("’", "'"))


# parole che valgono solo intere (come radici catturerebbero altro: "fermo" -> "fermentato", "roma" -> "romanesco")
_INTERE = {"roma", "sila", "lodi", "asti", "pisa", "etna", "bari", "fermo", "sardo", "sarda", "sardi", "umbro", "umbra",
           "lecce", "prato", "parma", "marche", "veneto", "veneta", "sicilia", "puglia", "lazio", "molise", "latina",
           "aosta", "carnia", "fiorentin"}


def conteggi(testo: str) -> dict:
    """{regione: quante volte e' nominata} nel testo (radici a inizio parola; le parole di _INTERE solo intere)."""
    t = _norm(testo)
    out = {}
    for reg, parole in REGIONI.items():
        n = sum(len(re.findall(r"(?<![a-zà-ù])" + re.escape(p) + (r"(?![a-zà-ù])" if p in _INTERE else ""), t))
                for p in parole)
        if n:
            out[reg] = n
    return out


# Nel messaggio del cliente contano solo NOMI di regione e AGGETTIVI ("pugliese", "salumi lucani", "di Sicilia"):
# le citta' sono quasi sempre dove sta il locale ("ho un ristorante a Bari"), non una richiesta di prodotti locali
# (e2e reale: "ristorante a Bari, fammi un tagliere" diventava un tagliere pugliese)
_PAROLE_CLIENTE = {
    "puglia": ["puglia", "puglies", "salent"], "basilicata": ["basilicata", "lucan"], "campania": ["campania", "campan"],
    "calabria": ["calabria", "calabres"], "sicilia": ["sicilia", "sicilian"], "sardegna": ["sardegna", "sardo", "sarda", "sardi"],
    "lazio": ["lazio", "laziale", "laziali"], "abruzzo": ["abruzz"], "molise": ["molise", "molisan"], "toscana": ["toscan"],
    "umbria": ["umbria", "umbro", "umbra", "umbri"], "marche": ["marche", "marchigian"],
    "emilia": ["emilia", "romagna", "romagnol", "emilian"], "lombardia": ["lombardia", "lombard"],
    "piemonte": ["piemont"], "liguria": ["liguria", "ligure", "liguri"], "veneto": ["veneto", "veneta", "veneti"],
    "friuli": ["friuli", "friulan"], "trentino": ["trentino", "trentin", "alto adige", "altoatesin", "sudtirol", "tirolese"],
    "valle d'aosta": ["valle d'aosta", "valdostan"], "spagna": ["spagna", "spagnol", "iberic"],
}


def regione_richiesta(testo: str) -> "str | None":
    """Regione o nazione nominata dal cliente ("salumi lucani", "qualcosa di campano", "iberici")."""
    t = _norm(testo)
    c = {}
    for reg, parole in _PAROLE_CLIENTE.items():
        n = sum(len(re.findall(r"(?<![a-zà-ù])" + re.escape(p) + (r"(?![a-zà-ù])" if p in _INTERE else ""), t))
                for p in parole)
        if n:
            c[reg] = n
    return max(c, key=c.get) if c else None


_CACHE: dict = {}


def _carica() -> dict:
    if "dati" not in _CACHE:
        dati = {}
        percorso = os.getenv("REGIONI_PRODUTTORI_CSV", _CSV)
        if os.path.exists(percorso):
            with open(percorso, encoding="utf-8-sig", newline="") as f:
                for riga in csv.DictReader(f):
                    reg = (riga.get("regione") or "").strip().lower()
                    ok = (riga.get("verificata") or "").strip().lower() in ("si", "sì", "x", "1", "true") \
                        or (riga.get("confidenza") or "").strip().lower() == "alta"
                    if reg and ok:
                        dati[_norm(riga.get("nome_fornitore"))] = reg
        _CACHE["dati"] = dati
    return _CACHE["dati"]


def regione_produttore(nome_fornitore: str) -> "str | None":
    return _carica().get(_norm(nome_fornitore))


NAZIONI_ESTERE = {"spagna": ["españa", "espana", "spain", "spagna"], "francia": ["france", "francia"],
                  "grecia": ["greece", "grecia"], "norvegia": ["norway", "norvegia"]}


def nazione_prodotto(documento: str) -> "str | None":
    """Paese estero in cui il prodotto e' fatto, dalla riga PRODUTTORE della scheda ("Prodotto in Spagna per Delfino",
    "...Castro Urdiales (Cantabria) ESPAÑA"): vale anche quando il produttore e' italiano. "Viale Spagna" non conta."""
    d = documento or ""
    i = d.find("PRODUTTORE:")
    t = d[i:i + 300].split("[DISCLAIMER")[0].lower() if i >= 0 else ""
    for n, parole in NAZIONI_ESTERE.items():
        if any(re.search(r"(?<!viale )(?<!via )(?<!corso )(?<!piazza )(?<![a-zà-ù])" + re.escape(p) + r"(?![a-zà-ù])", t)
               for p in parole):
            return n
    return None


def riga_origine(meta: dict, documento: str = "") -> str:
    """Riga per il contesto del modello: dove e' fatto il prodotto (estero) o di che regione e' il produttore."""
    estero = nazione_prodotto(documento)
    reg = regione_produttore((meta or {}).get("nome_fornitore") or "")
    if estero:
        return f"Origine: prodotto in {estero.capitalize()}"
    if reg in NAZIONI_ESTERE:
        return f"Origine: produttore di {reg.capitalize()}"
    if reg:
        return f"Regione del produttore: {nome_regione(reg)} (Italia)"
    return ""


def produttori_della_regione(regione: str) -> list:
    return [n for n, r in _carica().items() if r == regione]
