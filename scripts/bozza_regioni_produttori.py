# -*- coding: utf-8 -*-
"""
Regione e nazione di ogni produttore -> sofood/regioni_produttori.csv (REPORT_COLLEGAMENTI.md punto A4).

Fonti, in ordine di affidabilita':
  1) INDIRIZZO del produttore nelle schede prodotto (riga PRODUTTORE: sigla di provincia "(NA)", citta', nazione):
     e' un dato, non una deduzione -> verificata = si;
  2) schede ABSTRACT (Identita', Storia e Radici): regioni nominate;
  3) fornitori_config.py (regione assegnata a mano: si usa solo se 1 e 2 mancano, perche' in alcuni casi era
     sbagliata: Recco e' di Gaeta, Lazio, non pugliese).
Nessuna chiamata API. Le righe con verificata = si scritte a mano non vengono toccate (colonna "nota" = "manuale").

    .venv\\Scripts\\python.exe scripts\\bozza_regioni_produttori.py
"""
import csv
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb  # noqa: E402
import openpyxl  # noqa: E402

from core import percorsi, territorio  # noqa: E402
from core.anagrafica_fornitori import stesso_fornitore  # noqa: E402
from core.fornitori_config import FORNITORI  # noqa: E402

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USCITA = percorsi.dati("regioni_produttori.csv")
CAMPI = ["nome_fornitore", "regione", "nazione", "confidenza", "fonte", "indizi", "verificata", "nota"]

# sigla provincia -> regione (chiavi di core/territorio.py)
PROVINCE = {
    "puglia": "BA BT BR FG LE TA", "basilicata": "PZ MT", "campania": "NA SA AV BN CE",
    "calabria": "CS CZ KR RC VV", "sicilia": "AG CL CT EN ME PA RG SR TP", "sardegna": "CA CI NU OR SS SU OT OG VS",
    "lazio": "RM LT FR RI VT", "abruzzo": "AQ CH PE TE", "molise": "CB IS",
    "toscana": "AR FI GR LI LU MS PI PO PT SI", "umbria": "PG TR", "marche": "AN AP FM MC PU",
    "emilia": "BO FC FE MO PC PR RA RE RN", "lombardia": "BG BS CO CR LC LO MN MI MB PV SO VA",
    "piemonte": "AL AT BI CN NO TO VB VC", "liguria": "GE IM SP SV", "veneto": "BL PD RO TV VE VI VR",
    "friuli": "GO PN TS UD", "trentino": "BZ TN", "valle d'aosta": "AO",
}
SIGLA = {s: reg for reg, sig in PROVINCE.items() for s in sig.split()}
# capoluoghi scritti per esteso negli indirizzi ("Felino (PARMA)", "Fanano (Modena)", "Ravenna 48122")
CITTA = {"parma": "emilia", "modena": "emilia", "ravenna": "emilia", "reggio emilia": "emilia", "bologna": "emilia",
         "messina": "sicilia", "palermo": "sicilia", "catania": "sicilia", "pisa": "toscana", "firenze": "toscana",
         "genova": "liguria", "imperia": "liguria", "cremona": "lombardia", "milano": "lombardia", "torino": "piemonte",
         "salerno": "campania", "napoli": "campania", "bari": "puglia", "lecce": "puglia", "cagliari": "sardegna",
         "brennero": "trentino", "trento": "trentino", "bolzano": "trentino"}
NAZIONI = {"spagna": ["españa", "espana", "spain", "spagna"], "francia": ["france", "francia"],
           "grecia": ["greece", "grecia"], "norvegia": ["norway", "norvegia"]}


def da_indirizzo(righe: list) -> tuple:
    """(regione, nazione, indizio) dalle righe PRODUTTORE delle schede: un voto per prodotto, vince la maggioranza
    (Delfino ha la sede a Cetara ma alcune conserve sono prodotte a Bagheria o in Spagna: conta la sede)."""
    reg, naz = Counter(), Counter()
    for t in righe:
        tl = t.lower()
        voti = set()
        for s in re.findall(r"\(([A-Z][A-Za-z])\)|\b([A-Z]{2})\s*(?:\)|$|\[)", t):
            s = (s[0] or s[1]).upper()
            if s in SIGLA:
                voti.add(SIGLA[s])
        for c, r in CITTA.items():
            if re.search(r"\b" + c + r"\b", tl):
                voti.add(r)
        reg.update(voti)
        # "Viale Spagna" e' un indirizzo, non una nazione
        for n, parole in NAZIONI.items():
            if any(re.search(r"(?<!viale )(?<!via )(?<!corso )(?<!piazza )\b" + p + r"\b", tl) for p in parole):
                naz[n] += 1
    tot_it = sum(reg.values())
    if naz and naz.most_common(1)[0][1] > tot_it:
        n = naz.most_common(1)[0][0]
        return (n if n in territorio.REGIONI else ""), n.capitalize(), f"indirizzo: {n}"
    if reg:
        r = reg.most_common(1)[0][0]
        return r, "Italia", f"indirizzo: {dict(reg.most_common(3))}"
    return "", "", ""


def main():
    db = chromadb.PersistentClient(os.path.join(RADICE, "database_vettoriale"))
    d = db.get_collection("catalogo_v2").get(include=["metadatas", "documents"])
    testi, indirizzi = defaultdict(list), defaultdict(list)
    for m, doc in zip(d["metadatas"], d["documents"]):
        nome = str(m.get("nome_fornitore") or "")
        testi[nome].append(doc or "")
        i = (doc or "").find("PRODUTTORE:")
        if i >= 0:
            indirizzi[nome].append(doc[i:i + 300].split("[DISCLAIMER")[0])

    abstract = defaultdict(list)
    wb = openpyxl.load_workbook(percorsi.dati("ABSTRACT.xlsx"), read_only=True)
    for riga in wb.worksheets[0].iter_rows(min_row=2, values_only=True):
        if riga and riga[1] and riga[4] and str(riga[3] or "").lower().startswith(("ident", "storia")):
            abstract[str(riga[1])].append(str(riga[4]))

    esistenti = {}
    if os.path.exists(USCITA):
        with open(USCITA, encoding="utf-8-sig", newline="") as f:
            esistenti = {r["nome_fornitore"]: r for r in csv.DictReader(f)}

    righe = []
    for nome in sorted(testi):
        e = esistenti.get(nome) or {}
        if (e.get("nota") or "").strip().lower() == "manuale":
            righe.append(e)
            continue
        reg_i, naz_i, ind_i = da_indirizzo(indirizzi[nome])
        da_abstract = Counter()
        for k, v in abstract.items():
            if stesso_fornitore(nome, k):
                for t in v:
                    # il nome dell'azienda non e' un indizio di luogo ("Agricola Lodigiana" e' a Ronsecco, Vercelli)
                    da_abstract.update(territorio.conteggi(t.lower().replace(nome.lower(), ' ').replace(k.lower(), ' ')))
        config = {cfg["regione"] for cfg in FORNITORI.values()
                  if cfg.get("regione") not in (None, "generico")
                  and any(stesso_fornitore(nome, n) or n in nome.lower() for n in cfg.get("nomi_match", []))}
        nota = ""
        if reg_i or naz_i:
            reg, naz, conf, fonte, ver = reg_i, naz_i, "alta", ind_i, "si"
            if config and reg_i and reg_i not in config:
                nota = f"fornitori_config diceva {', '.join(sorted(config))}: vale l'indirizzo"
            elif da_abstract and reg_i and da_abstract.most_common(1)[0][0] != reg_i:
                nota = f"le schede ABSTRACT nominano soprattutto {da_abstract.most_common(1)[0][0]}"
        elif da_abstract:
            reg = da_abstract.most_common(1)[0][0]
            quota = da_abstract[reg] / sum(da_abstract.values())
            conf = "alta" if quota >= 0.7 and da_abstract[reg] >= 2 else "media"
            naz = "Spagna" if reg == "spagna" else "Italia"
            fonte, ver = "abstract", ("si" if conf == "alta" and (not config or reg in config) else "")
        elif len(config) == 1:
            reg = config.pop()
            naz, conf, fonte, ver = ("Spagna" if reg == "spagna" else "Italia"), "media", "fornitori_config", ""
        else:
            reg, naz, conf, fonte, ver = "", "", "", "nessun indizio", ""
        righe.append({"nome_fornitore": nome, "regione": reg, "nazione": naz, "confidenza": conf, "fonte": fonte,
                      "indizi": ", ".join(f"{r}:{n}" for r, n in da_abstract.most_common(3)),
                      "verificata": ver, "nota": nota})

    with open(USCITA, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPI)
        w.writeheader()
        for r in righe:
            w.writerow({k: r.get(k, "") for k in CAMPI})
    print(f"{len(righe)} produttori -> {USCITA}")
    print("verificate:", sum(1 for r in righe if r.get("verificata") == "si"), "| fonti:", Counter(r["fonte"].split(":")[0] for r in righe))


if __name__ == "__main__":
    main()
