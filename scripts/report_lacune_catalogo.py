# -*- coding: utf-8 -*-
"""Report delle lacune di catalogo da girare a So Food (acquisti/nuovi fornitori).

Due fonti, entrambe in sola lettura:
  1. ingredienti PROTAGONISTI delle ricette che il catalogo non copre (data/audit_ricettario.csv, da aggiorna_ricettario.py)
  2. termini che i clienti hanno chiesto nei log reali (log/chat/*.json) e che non compaiono in nessun prodotto

Scrive data/lacune_catalogo.csv (colonne: fonte, termine, occorrenze, esempi) ordinato per occorrenze.
Uso: python scripts/report_lacune_catalogo.py
"""
import collections
import csv
import glob
import json
import os
import sys

RADICE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RADICE)


def da_ricettario():
    percorso = os.path.join(RADICE, "data", "audit_ricettario.csv")
    cont, esempi = collections.Counter(), collections.defaultdict(list)
    if not os.path.exists(percorso):
        return cont, esempi
    with open(percorso, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            for ingr in filter(None, (x.strip() for x in r["ingredienti_protagonisti_non_coperti"].split(";"))):
                cont[ingr.lower()] += 1
                if len(esempi[ingr.lower()]) < 3:
                    esempi[ingr.lower()].append(r["nome"][:40])
    return cont, esempi


def da_log():
    import chromadb
    from core import copertura_richiesta, retrieval_utils as ru
    col = chromadb.PersistentClient(os.path.join(RADICE, "database_vettoriale")).get_collection(
        os.getenv("CATALOGO_COLLECTION", "catalogo_v2"))
    indice = ru.costruisci_indice_testuale(col)
    cont, esempi = collections.Counter(), collections.defaultdict(list)
    for f in glob.glob(os.path.join(RADICE, "log", "chat", "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        msgs = d if isinstance(d, list) else d.get("log_chat") or d.get("messaggi") or []
        for m in msgs:
            if isinstance(m, dict) and m.get("ruolo") == "utente":
                for t in copertura_richiesta.termini_senza_riscontro(m.get("testo") or "", indice):
                    cont[t] += 1
                    if len(esempi[t]) < 3:
                        esempi[t].append((m.get("testo") or "")[:60])
    return cont, esempi


def main():
    righe = []
    c1, e1 = da_ricettario()
    righe += [("ricettario", t, n, " | ".join(e1[t])) for t, n in c1.items()]
    try:
        c2, e2 = da_log()
        righe += [("richieste_clienti", t, n, " | ".join(e2[t])) for t, n in c2.items()]
    except Exception as e:
        print(f"[ATTENZIONE] log clienti non analizzati: {e}")
    righe.sort(key=lambda r: (-r[2], r[0], r[1]))
    out = os.path.join(RADICE, "data", "lacune_catalogo.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["fonte", "termine", "occorrenze", "esempi"])
        w.writerows(righe)
    print(f"[OK] {len(righe)} lacune in {out}")
    for r in righe[:25]:
        print(f"  {r[2]:>3}  {r[0]:<18} {r[1]}")


if __name__ == "__main__":
    main()
