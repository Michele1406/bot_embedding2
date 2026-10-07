# -*- coding: utf-8 -*-
"""Riapplica in place (senza embedding ne' API) le regole dietetiche derivate ai metadati di catalogo_v2.

Serve quando cambiano le regole in core/attributi_derivati.py (es. nuovi ingredienti animali, fornitori senza
flag dieta) e non si vuole rigenerare tutto il catalogo. Uso:  python scripts/correggi_diete_catalogo.py [--dry]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import chromadb

from core.attributi_derivati import completa_flag_dieta, correggi_flag_dieta, vegano_ingredienti_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--collezione", default="catalogo_v2")
    a = ap.parse_args()
    col = chromadb.PersistentClient("database_vettoriale").get_collection(a.collezione)
    dati = col.get(include=["metadatas", "documents"])
    ids, metas, cambi = [], [], 0
    for i, m, d in zip(dati["ids"], dati["metadatas"], dati["documents"]):
        nuovo = dict(completa_flag_dieta(m, d))
        nuovo.update(correggi_flag_dieta({**m, **nuovo}, d))
        base = {**m, **nuovo}
        nuovo["vegano_ingredienti_ok"] = bool(vegano_ingredienti_ok(base, d))
        if any(m.get(k) != v for k, v in nuovo.items()):
            cambi += 1
            ids.append(i)
            metas.append(nuovo)
    print(f"[OK] {cambi} prodotti da aggiornare su {len(dati['ids'])}")
    if cambi and not a.dry:
        for k in range(0, len(ids), 200):
            col.update(ids=ids[k:k + 200], metadatas=metas[k:k + 200])
        print("[OK] metadati aggiornati")


if __name__ == "__main__":
    main()
