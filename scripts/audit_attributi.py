# -*- coding: utf-8 -*-
"""Audit automatico della qualita' degli attributi di catalogo_v2 (T1.4 del piano: revisione dei casi dubbi).

Controlli incrociati tra attributi estratti dall'LLM, regole deterministiche e dati sorgente. Nessuna API.
Scrive data/audit_attributi.csv (codice;nome;controllo;dettaglio) e stampa il riepilogo.

  C1 origine_proteina vegetale ma ingredienti con ingredienti animali
  C2 reparto animale (SALUMI/CARNE/MARE) con origine_proteina vegetale/nessuna
  C3 flag vegano SI ma ingredienti animali (possibile errore del dato sorgente: da verificare con il fornitore)
  C4 ruoli_uso incoerenti (dolce/colazione su salumi o pesce; bevanda su pasta...)
  C5 modalita_uso 'ingrediente' per prodotti da ciotolina (snack_tipo valorizzato)
  C6 tipo_prodotto vuoto o molto lungo
  C7 tipo_prodotto simile al nome ma sottocategoria incompatibile (es. 'olive' in reparto SALUMI)
Uso: python scripts/audit_attributi.py
"""
import collections
import csv
import os
import re
import sys

RADICE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RADICE)

import chromadb

from core.attributi_derivati import _RE_ANIMALI, sezione_ingredienti
from core.testo_prodotto import nome_prodotto


def controlli(m: dict, doc: str):
    ing = sezione_ingredienti(doc)
    ing_pulito = re.sub(r"(puo|può)\s+contenere[^.]*|tracce[^.]*", "", ing, flags=re.IGNORECASE)
    animale_ing = bool(ing_pulito.strip()) and bool(_RE_ANIMALI.search(ing_pulito))
    rep = str(m.get("reparto") or "")
    orig = str(m.get("origine_proteina") or "")
    if orig == "vegetale" and animale_ing:
        yield "C1", f"origine vegetale ma ingredienti: {_RE_ANIMALI.search(ing_pulito).group(0)}"
    if rep in ("SALUMI", "CARNE", "MARE") and orig in ("vegetale", "nessuna", ""):
        yield "C2", f"reparto {rep} con origine_proteina='{orig}'"
    if str(m.get("vegano") or "").upper() == "SI" and animale_ing:
        yield "C3", f"flag vegano SI ma ingredienti: {_RE_ANIMALI.search(ing_pulito).group(0)}"
    if rep in ("SALUMI", "CARNE", "MARE") and (m.get("uso_dolce") or m.get("uso_colazione") or m.get("uso_bevanda")):
        yield "C4", "ruoli dolce/colazione/bevanda su prodotto animale"
    if str(m.get("sottocategoria") or "").startswith(("PASTA", "RISO")) and m.get("uso_bevanda"):
        yield "C4", "ruolo bevanda su pasta/riso"
    if m.get("modalita_uso") == "ingrediente" and m.get("snack_tipo"):
        yield "C5", f"snack_tipo={m.get('snack_tipo')} ma modalita_uso=ingrediente"
    t = str(m.get("tipo_prodotto") or "")
    if not t.strip() or len(t) > 60:
        yield "C6", f"tipo_prodotto '{t[:40]}'"
    if t.startswith("olive") and rep in ("SALUMI", "CARNE", "FORMAGGI"):
        yield "C7", f"tipo '{t}' in reparto {rep}"
    # C8: allergene dichiarato come "contiene" ma nella scheda compare solo tra le tracce ("puo' contenere"), oppure
    # "tracce: Nessuna" con frasi "puo' contenere" nella scheda (caso reale: taralli con arachidi tra gli allergeni)
    from core.allergeni import CATEGORIE, _contiene
    dich = str(m.get("allergeni") or "")
    frasi_tracce = " ".join(re.findall(r"(?:puo|può)\s+contenere[^.\n]*|tracce\s+di[^.\n]*", (doc or "").lower()))
    for cat in CATEGORIE:
        if _contiene(dich, cat) and frasi_tracce and _contiene(frasi_tracce, cat) and not _contiene(ing_pulito, cat):
            yield "C8", f"'{cat}' tra gli allergeni ma nella scheda e' solo una traccia"
    if str(m.get("tracce_di") or "").strip().lower() in ("nessuna", "") and frasi_tracce:
        yield "C8", f"tracce_di vuoto ma la scheda dice: {frasi_tracce[:80]}"


def main():
    col = chromadb.PersistentClient(os.path.join(RADICE, "database_vettoriale")).get_collection(
        os.getenv("CATALOGO_COLLECTION", "catalogo_v2"))
    d = col.get(include=["metadatas", "documents"])
    righe, cont = [], collections.Counter()
    for i, m, doc in zip(d["ids"], d["metadatas"], d["documents"]):
        for cod, det in controlli(m, doc):
            cont[cod] += 1
            righe.append((str(m.get("codice_prodotto") or i), nome_prodotto(doc)[:70], cod, det))
    out = os.path.join(RADICE, "data", "audit_attributi.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["codice", "nome", "controllo", "dettaglio"])
        w.writerows(sorted(righe, key=lambda r: (r[2], r[1])))
    print(f"[OK] {len(righe)} segnalazioni su {len(d['ids'])} prodotti -> {out}")
    print("     per controllo:", dict(cont))
    for cod in sorted(cont):
        for r in [x for x in righe if x[2] == cod][:4]:
            print(f"  {cod} {r[1][:50]:<50} {r[3][:70]}")


if __name__ == "__main__":
    main()
