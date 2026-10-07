# -*- coding: utf-8 -*-
"""
estrai_modalita_uso.py
======================
Per ogni prodotto stabilisce COME puo' essere proposto:

  singolo      si serve/consuma da solo (salumi, formaggi, olive, sottoli, taralli, paste, dolci...)
  ingrediente  entra solo in preparazioni o finiture (petali/cristalli di tartufo, spezie, farine, granelle...)
  entrambi     ha tutte e due le vite (anacardi al tartufo: snack in ciotola E topping tritato;
               carciofi sott'olio: singoli E dentro una composizione di sottoli)

e, per ingrediente/entrambi, in quali impieghi funziona (topping, farcitura, condimento per primi, ...).

Output: data/attributi/modalita_uso*.jsonl (uno per prodotto, riprendibile). Stesso schema operativo di
estrai_attributi.py (lotti da 10, <=10 chiamate/minuto). I file `attributi*.jsonl` NON vengono toccati.

Uso:  LLM_PRINCIPALE=models/gemini-3.5-flash-lite python scripts/estrai_modalita_uso.py --pilot
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from typing import Literal

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

from dotenv import load_dotenv

load_dotenv(os.path.join(RADICE, ".env"))

import chromadb
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from core.testo_prodotto import nome_prodotto

MODELLO = os.getenv("LLM_PRINCIPALE", "models/gemini-3.5-flash-lite")
CARTELLA_OUT = os.path.join(RADICE, "data", "attributi")
FILE_OUT = os.path.join(CARTELLA_OUT, "modalita_uso.jsonl")
DIMENSIONE_BATCH = 10
INTERVALLO_MIN_SEC = float(os.getenv("ESTRAI_INTERVALLO_SEC", "6"))

USI_INGREDIENTE = [
    "topping_finitura", "farcitura", "condimento_primi", "base_cottura", "impasto_panificazione",
    "aroma_spezia", "dolci_pasticceria", "composizione_sottoli", "accompagnamento_tagliere", "salsa_accompagnamento",
]


class UsoProdotto(BaseModel):
    id: str = Field(description="Id copiato esattamente dall'input.")
    modalita_uso: Literal["singolo", "ingrediente", "entrambi"]
    usi_come_ingrediente: list[Literal[
        "topping_finitura", "farcitura", "condimento_primi", "base_cottura", "impasto_panificazione",
        "aroma_spezia", "dolci_pasticceria", "composizione_sottoli", "accompagnamento_tagliere", "salsa_accompagnamento"
    ]] = Field(description="Vuota se modalita_uso='singolo'. Altrimenti gli impieghi reali come ingrediente.")
    motivo: str = Field(description="Max 15 parole: perche' questa modalita'.")


class BatchUso(BaseModel):
    prodotti: list[UsoProdotto]


PROMPT = f"""Sei un esperto di prodotti food per il canale HORECA. Per ogni prodotto di un catalogo B2B stabilisci
se un consulente puo' proporlo come PRODOTTO SINGOLO (da servire/consumare/vendere cosi' com'e'), solo come
INGREDIENTE (entra in preparazioni o finiture ma non si serve da solo), o ENTRAMBI.

Definizioni:
- "singolo": si serve o si consuma da solo come portata, snack, antipasto, referenza di tagliere, dolce, bevanda:
  salumi, formaggi, olive, paste e primi pronti, gelati, taralli, patatine, conserve di pesce, sottoli...
- "ingrediente": non ha senso proporlo da solo; serve per preparare o rifinire: petali, cristalli, polveri e scaglie
  aromatiche, spezie, aromi, farine e lieviti, granelle, basi, fondi, salse da cucina, sale aromatizzato, olio di semi...
- "entrambi": ha una vita da solo E una come ingrediente.

Esempi dati dal cliente (seguili):
- Petali di tartufo -> ingrediente (usi: topping_finitura). Mai come prodotto singolo.
- Anacardi tostati al tartufo -> entrambi (snack in ciotola; tritati come topping_finitura).
- Carciofi sott'olio -> entrambi (singoli come sottolio; composizione_sottoli).
- Burro -> entrambi (spalmato sul pane; base_cottura). Prosciutto cotto -> entrambi (tagliere; farcitura).
- Farina, lievito, pepe, origano, sale fino -> ingrediente.
- Taralli, olive in salamoia, formaggio stagionato da tavola, pasta secca, gelato, vino -> singolo
  (formaggi e salumi possono avere anche un uso come ingrediente: indicalo con "entrambi" solo se e' davvero comune).

Impieghi come ingrediente (lista chiusa): {', '.join(USI_INGREDIENTE)}.
- topping_finitura: sopra al piatto a fine cottura (granelle, petali, scaglie, polveri, olio aromatico a filo)
- farcitura: dentro/sopra pizza, panino, piadina, ripieni
- condimento_primi: sughi, creme e condimenti per pasta/riso
- base_cottura: per cucinare (oli, burro, panna, soffritti, passate, pelati)
- impasto_panificazione: farine, lieviti, basi per pizza
- aroma_spezia: aromatizzare (spezie, erbe, sale aromatizzato)
- dolci_pasticceria: dolci, creme, gelateria
- composizione_sottoli: dentro un misto/antipasto di sottoli e giardiniere
- accompagnamento_tagliere: affianca salumi e formaggi (mostarde, confetture, miele, frutta secca)
- salsa_accompagnamento: salse/maionesi/senape per carne, panini, fritti
Decidi in base a nome, categoria e scheda. Copia esattamente l'id. Un elemento per prodotto."""


def carica_prodotti():
    col = chromadb.PersistentClient(path=os.path.join(RADICE, "database_vettoriale")).get_collection("catalogo_v2")
    d = col.get(include=["metadatas", "documents"])
    return [{"id": i, "meta": m, "doc": x} for i, m, x in zip(d["ids"], d["metadatas"], d["documents"])]


def gia_fatti():
    fatti = set()
    for f in glob.glob(os.path.join(CARTELLA_OUT, "modalita_uso*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for r in fh:
                if r.strip():
                    try:
                        fatti.add(json.loads(r)["id"])
                    except ValueError:
                        pass
    return fatti


def scheda(p, max_char=420):
    doc = p["doc"].split("[DISCLAIMER LEGALE SOFOOD]")[0].replace("﻿", "").strip()
    m = p["meta"]
    return (f"id: {p['id']}\nnome: {nome_prodotto(p['doc'])}\n"
            f"categoria: {m.get('reparto')} / {m.get('sottocategoria')} / {m.get('tipo_prodotto')} | formato: {m.get('formato_valore')} {m.get('formato_unita')}\n"
            f"scheda: {doc[:max_char]}")


_ULTIMA = [0.0]


def chiama(client, batch):
    testo = "Classifica questi prodotti:\n\n" + "\n\n---\n\n".join(scheda(p) for p in batch)
    err = None
    for t in range(3):
        attesa = INTERVALLO_MIN_SEC - (time.time() - _ULTIMA[0])
        if attesa > 0:
            time.sleep(attesa)
        _ULTIMA[0] = time.time()
        try:
            r = client.models.generate_content(
                model=MODELLO, contents=testo,
                config=types.GenerateContentConfig(system_instruction=PROMPT, temperature=0.0,
                                                   response_mime_type="application/json", response_schema=BatchUso))
            dati = json.loads(r.text)
            return dati["prodotti"] if isinstance(dati, dict) else dati
        except Exception as e:
            err = e
            time.sleep(20 * (t + 1))
    raise RuntimeError(f"batch fallito: {err}")


def pilot(prodotti):
    per = {p["id"]: p for p in prodotti}
    voluti = ["19010480_MAC.PETALI015", "19010480_MAC-UNIONE15", "19010480_APER.ANACARDI70", "19010074_TR0431",
              "19010406_RAFI001", "19010061_NGDC150", "19010106_GRAND1", "19010483_SD02", "19010448_MG-INS1",
              "19010843_FARINO4", "19010901_CAP00025", "19010480_SALE.TART100QUA", "19010014_PROSO", "19010045_FRANCHI19"]
    sel = [per[i] for i in voluti if i in per]
    import random
    random.seed(11)
    resto = [p for p in prodotti if p["id"] not in {x["id"] for x in sel}]
    return sel + random.sample(resto, 26)


def main():
    global FILE_OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--out", default=FILE_OUT)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    FILE_OUT = a.out
    os.makedirs(CARTELLA_OUT, exist_ok=True)
    prodotti = carica_prodotti()
    if a.pilot:
        prodotti = pilot(prodotti)
    fatti = gia_fatti() if not a.pilot else set()
    da_fare = [p for p in prodotti if p["id"] not in fatti]
    if a.limit:
        da_fare = da_fare[:a.limit]
    print(f"[INFO] {len(prodotti)} prodotti | gia' fatti {len(fatti)} | da fare {len(da_fare)} | modello {MODELLO}", flush=True)
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    scritti, mancanti = 0, []
    with open(FILE_OUT, "a", encoding="utf-8") as out:
        for k in range(0, len(da_fare), DIMENSIONE_BATCH):
            batch = da_fare[k:k + DIMENSIONE_BATCH]
            try:
                ris = chiama(client, batch)
            except RuntimeError as e:
                print(f"[ERRORE] {e}", flush=True)
                mancanti += [p["id"] for p in batch]
                continue
            per_id = {r["id"]: r for r in ris}
            for p in batch:
                r = per_id.get(p["id"])
                if r is None:
                    mancanti.append(p["id"])
                    continue
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
                scritti += 1
            out.flush()
            print(f"[OK] {min(k + DIMENSIONE_BATCH, len(da_fare))}/{len(da_fare)}", flush=True)
    print(f"[FINE] scritti {scritti} | mancanti {len(mancanti)}", flush=True)


if __name__ == "__main__":
    main()
