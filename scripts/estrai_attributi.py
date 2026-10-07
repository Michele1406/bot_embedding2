# -*- coding: utf-8 -*-
"""
estrai_attributi.py
===================
Fase 1 (T1.2) di implementationplan.md: per ogni prodotto del catalogo estrae
con un LLM (structured output, temperature 0) i campi di core/schema_prodotto.py
e li salva in data/attributi/attributi.jsonl (una riga JSON per prodotto).

- Riprendibile: i prodotti gia' presenti nel file vengono saltati.
- Non modifica ChromaDB ne' la cartella dati dei prodotti (sola lettura).
- Uso:
    python scripts/estrai_attributi.py --pilot          # 36 prodotti scelti (wurstel, salumi, latte, ...)
    python scripts/estrai_attributi.py --limit 100      # primi 100 non ancora fatti
    python scripts/estrai_attributi.py                  # tutto il catalogo
"""
import argparse
import json
import os
import sys
import time

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

from dotenv import load_dotenv

load_dotenv(os.path.join(RADICE, ".env"))

import chromadb
from google import genai
from google.genai import types

from core.schema_prodotto import BatchAttributi, RUOLI_USO
from core.testo_prodotto import nome_prodotto

MODELLO = os.getenv("LLM_PRINCIPALE", "models/gemini-3.5-flash")
CARTELLA_OUT = os.path.join(RADICE, "data", "attributi")
FILE_OUT = os.path.join(CARTELLA_OUT, "attributi.jsonl")
DIMENSIONE_BATCH = 10
REPARTI_NON_PRODOTTO = ("AZIENDALE", "LOGISTICA")

PROMPT_SISTEMA = f"""Sei un esperto di prodotti food per il canale HORECA (ristoranti, bar, pizzerie, hotel).
Classifichi prodotti di un catalogo di distribuzione B2B. Per ogni prodotto restituisci gli attributi richiesti
basandoti SOLO su nome, categoria e testo della scheda forniti; se un dato non e' deducibile abbassa `confidenza`.

Ruoli d'uso ammessi (lista chiusa): {', '.join(RUOLI_USO)}.

Criteri importanti:
- `ruoli_uso` descrive come il prodotto viene REALMENTE usato in cucina/sala, non la sua categoria merceologica.
  Esempi: wurstel -> [panino_fast_food, cottura_griglia_padella] (NON tagliere); prosciutto crudo stagionato, culatello,
  salame stagionato, bresaola -> [tagliere, antipasto, aperitivo]; mortadella -> [tagliere, panino_fast_food, antipasto];
  guanciale/pancetta da cuocere -> [ingrediente_base, cottura_griglia_padella]; hamburger/carne macinata/carne cruda
  -> [secondo, cottura_griglia_padella]; formaggio stagionato da tavola -> [tagliere, antipasto];
  mozzarella/burrata -> [antipasto, secondo, pizza_pane]; formaggio julienne/grattugiato/per pizza -> [ingrediente_base, pizza_pane];
  latte/burro/panna -> [ingrediente_base, colazione]; pasta secca -> [primo]; gelato -> [dolce].
- `origine_proteina`: i "salumi di pesce" (prosciutto di tonno, bresaola di tonno, mortadella di mare...) sono 'pesce', mai carne.
- `richiede_cottura`: True per wurstel, carne cruda, pasta, riso, surgelati da friggere/cuocere; False per salumi stagionati, formaggi, conserve pronte.
- `tipo_prodotto`: nome generico minuscolo, senza marchio, senza grammatura.
- `id`: copia esattamente l'id ricevuto. Rispondi con un elemento per ogni prodotto ricevuto."""


def carica_prodotti():
    col = chromadb.PersistentClient(path=os.path.join(RADICE, "database_vettoriale")).get_collection("catalogo_sofood")
    d = col.get(include=["metadatas", "documents"])
    prodotti = []
    for i, m, doc in zip(d["ids"], d["metadatas"], d["documents"]):
        if str(m.get("reparto", "")).upper() in REPARTI_NON_PRODOTTO or not m.get("reparto"):
            continue
        prodotti.append({"id": i, "meta": m, "doc": doc})
    return prodotti


def gia_fatti():
    """Id gia' estratti in QUALSIASI file attributi*.jsonl della cartella (cosi' gli shard
    paralleli e le riprese non rifanno lavoro gia' pagato)."""
    import glob
    fatti = set()
    for percorso in glob.glob(os.path.join(os.path.dirname(FILE_OUT), "attributi*.jsonl")):
        with open(percorso, encoding="utf-8") as f:
            for r in f:
                if r.strip():
                    try:
                        fatti.add(json.loads(r)["id"])
                    except ValueError:
                        pass  # riga troncata da un'interruzione
    return fatti


def scheda_breve(p, max_char=650):
    doc = p["doc"].split("[DISCLAIMER LEGALE SOFOOD]")[0].replace("﻿", "").strip()
    m = p["meta"]
    return (f"id: {p['id']}\n"
            f"nome: {nome_prodotto(p['doc'])}\n"
            f"reparto: {m.get('reparto')} | sottocategoria: {m.get('sottocategoria')} | dettaglio: {m.get('specifiche_liv4')} "
            f"| formato: {m.get('formato_variante_liv5')}\n"
            f"scheda: {doc[:max_char]}")


def scegli_pilot(prodotti):
    """36 prodotti rappresentativi, inclusi i casi critici emersi dai test."""
    per_id = {p["id"]: p for p in prodotti}
    obbligati = ["19010045_FRANCHI19", "19010485_22010008", "19010485_22010012", "19010883_OBE4047", "19010925_PSCTTNN50",
                 "19010925_MRTMRE50", "19010826_CD51", "19010693_PROFUMO62", "19010792_SECADE08", "19010699_LOVISON26",
                 "19010448_MG-INS1", "19010890_NOB006", "19010003_RP CIOCCOLATO", "19010004_021", "19010498_BBFRU3",
                 "19010926_PVAZ030139", "19010071_00079", "19010883_OBE1165"]
    scelti = [per_id[i] for i in obbligati if i in per_id]
    visti = {p["id"] for p in scelti}
    import random
    random.seed(7)
    altri = [p for p in prodotti if p["id"] not in visti]
    scelti += random.sample(altri, max(0, 36 - len(scelti)))
    return scelti


_ULTIMA_CHIAMATA = [0.0]
INTERVALLO_MIN_SEC = float(os.getenv('ESTRAI_INTERVALLO_SEC', '6'))  # <=10 chiamate/min (limite modello: 15/min)


def chiama_llm(client, batch):
    testo = "Classifica questi prodotti:\n\n" + "\n\n---\n\n".join(scheda_breve(p) for p in batch)
    ultimo_errore = None
    for tentativo in range(3):
        attesa = INTERVALLO_MIN_SEC - (time.time() - _ULTIMA_CHIAMATA[0])
        if attesa > 0:
            time.sleep(attesa)
        _ULTIMA_CHIAMATA[0] = time.time()
        try:
            r = client.models.generate_content(
                model=MODELLO, contents=testo,
                config=types.GenerateContentConfig(
                    system_instruction=PROMPT_SISTEMA, temperature=0.0,
                    response_mime_type="application/json", response_schema=BatchAttributi),
            )
            dati = json.loads(r.text)
            return dati["prodotti"] if isinstance(dati, dict) else dati
        except Exception as e:  # rete, quota, JSON malformato: si riprova
            ultimo_errore = e
            time.sleep(20 * (tentativo + 1))
    raise RuntimeError(f"batch fallito dopo 3 tentativi: {ultimo_errore}")


def main():
    global FILE_OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=FILE_OUT)
    ap.add_argument("--shard", default="", help="k/n: elabora solo il k-esimo di n gruppi (0-based), per eseguire in parallelo")
    args = ap.parse_args()

    FILE_OUT = args.out
    os.makedirs(os.path.dirname(FILE_OUT), exist_ok=True)

    prodotti = carica_prodotti()
    if args.pilot:
        prodotti = scegli_pilot(prodotti)
    fatti = gia_fatti()
    da_fare = [p for p in prodotti if p["id"] not in fatti]
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        da_fare = da_fare[k::n]
    if args.limit:
        da_fare = da_fare[:args.limit]
    print(f"[INFO] prodotti nel catalogo: {len(prodotti)} | gia' fatti: {len(fatti)} | da fare ora: {len(da_fare)} | modello: {MODELLO}")

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    ids_attesi_tot = 0
    mancanti = []
    with open(FILE_OUT, "a", encoding="utf-8") as out:
        for k in range(0, len(da_fare), DIMENSIONE_BATCH):
            batch = da_fare[k:k + DIMENSIONE_BATCH]
            try:
                risultati = chiama_llm(client, batch)
            except RuntimeError as e:
                print(f"[ERRORE] {e}")
                mancanti += [p["id"] for p in batch]
                continue
            per_id = {r["id"]: r for r in risultati}
            for p in batch:
                r = per_id.get(p["id"])
                if r is None:
                    mancanti.append(p["id"])
                    continue
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
                ids_attesi_tot += 1
            out.flush()
            print(f"[OK] {min(k + DIMENSIONE_BATCH, len(da_fare))}/{len(da_fare)}")
    print(f"[FINE] scritti {ids_attesi_tot} | mancanti {len(mancanti)}: {mancanti[:10]}")


if __name__ == "__main__":
    main()
