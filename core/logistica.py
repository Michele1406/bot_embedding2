# -*- coding: utf-8 -*-
"""
Logistica deterministica di So Food.

Regola (sofood/consegne.xlsx, prompt di sistema): i mezzi refrigerati coprono solo Puglia e Basilicata; nel resto
d'Italia si spedisce SOLO merce a temperatura ambiente. Prima la regola era solo testo nel prompt: ora i
prodotti refrigerati/surgelati vengono tolti dal retrieval quando il cliente e' fuori zona.

  classe_temperatura(meta, doc)  -> "ambiente" | "refrigerato" | "surgelato"
  zona_cliente(citta)            -> "coperta" | "fuori" | "sconosciuta"
  ZONA_CORRENTE                  ContextVar per turno (come la dieta); "fuori" attiva il filtro
  non_consegnabile(meta, doc)    -> True se il prodotto non si puo' spedire alla zona corrente
  riga_calendario(fornitore)     giorni limite d'ordine e arrivo dei freschi (sofood/calendario_freschi.xlsx)
"""
import contextvars
import os
import re
import unicodedata

import yaml

from core import percorsi

_DIR = os.path.dirname(os.path.abspath(__file__))
_RADICE = os.path.dirname(_DIR)

ZONA_CORRENTE: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("zona_corrente", default=None)

# --------------------------------------------------------------------------- temperatura
_REPARTI_REFRIGERATI = {"FORMAGGI", "SALUMI", "MARE", "CARNE"}
_RE_FRIGO = re.compile(r"(conservare|conservazione|mantenere|tenere)[^.\n]{0,60}(\+\s?[0-9]\s?°|frigo|refrigerat)", re.I)
_RE_DOPO_APERTURA = re.compile(r"(dopo l['’]?apertura|una volta aperto|dopo l['’]?uso|a confezione aperta|aperta la confezione)", re.I)
_RE_AMBIENTE_ESPLICITO = re.compile(r"(luogo fresco e asciutto|temperatura ambiente|al riparo da fonti di calore)", re.I)


_RE_CONSERVA = re.compile(r"sott['’]?olio|sott['’]?olii|sott['’]?aceto|sotto sale|sott['’]?sale|in scatola|in vasetto|conserva|bottarga|"
                          r"colatura|essiccat|stagionat|secco|secca|affumicat[oa]? a freddo vasetto", re.I)


_RE_FORMAGGIO_DA_TAVOLA = re.compile(r"grana|parmigiano|pecorino|stagionat|provolone|caciocavallo|asiago|montasio|fontina|emmental|gruy|"
                                     r"cheddar|manchego|toma|castelmagno|bitto|taleggio stagion", re.I)
_RE_TEMP = re.compile(r"([+-]?\s?\d{1,2})\s?°")


def _temp_massima(cons: str):
    """Temperatura massima di conservazione prescritta nella scheda (es. 'tra 0°C e +10°C' -> 10), None se assente."""
    v = []
    for m in _RE_TEMP.finditer(cons or ""):
        try:
            v.append(int(m.group(1).replace(" ", "")))
        except ValueError:
            pass
    return max(v) if v else None


def _sezione_conservazione(doc: str) -> str:
    m = re.search(r"CONSERVAZIONE:?\s*(.+?)(?:\n\s*\n|\nPRODUTTORE|$)", doc or "", re.S)
    return m.group(1) if m else ""


def classe_temperatura(meta: dict, doc: str = "") -> str:
    rep = str(meta.get("reparto") or "").upper()
    if rep == "GELO":
        return "surgelato"
    cons = _sezione_conservazione(doc)
    if re.search(r"(-\s?18\s?°|surgelat)", cons, re.I):
        return "surgelato"
    chiusa = " ".join(f for f in re.split(r"(?<=[.;])\s+", cons) if not _RE_DOPO_APERTURA.search(f))  # solo confezione chiusa
    temp = _temp_massima(chiusa)
    if temp is not None and rep != "GELO":
        return "refrigerato" if temp <= 12 else "ambiente"   # "tra 0 e +10 C", "+4 C": catena del freddo; "max +15 C": ambiente fresco
    if rep in _REPARTI_REFRIGERATI:
        # la scheda che dice espressamente "temperatura ambiente / luogo fresco e asciutto" prevale (salumi stagionati, conserve)
        formaggio_molle = rep == "FORMAGGI" and not _RE_FORMAGGIO_DA_TAVOLA.search(str(meta.get("tipo_prodotto") or "") + " " + (doc or "").split("\n", 1)[0])
        if cons and _RE_AMBIENTE_ESPLICITO.search(cons) and not _RE_FRIGO.search(cons) and not formaggio_molle:
            return "ambiente"
        if _RE_CONSERVA.search(str(meta.get("tipo_prodotto") or "") + " " + (doc or "").split("\n", 1)[0]) and not _RE_FRIGO.search(cons):
            return "ambiente"  # conserve, bottarga, colatura...: senza prescrizione del freddo in scheda
        return "refrigerato"
    # DISPENSA e altro: frigo solo se la scheda lo prescrive per la confezione chiusa
    for frase in re.split(r"(?<=[.;])\s+", cons):
        if _RE_FRIGO.search(frase) and not _RE_DOPO_APERTURA.search(frase):
            return "refrigerato"
    return "ambiente"


# --------------------------------------------------------------------------- zona
def _norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9' ]+", " ", t).strip()


def _carica():
    try:
        with open(os.path.join(_DIR, "zone_consegna.yaml"), encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}


_CFG = _carica()


def _contiene(testo: str, parola: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(_norm(parola)) + r"(?![a-z0-9])", testo) is not None


def zona_cliente(citta: "str | None") -> str:
    """'coperta' se la citta'/provincia/regione e' servita dai mezzi refrigerati, 'fuori' se e' chiaramente altrove,
    'sconosciuta' se non si capisce (non si filtra: meglio verificare al momento dell'ordine)."""
    t = _norm(citta)
    if not t:
        return "sconosciuta"
    for r in _CFG.get("regioni_coperte", []):
        if _contiene(t, r):
            return "coperta"
    for prov, comuni in (_CFG.get("province_o_zone") or {}).items():
        if _contiene(t, prov) or any(_contiene(t, c) for c in comuni):
            return "coperta"
    if any(_contiene(t, x) for x in _CFG.get("fuori_zona_esplicito", [])):
        return "fuori"
    return "sconosciuta"


_GEO: "set | None" = None


def parole_geografiche() -> set:
    """Parole (minuscole, senza accenti) di citta', province e regioni note: non sono prodotti da cercare a catalogo."""
    global _GEO
    if _GEO is None:
        voci = list(_CFG.get("regioni_coperte", [])) + list(_CFG.get("fuori_zona_esplicito", []))
        for prov, comuni in (_CFG.get("province_o_zone") or {}).items():
            voci += [prov] + list(comuni)
        _GEO = {w for v in voci for w in _norm(v).split()}
    return _GEO


def non_consegnabile(meta: dict, doc: str = "") -> bool:
    """True se il cliente e' fuori zona refrigerata e il prodotto richiede il freddo."""
    if ZONA_CORRENTE.get() != "fuori":
        return False
    return (meta.get("temperatura") or classe_temperatura(meta, doc)) != "ambiente"


# --------------------------------------------------------------------------- calendario freschi
_CAL: "dict | None" = None


def calendario_ordini() -> dict:
    """{nome azienda normalizzato: (giorno limite ordine, giorno arrivo)} da sofood/calendario_freschi.xlsx."""
    global _CAL
    if _CAL is not None:
        return _CAL
    _CAL = {}
    try:
        import openpyxl
        percorso = os.getenv("CALENDARIO_XLSX", percorsi.dati("calendario_freschi.xlsx"))
        ws = openpyxl.load_workbook(percorso, read_only=True).active
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            if i and r and r[0]:
                _CAL[_norm(r[0])] = (str(r[1] or "").strip(), str(r[2] or "").strip())
    except Exception as e:
        print(f"[LOGISTICA] calendario freschi non disponibile: {e}")
    return _CAL


def riga_calendario(nome_fornitore: str) -> str:
    """Riga di contesto con i giorni d'ordine/arrivo del fornitore se e' nel calendario freschi."""
    n = _norm(nome_fornitore)
    if not n:
        return ""
    for k, (ordine, arrivo) in calendario_ordini().items():
        if k and (k == n or k in n or n in k):
            return f"Ordine fresco: entro {ordine}, arrivo {arrivo}"
    return ""


def righe_logistica(meta: dict, doc: str = "") -> list:
    """Righe di contesto: temperatura (se non ambiente) e calendario ordini del fornitore (se refrigerato)."""
    cl = meta.get("temperatura") or classe_temperatura(meta, doc)
    if cl == "ambiente":
        return []
    righe = [f"Temperatura: {cl.upper()} (consegna solo in zona coperta)"]
    cal = riga_calendario(str(meta.get("nome_fornitore") or ""))
    if cal:
        righe.append(cal)
    return righe
