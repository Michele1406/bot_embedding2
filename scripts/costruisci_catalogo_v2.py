# -*- coding: utf-8 -*-
"""
costruisci_catalogo_v2.py
=========================
Fase 1/3 di implementationplan.md. Crea la collezione `catalogo_v2` copiando
da `catalogo_sofood` documenti, metadati e VETTORI GIA' CALCOLATI (nessuna
chiamata di embedding) e aggiungendo i metadati arricchiti:

  da data/attributi/attributi.jsonl (LLM):
      tipo_prodotto, origine_proteina, ruoli_uso ("|tagliere|antipasto|"),
      uso_<ruolo> (booleani filtrabili), richiede_cottura, fascia
  da core/parse_formato.py (deterministico):
      formato_valore, formato_unita, canale_formato, peso_variabile

I documenti AZIENDALE/LOGISTICA (schede fornitore, consegne) vanno nella
collezione separata `aziende_sofood`, cosi' il catalogo contiene solo prodotti.
La collezione originale NON viene modificata.

Uso:  python scripts/costruisci_catalogo_v2.py [--ricrea]
"""
import argparse
import json
import os
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

import chromadb

from core.parse_formato import formato_prodotto
from core.attributi_derivati import classifica_snack, vegano_ingredienti_ok, completa_flag_dieta, correggi_flag_dieta
from core.schema_prodotto import RUOLI_USO

FILE_ATTRIBUTI = os.path.join(RADICE, "data", "attributi", "attributi.jsonl")
NON_PRODOTTO = ("AZIENDALE", "LOGISTICA")
# categorie documentali (es. i 11 "CALENDARIO ORDINI FRESCHI" censiti come prodotti SALUMI/MARE/FORMAGGI...)
CATEGORIE_NON_PRODOTTO = ("SCHEDA AZIENDALE FORNITORE", "INFO LOGISTICA E ORDINI", "REGOLE DI CONSEGNA ZONALE",
                          "RICETTARIO E REGOLE", "SCHEDA FORNITORE", "CONSEGNE E SPEDIZIONI")
BLOCCO = 200


def leggi_attributi(percorso):
    """Unisce tutti i file attributi*.jsonl della cartella (run unico, shard o riprese)."""
    import glob
    out = {}
    for file in sorted(glob.glob(os.path.join(os.path.dirname(percorso), "attributi*.jsonl"))):
        with open(file, encoding="utf-8") as f:
            for riga in f:
                if riga.strip():
                    try:
                        r = json.loads(riga)
                    except ValueError:
                        continue
                    out[r["id"]] = r
    return out


USI_INGREDIENTE = ["topping_finitura", "farcitura", "condimento_primi", "base_cottura", "impasto_panificazione",
                   "aroma_spezia", "dolci_pasticceria", "composizione_sottoli", "accompagnamento_tagliere",
                   "salsa_accompagnamento"]


def leggi_uso(percorso):
    """modalita_uso*.jsonl: singolo | ingrediente | entrambi (+ usi come ingrediente)."""
    import glob
    out = {}
    for file in sorted(glob.glob(os.path.join(os.path.dirname(percorso), "modalita_uso*.jsonl"))):
        with open(file, encoding="utf-8") as f:
            for riga in f:
                if riga.strip():
                    try:
                        r = json.loads(riga)
                    except ValueError:
                        continue
                    out[r["id"]] = r
    return out


def metadati_arricchiti(meta, doc, attr, uso=None):
    m = {k: v for k, v in meta.items() if v is not None}  # Chroma non accetta None
    f = formato_prodotto(meta, doc)
    if f["valore"] is not None:
        m["formato_valore"] = float(f["valore"])
        m["formato_unita"] = f["unita"]
    m["canale_formato"] = f["canale_formato"]
    m["peso_variabile"] = bool(f["peso_variabile"])
    d = classifica_snack(meta, doc)  # regole deterministiche: olive in salamoia/olio, taralli, patatine, ...
    m["snack_tipo"] = d["snack_tipo"]
    m["conservazione"] = d["conservazione"]
    m["denocciolate"] = bool(d["denocciolate"])
    m.update(completa_flag_dieta(meta, doc))  # fornitori senza flag dietetici: dedotti dagli ingredienti
    m.update(correggi_flag_dieta(m, doc))     # flag vegano SI smentito dagli ingredienti (errore del dato sorgente)
    m["vegano_ingredienti_ok"] = bool(vegano_ingredienti_ok(m, doc))
    if attr:
        m["tipo_prodotto"] = attr["tipo_prodotto"]
        m["origine_proteina"] = attr["origine_proteina"]
        ruoli = attr.get("ruoli_uso", [])
        m["ruoli_uso"] = "|" + "|".join(ruoli) + "|"
        for r in RUOLI_USO:
            m[f"uso_{r}"] = r in ruoli
        m["richiede_cottura"] = bool(attr["richiede_cottura"])
        m["fascia"] = attr["fascia"]
        m["attributi_ok"] = True
    else:
        m["attributi_ok"] = False
    if uso:
        m["modalita_uso"] = uso["modalita_uso"]
        usi = uso.get("usi_come_ingrediente", [])
        m["usi_ingr"] = "|" + "|".join(usi) + "|" if usi else ""
        for u in USI_INGREDIENTE:
            m[f"ingr_{u}"] = u in usi
        m["modalita_ok"] = True
    else:
        m["modalita_ok"] = False  # sconosciuta: trattata come prodotto singolo (non si nasconde nulla)
    return m


def copia(src, dst, ids, metadatas, documents, embeddings):
    for k in range(0, len(ids), BLOCCO):
        dst.upsert(ids=ids[k:k + BLOCCO], metadatas=metadatas[k:k + BLOCCO],
                   documents=documents[k:k + BLOCCO], embeddings=embeddings[k:k + BLOCCO])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ricrea", action="store_true", help="cancella e ricrea catalogo_v2 / aziende_sofood")
    args = ap.parse_args()

    attributi = leggi_attributi(FILE_ATTRIBUTI)
    uso = leggi_uso(FILE_ATTRIBUTI)
    db = chromadb.PersistentClient(path=os.path.join(RADICE, "database_vettoriale"))
    src = db.get_collection("catalogo_sofood")
    d = src.get(include=["metadatas", "documents", "embeddings"])

    if args.ricrea:
        for nome in ("catalogo_v2", "aziende_sofood"):
            try:
                db.delete_collection(nome)
            except Exception:
                pass
    # stesso spazio vettoriale della collezione originale (l2)
    v2 = db.get_or_create_collection("catalogo_v2", metadata={"hnsw:space": "l2"})
    az = db.get_or_create_collection("aziende_sofood", metadata={"hnsw:space": "l2"})

    p = {"ids": [], "metadatas": [], "documents": [], "embeddings": []}
    a = {"ids": [], "metadatas": [], "documents": [], "embeddings": []}
    senza_attr = []
    for i, m, doc, e in zip(d["ids"], d["metadatas"], d["documents"], d["embeddings"]):
        if (str(m.get("reparto", "")).upper() in NON_PRODOTTO or not m.get("reparto")
                or str(m.get("categoria_prodotto", "")).upper() in CATEGORIE_NON_PRODOTTO):
            tgt = a
            meta = {k: v for k, v in m.items() if v is not None}
        else:
            tgt = p
            meta = metadati_arricchiti(m, doc, attributi.get(i), uso.get(i))
            if i not in attributi:
                senza_attr.append(i)
        tgt["ids"].append(i)
        tgt["metadatas"].append(meta)
        tgt["documents"].append(doc)
        tgt["embeddings"].append([float(x) for x in e])

    copia(src, v2, **p)
    copia(src, az, **a)
    print(f"[OK] catalogo_v2: {v2.count()} prodotti | aziende_sofood: {az.count()} documenti")
    print(f"[INFO] prodotti senza attributi LLM: {len(senza_attr)} {senza_attr[:8]}")


if __name__ == "__main__":
    main()
