# -*- coding: utf-8 -*-
"""
Informazioni aziendali ufficiali di So Food (consegne, ordine minimo, pagamenti, sede, calendario dei freschi).

Fonte: sofood/consegne.xlsx e sofood/calendario_freschi.xlsx (PRODOTTI SOFOOD, core/percorsi.py). Prima di questo modulo le
domande "quando consegnate a Lecce?", "qual e' l'ordine minimo?", "come si paga?" non avevano dati nel contesto:
il modello poteva solo dire "non lo so" o inventare.

  domanda_aziendale(testo) -> True se la domanda riguarda consegne/ordini/pagamenti/sede/contatti
  blocco_contesto(testo, citta) -> "[INFO SO FOOD - dati ufficiali]..." (vuoto se la domanda non c'entra)
I contatti (telefono/email) NON sono nel file: si leggono da regole_cliente.yaml (azienda.contatti) solo se compilati.
"""
import os
import re
import unicodedata

from core import percorsi

_RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE: dict = {}

_PAROLE = ("consegn", "spedi", "spedizion", "ordine minimo", "minimo d'ordine", "minimo ordine", "minimo per",
           "pagament", "pagare", "si paga", "paghiamo", "pago ", "contrassegno", " pos ", "bonifico", "contanti", "tempi di consegna", "in quanto tempo",
           "quando arriva", "quanto ci mette",
           "arriva la merce", "orari", "contatt", "telefono", "email", "mail", "sede", "deposito", "magazzino",
           "costo di spedizione", "costi di spedizione", "trasporto", "zona", "zone", "corriere", "fattura",
           "entro quando", "giorno di consegna", "giorni di consegna", "freschi arrivano", "calendario")


def _norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def domanda_aziendale(testo: str) -> bool:
    t = " " + _norm(testo) + " "
    return any(p in t for p in _PAROLE)


def _zone() -> list:
    if "zone" in _CACHE:
        return _CACHE["zone"]
    righe = []
    try:
        import openpyxl
        ws = openpyxl.load_workbook(os.getenv("CONSEGNE_XLSX", percorsi.dati("consegne.xlsx")),
                                    read_only=True).active
        intest = None
        for r in ws.iter_rows(values_only=True):
            if r and r[0] == "ZONA (ID)":
                intest = [str(x or "").strip() for x in r]
                continue
            if intest and r and isinstance(r[0], (int, float)):
                righe.append({intest[i]: (r[i] if i < len(r) else None) for i in range(len(intest))})
    except Exception as e:
        print(f"[INFO AZIENDA] consegne non disponibili: {e}")
    _CACHE["zone"] = righe
    return righe


def _zone_del_cliente(citta: "str | None") -> list:
    """Righe di consegne.xlsx che riguardano la citta'/provincia del cliente (via core/zone_consegna.yaml)."""
    if not citta:
        return []
    from core import logistica
    c = _norm(citta)
    province = []
    for prov, comuni in (logistica._CFG.get("province_o_zone") or {}).items():
        if logistica._contiene(c, prov) or any(logistica._contiene(c, x) for x in comuni):
            province.append(prov)
    out = []
    for z in _zone():
        nome = _norm(z.get("NOME"))
        if any(p in nome for p in province) or (nome and nome.split()[0] in c):
            out.append(z)
    return out


def _euro(v) -> str:
    if v is None or str(v).strip() == "":
        return "n/d"
    s = str(v).strip()
    return s if not re.fullmatch(r"\d+(\.\d+)?", s) else f"{float(s):g} euro"


def blocco_contesto(testo: str, citta: "str | None" = None) -> str:
    if not domanda_aziendale(testo):
        return ""
    zone = _zone()
    if not zone:
        return ""
    righe = ["[INFO SO FOOD - dati ufficiali (sofood/consegne.xlsx): usa SOLO questi dati per consegne, ordine minimo, "
             "costi, tempi e pagamenti; per tutto il resto rimanda al commerciale]"]
    z0 = zone[0]
    righe.append(f"- Sede legale: {z0.get('SEDE LEGALE')}. Deposito: {z0.get('DEPOSITO')}.")
    righe.append(f"- Pagamento: {z0.get('METODI DI PAGAMENTO')}.")
    righe.append(f"- Condizioni di consegna: {z0.get('CONDIZIONI DI CONSEGNA')}")
    del_cliente = _zone_del_cliente(citta)
    elenco = del_cliente or zone
    if del_cliente:
        righe.append(f"- Zona del cliente ({citta}):")
    else:
        righe.append("- Zone servite con i mezzi So Food (Puglia e Basilicata):")
    for z in elenco:
        righe.append(f"  * {z.get('NOME')}: consegna gratuita da {_euro(z.get('MINIMO ORDINE PER CONSEGNA GRATUITA'))}, "
                     f"altrimenti spedizione {_euro(z.get('COSTI SPEDIZIONE'))}; tempi medi {z.get('TEMPI MEDI')}")
    righe.append("- Fuori da Puglia e Basilicata si spediscono solo prodotti a temperatura ambiente (costi e tempi da "
                 "concordare con il commerciale).")
    from core import logistica
    cal = logistica.calendario_ordini()
    if cal and re.search(r"fresc|calendario|entro quando|giorn", _norm(testo)):
        righe.append("- Calendario ordini dei freschi (fornitore: ordine entro -> arrivo): "
                     + "; ".join(f"{k.title()}: {o} -> {a}" for k, (o, a) in cal.items() if k))
    contatti = _contatti()
    righe.append(f"- Contatti: {contatti}" if contatti else
                 "- Contatti diretti: non disponibili nei dati; il commerciale ricontatta il cliente dopo l'ordine.")
    return "\n".join(righe)


def _contatti() -> str:
    try:
        from core.config_manager import get_azienda_info
        c = get_azienda_info().get("contatti") or {}
        parti = [f"{k} {v}" for k, v in c.items() if v and str(v).strip()]
        return ", ".join(parti)
    except Exception:
        return ""
