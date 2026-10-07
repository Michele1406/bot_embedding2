# -*- coding: utf-8 -*-
"""
aggiorna_ricettario.py
======================
Crea la collezione `ricette_v2` a partire da `ricette_sofood` (documenti, metadati e
vettori gia' calcolati) applicando core/ricettario_extra.yaml (correzioni + nuove ricette)
e aggiungendo per ogni ricetta `copertura_catalogo` (alta/media/bassa): quanta parte degli
ingredienti protagonisti e' realmente reperibile nel catalogo_v2.

Fa anche cose di manutenzione che prima mancavano:
  * esporta in data/ricettario_db_only.json le ricette presenti SOLO nel database (non nel
    file Excel), cosi' non si perdono se il DB viene ricreato;
  * scrive data/audit_ricettario.csv con la copertura di ogni ricetta (sola lettura sul catalogo).

Embedding: solo per le ricette nuove o con ingredienti modificati (poche chiamate).

Uso:  python scripts/aggiorna_ricettario.py [--ricrea]
"""
import argparse
import csv
import json
import os
import re
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

import chromadb
import openpyxl
import yaml
from dotenv import load_dotenv

load_dotenv(os.path.join(RADICE, ".env"))

from core import ontologia
from core.testo_prodotto import nome_prodotto

FILE_EXCEL = os.path.join(RADICE, "Ricettario_SO_FOOD.xlsx")
FILE_EXTRA = os.path.join(RADICE, "core", "ricettario_extra.yaml")
CARTELLA_DATA = os.path.join(RADICE, "data")
FOGLI = ["Antipasti", "Primi", "Secondi", "Contorni", "Pizze", "Dolci", "Taglieri", "Aperitivi_Tris", "Panini_Burger"]
MODELLO_EMB = "models/gemini-embedding-2"
STOP = {"di", "da", "al", "alla", "con", "per", "e", "in", "del", "della", "dei", "delle", "fresco", "fresca",
        "fresche", "freschi", "misto", "mista", "misti", "miste", "tipo", "pronto", "pronta", "artigianale",
        "artigianali", "qualita", "olio", "extravergine", "oliva", "sale", "pepe", "una", "uno"}


def id_in_excel():
    wb = openpyxl.load_workbook(FILE_EXCEL, read_only=True)
    ids = set()
    for n in FOGLI:
        for r in wb[n].iter_rows(min_row=2, values_only=True):
            if r[0]:
                ids.add(str(r[0]).strip())
    return ids


def testo_embedding(nome, categoria, stile, dieta, ingredienti, descrizione):
    elenco = ", ".join(i["INGREDIENTE_GENERICO"] for i in ingredienti)
    return f"{nome} ({categoria}). Stile: {stile}. Adatto a dieta: {dieta}. Ingredienti: {elenco}. {descrizione}"


def indice_catalogo(db):
    d = db.get_collection("catalogo_v2").get(include=["metadatas", "documents"])
    out = []
    for i, m, x in zip(d["ids"], d["metadatas"], d["documents"]):
        out.append({"id": i, "metadata": m, "document": x,
                    "testo": (nome_prodotto(x) + " " + str(m.get("tipo_prodotto")) + " " + str(m.get("sottocategoria"))
                              + " " + str(m.get("specifiche_liv4")) + " " + str(m.get("nome_fornitore"))).lower()})
    return out


def _tok(s):
    sin = ontologia._carica().get("sinonimi") or {}
    s = s.lower()
    for k, v in sin.items():
        if k in s and isinstance(v, str):
            s = s.replace(k, v)
    return [t[:6] for t in re.findall(r"[a-zàèéìòù]+", s) if len(t) >= 4 and t not in STOP]


def _alternative(nome_ingr):
    """"bucatini o spaghetti di grano duro" -> ["bucatini", "spaghetti di grano duro"]: basta una alternativa a catalogo."""
    parti = [p.strip() for p in re.split(r"\s+o\s+|/|,", nome_ingr) if p.strip()]
    return parti or [nome_ingr]


def ingrediente_coperto(nome_ingr, catalogo) -> bool:
    for alt in _alternative(nome_ingr):
        spec = ontologia.risolvi_slot(alt, "")
        if spec:
            if any(ontologia.prodotto_rispetta(p["metadata"], spec) for p in catalogo):
                return True
            continue
        t = _tok(alt)
        if not t:
            return True
        # il primo termine e' il nome dell'ingrediente (spaghetti, pecorino...); i successivi sono qualificativi
        if any(t[0] in p["testo"] and (len(t) == 1 or any(x in p["testo"] for x in t[1:3])) for p in catalogo):
            return True
        if any(t[0] in p["testo"] for p in catalogo) and len(t) >= 3:
            return True
    return False


def copertura(ingredienti, catalogo):
    prot = [i for i in ingredienti if str(i.get("RUOLO", "")).lower() == "protagonista"] or ingredienti
    if not prot:
        return "sconosciuta", 0, 0, []
    mancanti = [i["INGREDIENTE_GENERICO"] for i in prot if not ingrediente_coperto(i["INGREDIENTE_GENERICO"], catalogo)]
    ok = len(prot) - len(mancanti)
    frac = ok / len(prot)
    return ("alta" if frac >= 0.999 else "media" if frac >= 0.5 else "bassa"), len(prot), ok, mancanti


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ricrea", action="store_true")
    ap.add_argument("--senza-embedding", action="store_true", help="non chiama l'API (le ricette nuove restano escluse)")
    args = ap.parse_args()

    os.makedirs(CARTELLA_DATA, exist_ok=True)
    db = chromadb.PersistentClient(path=os.path.join(RADICE, "database_vettoriale"))
    base = db.get_collection("ricette_sofood")
    d = base.get(include=["metadatas", "documents", "embeddings"])
    extra = yaml.safe_load(open(FILE_EXTRA, encoding="utf-8")) or {}
    catalogo = indice_catalogo(db)

    # 1) backup delle ricette presenti solo nel DB
    excel = id_in_excel()
    solo_db = [{"id": i, "documento": x, "metadati": m} for i, m, x in zip(d["ids"], d["metadatas"], d["documents"]) if i not in excel]
    with open(os.path.join(CARTELLA_DATA, "ricettario_db_only.json"), "w", encoding="utf-8") as f:
        json.dump(solo_db, f, ensure_ascii=False, indent=1)
    print(f"[OK] {len(solo_db)} ricette presenti solo nel DB esportate in data/ricettario_db_only.json")

    ids, docs, metas, embs = [], [], [], []
    da_reembeddare = {}
    for i, m, x, e in zip(d["ids"], d["metadatas"], d["documents"], d["embeddings"]):
        m = {k: v for k, v in m.items() if v is not None}
        ids.append(i); docs.append(x); metas.append(m); embs.append([float(v) for v in e])
    pos = {i: k for k, i in enumerate(ids)}

    # 2) patch
    for rid, p in (extra.get("patch") or {}).items():
        if rid not in pos:
            print(f"[ATTENZIONE] patch su ricetta inesistente: {rid}")
            continue
        k = pos[rid]
        m = metas[k]
        for campo, val in p.items():
            if campo == "ingredienti_sostituisci":
                ingr = json.loads(m.get("ingredienti_json", "[]"))
                for r in ingr:
                    if r["INGREDIENTE_GENERICO"] in val:
                        r["INGREDIENTE_GENERICO"] = val[r["INGREDIENTE_GENERICO"]]
                m["ingredienti_json"] = json.dumps(ingr, ensure_ascii=False)
                docs[k] = testo_embedding(m.get("nome_piatto", ""), m.get("categoria", ""), m.get("stile_cucina", ""),
                                          m.get("tag_dieta", ""), ingr, str(docs[k]).split(". ")[-1])
                da_reembeddare[rid] = k
            else:
                m[campo.replace("descrizione", "descrizione")] = str(val).strip()

    # 3) nuove
    for r in extra.get("nuove") or []:
        ingr = [{"INGREDIENTE_GENERICO": x["ingrediente"], "CATEGORIA_ATTESA": x["categoria_attesa"],
                 "RUOLO": x["ruolo"], "NOTE_INGREDIENTE": x.get("note", "")} for x in r["ingredienti"]]
        meta = {"tipo_voce": "ricetta", "nome_piatto": r["nome_piatto"], "categoria": r["categoria"],
                "foglio_origine": "ricettario_extra.yaml", "stile_cucina": r["stile_cucina"],
                "tag_dieta": r["tag_dieta"], "tag_protagonista": r["tag_protagonista"], "canale": r["canale"],
                "canali_adatti": r["canali_adatti"], "canali_sconsigliati": r.get("canali_sconsigliati", ""),
                "stato_verifica_catalogo": r.get("stato_verifica_catalogo", "da_verificare"),
                "note_composizione": r.get("note_composizione", ""),
                "ingredienti_json": json.dumps(ingr, ensure_ascii=False)}
        doc = testo_embedding(r["nome_piatto"], r["categoria"], r["stile_cucina"], r["tag_dieta"], ingr, r["descrizione"])
        if r["id"] in pos:
            k = pos[r["id"]]; docs[k] = doc; metas[k] = meta
        else:
            pos[r["id"]] = len(ids); ids.append(r["id"]); docs.append(doc); metas.append(meta); embs.append(None)
        da_reembeddare[r["id"]] = pos[r["id"]]

    # 4) metadati mancanti (le 112 ricette solo-DB): valori espliciti, mai inventati
    for m in metas:
        m.setdefault("tag_dieta", "")            # sconosciuto -> trattato come NON adatto a vegani/vegetariani
        m.setdefault("stato_verifica_catalogo", "da_verificare")
        m.setdefault("canali_adatti", "")
        m.setdefault("canali_sconsigliati", "")
        m.setdefault("categoria", "sconosciuta")

    # 5) copertura
    righe = []
    for k, rid in enumerate(ids):
        ingr = json.loads(metas[k].get("ingredienti_json") or "[]")
        etichetta, n_prot, n_ok, manc = copertura(ingr, catalogo)
        metas[k]["copertura_catalogo"] = etichetta
        righe.append([rid, metas[k].get("nome_piatto"), metas[k].get("categoria"), n_prot, n_ok, etichetta, "; ".join(manc)])
    with open(os.path.join(CARTELLA_DATA, "audit_ricettario.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "nome", "categoria", "protagonisti", "coperti_da_catalogo", "copertura", "ingredienti_protagonisti_non_coperti"])
        w.writerows(righe)

    # 6) embedding solo dove serve
    if da_reembeddare and not args.senza_embedding:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        for rid, k in da_reembeddare.items():
            r = client.models.embed_content(model=MODELLO_EMB, contents=[docs[k]],
                                            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"))
            v = r.embeddings[0]
            embs_k = list(v.values if hasattr(v, "values") else v)
            if k < len(embs):
                embs[k] = embs_k
            else:
                embs.append(embs_k)
        print(f"[OK] {len(da_reembeddare)} embedding calcolati (nuove/modificate)")
    keep = [k for k in range(len(ids)) if k < len(embs) and embs[k] is not None]
    if len(keep) < len(ids):
        print(f"[ATTENZIONE] {len(ids) - len(keep)} ricette senza embedding escluse")

    if args.ricrea:
        try:
            db.delete_collection("ricette_v2")
        except Exception:
            pass
    v2 = db.get_or_create_collection("ricette_v2", metadata={"hnsw:space": "l2"})
    for s in range(0, len(keep), 200):
        blocco = keep[s:s + 200]
        v2.upsert(ids=[ids[k] for k in blocco], documents=[docs[k] for k in blocco],
                  metadatas=[metas[k] for k in blocco], embeddings=[embs[k] for k in blocco])
    import collections
    print(f"[OK] ricette_v2: {v2.count()} ricette | copertura: {dict(collections.Counter(r[5] for r in righe))}")


if __name__ == "__main__":
    main()
