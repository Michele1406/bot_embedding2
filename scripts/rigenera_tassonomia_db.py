import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rigenera_tassonomia_db.py
==========================
Applica la nuova tassonomia ECR (Tassonomia.xlsx) ai metadati dei prodotti
già presenti in ChromaDB, SENZA richiamare alcuna API esterna (Gemini o
altro): la classificazione è già stata fatta dall'Excel, qui la si scrive
nei metadati. Aggiorna:

  - reparto            <- "Reparto" (unificato con "Categoria Prodotto",
                           vedi nota nel resoconto: sono lo stesso campo ora)
  - categoria_prodotto  <- "Reparto" (stesso valore, per compatibilità con
                           il codice legacy che legge ancora questo campo)
  - categoria_tassonomia<- "Categoria Tassonomia"
  - sottocategoria      <- "Sottocategoria"
  - specifiche_liv4     <- "Specifiche (Liv 4)"          [CAMPO NUOVO]
  - formato_variante_liv5 <- "Formato/Variante (Liv 5)"  [CAMPO NUOVO]
  - canale_probabile    <- calcolato da formato_variante_liv5 con
                            profilazione_locale.rileva_canale_da_formato_liv5
                            ("horeca" | "retail" | "misto"), PRECALCOLATO qui
                            una volta sola invece che ad ogni query.

Il match tra riga Excel e record ChromaDB avviene sull'id
"{codice_fornitore}_{codice_prodotto}" (stessa convenzione usata da
caricaprodotti_v2.py). Un prodotto a catalogo ma assente dall'Excel (o
viceversa) viene segnalato in un report finale e NON viene toccato/perso: va
verificato a mano una tantum.

Uso:
    python rigenera_tassonomia_db.py                 # applica le modifiche
    python rigenera_tassonomia_db.py --dry-run        # simula, non scrive nulla
    python rigenera_tassonomia_db.py --file Altro.xlsx --db ./database_vettoriale
"""

import argparse
from pathlib import Path
import pandas as pd
import chromadb

from core.profilazione_locale import rileva_canale_da_formato_liv5

NOME_COLLEZIONE = "catalogo_sofood"


def _pulisci(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nd", "n.d.", "n/a") else s


def _normalizza_tassonomia(v) -> str:
    """Normalizza un valore di tassonomia (Reparto/Categoria/Sottocategoria):
    spazi multipli collassati, maiuscolo uniforme, trim. Il file Excel ha
    alcune incoerenze di battitura ereditate dalla classificazione LLM
    originaria (es. 'PROSC  CRUDO' con doppio spazio, 'SALame' maiuscole
    miste): normalizzando qui, una volta sola, evitiamo che ogni filtro nel
    resto del codice debba gestire queste varianti a mano, e i filtri esatti
    di ChromaDB (case/whitespace-sensitive) continuano a funzionare."""
    s = _pulisci(v)
    if not s:
        return s
    s = " ".join(s.split())  # collassa spazi multipli
    return s.upper()


def rigenera_tassonomia(file_excel: str = "Tassonomia.xlsx", percorso_db: str = "./database_vettoriale",
                         dry_run: bool = False) -> dict:
    print("=" * 70)
    print("MIGRAZIONE TASSONOMIA ECR -> CHROMADB")
    print("=" * 70)

    if not Path(file_excel).exists():
        print(f"[ERRORE] File '{file_excel}' non trovato.")
        return {}

    print(f"[1/4] Lettura '{file_excel}'...")
    df = pd.read_excel(file_excel, dtype=str)
    df = df.fillna("")
    richieste = ["Codice Fornitore", "Codice Prodotto", "Reparto", "Categoria Tassonomia", "Sottocategoria"]
    mancanti = [c for c in richieste if c not in df.columns]
    if mancanti:
        print(f"[ERRORE] Colonne mancanti nel file: {mancanti}")
        return {}

    df["id_calcolato"] = df["Codice Fornitore"].str.strip() + "_" + df["Codice Prodotto"].str.strip()
    righe_per_id = {r["id_calcolato"]: r for _, r in df.iterrows()}
    print(f"[OK] {len(righe_per_id)} righe prodotto lette dall'Excel.")

    print(f"[2/4] Connessione a ChromaDB ({percorso_db})...")
    client = chromadb.PersistentClient(path=percorso_db)
    collezione = client.get_collection(NOME_COLLEZIONE)
    dati = collezione.get(include=["metadatas"])
    ids_db = dati["ids"]
    metas_db = dati["metadatas"]
    print(f"[OK] {len(ids_db)} record letti da ChromaDB (inclusi fornitori/logistica).")

    print("[3/4] Calcolo differenze e preparazione aggiornamenti...")
    ids_aggiornare, metas_aggiornare = [], []
    non_in_excel, non_in_db = [], []

    id_db_set = set(ids_db)
    for id_excel in righe_per_id:
        if id_excel not in id_db_set:
            non_in_db.append(id_excel)

    for doc_id, meta_attuale in zip(ids_db, metas_db):
        riga = righe_per_id.get(doc_id)
        if riga is None:
            # Non tutti i record ChromaDB sono prodotti (schede fornitore,
            # calendario, consegne): li ignoriamo silenziosamente se non
            # hanno un codice_prodotto (non sono nell'Excel per definizione).
            if meta_attuale.get("codice_prodotto"):
                non_in_excel.append(doc_id)
            continue

        reparto_nuovo = _normalizza_tassonomia(riga["Reparto"])
        formato_liv5 = _pulisci(riga.get("Formato/Variante (Liv 5)", ""))

        nuovo_meta = dict(meta_attuale)
        nuovo_meta["reparto"] = reparto_nuovo
        nuovo_meta["categoria_prodotto"] = reparto_nuovo.capitalize()
        nuovo_meta["categoria_tassonomia"] = _normalizza_tassonomia(riga["Categoria Tassonomia"])
        nuovo_meta["sottocategoria"] = _normalizza_tassonomia(riga["Sottocategoria"])
        nuovo_meta["specifiche_liv4"] = _pulisci(riga.get("Specifiche (Liv 4)", ""))
        nuovo_meta["formato_variante_liv5"] = formato_liv5
        nuovo_meta["canale_probabile"] = rileva_canale_da_formato_liv5(formato_liv5)

        if nuovo_meta != meta_attuale:
            ids_aggiornare.append(doc_id)
            metas_aggiornare.append(nuovo_meta)

    print(f"[OK] Record da aggiornare: {len(ids_aggiornare)} / {len(righe_per_id)}")
    if non_in_db:
        print(f"[AVVISO] {len(non_in_db)} codici presenti nell'Excel ma assenti da ChromaDB (prodotti non ancora indicizzati): {non_in_db[:10]}{' ...' if len(non_in_db) > 10 else ''}")
    if non_in_excel:
        print(f"[AVVISO] {len(non_in_excel)} prodotti presenti in ChromaDB ma assenti dal nuovo Excel (verificare se dismessi): {non_in_excel[:10]}{' ...' if len(non_in_excel) > 10 else ''}")

    if dry_run:
        print("\n[DRY-RUN] Nessuna scrittura effettuata (--dry-run attivo). Esempio di una modifica:")
        if ids_aggiornare:
            print(f"  ID: {ids_aggiornare[0]}")
            for k in ("reparto", "categoria_tassonomia", "sottocategoria", "specifiche_liv4", "formato_variante_liv5", "canale_probabile"):
                print(f"    {k}: {metas_aggiornare[0].get(k)!r}")
        return {"da_aggiornare": len(ids_aggiornare), "non_in_db": len(non_in_db), "non_in_excel": len(non_in_excel)}

    print("[4/4] Scrittura aggiornamenti su ChromaDB (a blocchi da 200)...")
    BLOCCO = 200
    for i in range(0, len(ids_aggiornare), BLOCCO):
        collezione.update(ids=ids_aggiornare[i:i + BLOCCO], metadatas=metas_aggiornare[i:i + BLOCCO])
        print(f"  ... aggiornati {min(i + BLOCCO, len(ids_aggiornare))}/{len(ids_aggiornare)}")

    print("\n" + "=" * 70)
    print(f"[SUCCESSO] Tassonomia ECR applicata a {len(ids_aggiornare)} prodotti.")
    print("=" * 70)
    return {"aggiornati": len(ids_aggiornare), "non_in_db": len(non_in_db), "non_in_excel": len(non_in_excel)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Applica Tassonomia.xlsx (ECR) ai metadati ChromaDB")
    parser.add_argument("--file", default="Tassonomia.xlsx", help="Percorso del file Tassonomia.xlsx")
    parser.add_argument("--db", default="./database_vettoriale", help="Percorso del database vettoriale")
    parser.add_argument("--dry-run", action="store_true", help="Simula senza scrivere nulla")
    args = parser.parse_args()
    rigenera_tassonomia(args.file, args.db, args.dry_run)
