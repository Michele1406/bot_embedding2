#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script per aggiornare gli elementi del fornitore San Salvatore (19010926) in ChromaDB:
1. Rimuove i vecchi elementi con codici errati (CRM-*, YOG-*).
2. Indicizza e calcola gli embedding per i 43 nuovi prodotti con i codici corretti (PVAZ*).
3. Assegna la corretta tassonomia a ciascun prodotto:
   - PVAZ01*: FORMAGGI > Latticini > Yogurt
   - PVAZ02*: FORMAGGI > Latticini > Dessert e cremosi di latte
   - PVAZ03*: FORMAGGI > Formaggi freschi > Mozzarella di bufala
4. Aggiorna il file Excel Tassonomia_Prodotti_SoFood.xlsx.
"""

import os
import re
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
import chromadb
from google import genai
from google.genai import types

# Assicura output UTF-8
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CARTELLA_BASE = Path(__file__).resolve().parent
load_dotenv(CARTELLA_BASE / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY mancante nel file .env")

MODELLO_EMBEDDING = "models/gemini-embedding-2"
PERCORSO_DB = CARTELLA_BASE / "database_vettoriale"
NOME_COLLEZIONE = "catalogo_sofood"
CARTELLA_SAN_SALVATORE = Path(r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD\19010926")
NOME_FORNITORE = "San Salvatore | Azienda Agricola San Salvatore 1988"
CODICE_FORNITORE = "19010926"


class Embedder:
    def __init__(self, api_key: str, model_name: str):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def embed_documento(self, testo: str) -> list:
        for tentativo in range(1, 5):
            try:
                res = self.client.models.embed_content(
                    model=self.model_name,
                    contents=[testo],
                    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
                )
                v = res.embeddings[0]
                return v.values if hasattr(v, "values") else list(v)
            except Exception as e:
                attesa = 2.0 * tentativo
                print(f"      [ATTENZIONE] Tentativo {tentativo} fallito ({e}), attendo {attesa}s...")
                time.sleep(attesa)
        raise RuntimeError(f"Embedding fallito dopo tentativi multipli per il testo.")


def determina_tassonomia(codice_prod: str, testo: str):
    """Determina Reparto, Categoria e Sottocategoria in base al codice PVAZ."""
    cod = codice_prod.upper()
    if cod.startswith("PVAZ01"):
        return "FORMAGGI", "Latticini", "Yogurt"
    elif cod.startswith("PVAZ02"):
        return "FORMAGGI", "Latticini", "Dessert e cremosi di latte"
    elif cod.startswith("PVAZ03"):
        return "FORMAGGI", "Formaggi freschi", "Mozzarella di bufala"
    else:
        # Fallback basato su parole chiave
        t_low = testo.lower()
        if "yogurt" in t_low:
            return "FORMAGGI", "Latticini", "Yogurt"
        elif "cremoso" in t_low or "dessert" in t_low:
            return "FORMAGGI", "Latticini", "Dessert e cremosi di latte"
        else:
            return "FORMAGGI", "Formaggi freschi", "Mozzarella di bufala"


def aggiorna_san_salvatore():
    print("=" * 70)
    print("AGGIORNAMENTO PRODOTTI SAN SALVATORE (19010926) IN CHROMADB")
    print("=" * 70)

    print(f"[1/4] Connessione a ChromaDB ({PERCORSO_DB})...")
    client_chroma = chromadb.PersistentClient(path=str(PERCORSO_DB))
    collezione = client_chroma.get_collection(NOME_COLLEZIONE)
    totale_prima = collezione.count()
    print(f"[OK] Record totali nel database prima dell'operazione: {totale_prima}")

    # Ricerca vecchi elementi San Salvatore
    dati = collezione.get(include=["metadatas"])
    vecchi_ids = []
    for doc_id, meta in zip(dati["ids"], dati["metadatas"]):
        cf = str(meta.get("codice_fornitore", "")).strip()
        nf = str(meta.get("nome_fornitore", "")).strip().lower()
        if cf == CODICE_FORNITORE or "salvatore" in nf:
            # Se ha i vecchi codici (es. CRM-, YOG-) o non è un PVAZ
            cod_p = str(meta.get("codice_prodotto", "")).strip()
            if not cod_p.startswith("PVAZ"):
                vecchi_ids.append(doc_id)

    print(f"[2/4] Eliminazione vecchi elementi errati trovati: {len(vecchi_ids)}")
    for vid in vecchi_ids:
        print(f"  - Eliminazione record errato: {vid}")
    if vecchi_ids:
        collezione.delete(ids=vecchi_ids)
        print(f"[OK] Eliminati {len(vecchi_ids)} record obsoleti con successo.")
    else:
        print("[INFO] Nessun vecchio record errato da eliminare.")

    # Scansione dei 43 prodotti da disco
    print(f"\n[3/4] Indicizzazione nuovi prodotti da '{CARTELLA_SAN_SALVATORE}'...")
    if not CARTELLA_SAN_SALVATORE.exists():
        print(f"[ERRORE] La cartella {CARTELLA_SAN_SALVATORE} non esiste!")
        return False

    subdirs = sorted([d for d in CARTELLA_SAN_SALVATORE.iterdir() if d.is_dir()])
    print(f"[INFO] Trovate {len(subdirs)} cartelle prodotto da processare.")

    embedder = Embedder(api_key=GEMINI_API_KEY, model_name=MODELLO_EMBEDDING)
    inseriti = 0

    for cartella_prod in subdirs:
        codice_prod = cartella_prod.name.strip().upper()
        id_record = f"{CODICE_FORNITORE}_{codice_prod}"

        txt_files = list(cartella_prod.glob("*.txt"))
        if not txt_files:
            print(f"  [ATTENZIONE] Nessun file .txt in {cartella_prod.name}, salto.")
            continue

        txt_path = txt_files[0]
        testo = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        if not testo:
            print(f"  [ATTENZIONE] File .txt vuoto in {cartella_prod.name}, salto.")
            continue

        reparto, cat_tass, sottocategoria = determina_tassonomia(codice_prod, testo)

        # Rileva se senza lattosio dal testo
        t_low = testo.lower()
        senza_lattosio = "SI" if ("senza lattosio" in t_low or "<0,01g" in t_low) else "NO"

        meta = {
            "codice_prodotto": codice_prod,
            "codice_fornitore": CODICE_FORNITORE,
            "nome_fornitore": NOME_FORNITORE,
            "categoria_prodotto": "Formaggi",
            "reparto": reparto,
            "categoria_tassonomia": cat_tass,
            "sottocategoria": sottocategoria,
            "varianti_prodotto": "Nessuna variante",
            "allergeni": "Latte",
            "tracce_di": "Nessuna",
            "biologico": "NO",
            "vegano": "NO",
            "vegetariano": "SI",
            "senza_glutine": "SI*",
            "senza_lattosio": senza_lattosio,
            "milk_free": "NO",
            "kosher": "NO",
            "ha_immagine_primaria": False,
            "percorso_immagine": "",
            "ha_pdf_tecnico": False,
            "percorso_cartella_locale": str(cartella_prod.resolve()),
        }

        print(f"  [{inseriti+1}/{len(subdirs)}] Embed e Upsert: {id_record} -> {reparto} > {cat_tass} > {sottocategoria}")
        vettore = embedder.embed_documento(testo)

        collezione.upsert(
            ids=[id_record],
            embeddings=[vettore],
            documents=[testo],
            metadatas=[meta]
        )
        inseriti += 1
        time.sleep(0.3)  # Rispetta i limiti API senza attese eccessive

    totale_dopo = collezione.count()
    print("\n" + "=" * 70)
    print(f"[OK] Inseriti {inseriti} nuovi prodotti San Salvatore in ChromaDB!")
    print(f"Record totali nel database: {totale_prima} -> {totale_dopo} (+{totale_dopo - totale_prima})")
    print("=" * 70)

    # Aggiornamento dell'Excel
    print("\n[4/4] Rigenerazione del file Excel con la tassonomia aggiornata...")
    from esporta_tassonomia_excel import esporta_tassonomia_excel
    esporta_tassonomia_excel()

    print("\n[FINE] Operazione completata con successo!")
    return True


if __name__ == "__main__":
    aggiorna_san_salvatore()
