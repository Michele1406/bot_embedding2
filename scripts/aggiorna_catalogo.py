# -*- coding: utf-8 -*-
"""
aggiorna_catalogo.py
====================
Un solo comando per portare nuovi prodotti/ricette fino al motore di ricerca (Fase 6 del piano):

  1. estrai_attributi.py       classifica (LLM) i prodotti nuovi: tipo, ruoli d'uso, origine, cottura
  2. estrai_modalita_uso.py    singolo / solo ingrediente / entrambi (LLM)
  3. costruisci_catalogo_v2.py ricostruisce catalogo_v2 (attributi + parser formati + regole derivate)
  3b. carica_abstract_fornitori_v2.py  ricarica le schede aziende da sofood/ABSTRACT.xlsx (incrementale)
  4. aggiorna_ricettario.py    ricostruisce ricette_v2 e l'audit di copertura
  5. test di regressione       python -m unittest (solo se --test)

Prerequisito: i nuovi prodotti devono essere gia' in `catalogo_sofood` (scripts/caricaprodotti_v2.py).
I passi 1 e 2 sono incrementali: processano solo i prodotti non ancora presenti in data/attributi/.
Costo: ~1 chiamata LLM ogni 10 prodotti nuovi per ciascuno dei passi 1 e 2 (+ pochi embedding per le ricette
nuove). Modello di default: gemini-3.5-flash-lite (<=10 chiamate/minuto). Con --stima non si chiama nulla.

Uso:
    python scripts/aggiorna_catalogo.py --stima
    python scripts/aggiorna_catalogo.py [--test]
"""
import argparse
import glob
import json
import os
import subprocess
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
MODELLO = os.getenv("LLM_PRINCIPALE", "models/gemini-3.5-flash-lite")


def id_prodotti_catalogo():
    import chromadb
    col = chromadb.PersistentClient(path=os.path.join(RADICE, "database_vettoriale")).get_collection("catalogo_sofood")
    d = col.get(include=["metadatas"])
    non_prodotto = {"AZIENDALE", "LOGISTICA"}
    categorie_doc = {"SCHEDA AZIENDALE FORNITORE", "INFO LOGISTICA E ORDINI", "REGOLE DI CONSEGNA ZONALE",
                     "RICETTARIO E REGOLE", "SCHEDA FORNITORE", "CONSEGNE E SPEDIZIONI"}
    return {i for i, m in zip(d["ids"], d["metadatas"])
            if str(m.get("reparto") or "").upper() not in non_prodotto and m.get("reparto")
            and str(m.get("categoria_prodotto") or "").upper() not in categorie_doc}


def id_gia_classificati(prefisso):
    fatti = set()
    for f in glob.glob(os.path.join(RADICE, "data", "attributi", prefisso + "*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for r in fh:
                if r.strip():
                    try:
                        fatti.add(json.loads(r)["id"])
                    except ValueError:
                        pass
    return fatti


def esegui(cmd, env_extra=None):
    print(f"\n$ {' '.join(os.path.basename(c) if os.path.sep in c else c for c in cmd)}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **(env_extra or {}))
    r = subprocess.run(cmd, cwd=RADICE, env=env)
    if r.returncode != 0:
        sys.exit(f"[ERRORE] passo fallito ({r.returncode}): {cmd[1] if len(cmd) > 1 else cmd}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stima", action="store_true", help="mostra solo cosa verrebbe fatto, senza chiamate")
    ap.add_argument("--test", action="store_true", help="esegue i test di regressione alla fine")
    a = ap.parse_args()

    prodotti = id_prodotti_catalogo()
    nuovi_attr = prodotti - id_gia_classificati("attributi")
    nuovi_uso = prodotti - id_gia_classificati("modalita_uso")
    print(f"Prodotti nel catalogo: {len(prodotti)}")
    print(f"  senza attributi:     {len(nuovi_attr)}  -> ~{-(-len(nuovi_attr) // 10)} chiamate LLM")
    print(f"  senza modalita' uso: {len(nuovi_uso)}  -> ~{-(-len(nuovi_uso) // 10)} chiamate LLM")
    if a.stima:
        return

    env = {"LLM_PRINCIPALE": MODELLO}
    if nuovi_attr:
        esegui([PY, "scripts/estrai_attributi.py", "--out", os.path.join(RADICE, "data", "attributi", "attributi_aggiornamento.jsonl")], env)
    if nuovi_uso:
        esegui([PY, "scripts/estrai_modalita_uso.py", "--out", os.path.join(RADICE, "data", "attributi", "modalita_uso_aggiornamento.jsonl")], env)
    esegui([PY, "scripts/costruisci_catalogo_v2.py", "--ricrea"])
    # --ricrea svuota aziende_sofood e la ricopia dal catalogo legacy: le schede aggiunte solo in sofood/ABSTRACT.xlsx
    # (es. Masciarelli, La Valletta) si ricaricano qui (incrementale: solo i fornitori mancanti, pochi embedding)
    esegui([PY, "scripts/carica_abstract_fornitori_v2.py"])
    esegui([PY, "scripts/aggiorna_ricettario.py", "--ricrea"])
    if a.test:
        env_t = {"CATALOGO_COLLECTION": "catalogo_v2"}
        esegui([PY, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_*.py"], env_t)
    print("\n[FINE] catalogo_v2 e ricette_v2 aggiornati. Per usarli: CATALOGO_COLLECTION=catalogo_v2 RICETTE_COLLECTION=ricette_v2")


if __name__ == "__main__":
    main()
