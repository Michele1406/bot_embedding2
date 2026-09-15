import os
import re
import json
import time
import argparse
from pathlib import Path
import chromadb
from google import genai
from google.genai import types
from pydantic import BaseModel
from dotenv import load_dotenv

from tassonomia_sofood import TASSONOMIA_LISTA, MAPPA_SOTTOCATEGORIA, SOTTOCATEGORIE_NOMI

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY non trovata nel file .env")

MODELLO_EMBEDDING = "models/gemini-embedding-2"
MODELLO_CLASSIFICAZIONE = "models/gemini-3.5-flash-lite"
PERCORSO_DB = "./database_vettoriale"
NOME_COLLEZIONE = "catalogo_sofood"
CARTELLA_PRODOTTI = Path(r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD")
FILE_PROGRESSO = Path("progresso_tassonomia.json")

# Modello Pydantic per la classificazione batch
class AssegnazioneTassonomia(BaseModel):
    id_prodotto: str
    sottocategoria: str

class BatchClassificazione(BaseModel):
    classificazioni: list[AssegnazioneTassonomia]


class GestoreCatalogo:
    def __init__(self):
        self.client_chroma = chromadb.PersistentClient(path=PERCORSO_DB)
        self.collezione = self.client_chroma.get_or_create_collection(name=NOME_COLLEZIONE)
        self.client_genai = genai.Client(api_key=GEMINI_API_KEY)
        self.fornitori_dict = self._carica_fornitori()

    def _carica_fornitori(self) -> dict:
        csv_p = CARTELLA_PRODOTTI / "fornitori.csv"
        f_map = {}
        if csv_p.exists():
            import pandas as pd
            df = pd.read_csv(csv_p, dtype=str)
            for _, r in df.iterrows():
                f_map[str(r["an_forn"]).strip()] = str(r["nome_azienda"]).strip()
        return f_map

    def embed_documento(self, testo: str, img_path: Path = None) -> list:
        contents = [testo]
        if img_path and img_path.exists():
            ext = img_path.suffix.lower()
            mime = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"
            try:
                contents.append(types.Part.from_bytes(data=img_path.read_bytes(), mime_type=mime))
            except Exception as e:
                print(f"      [WARN] Impossibile leggere immagine {img_path.name}: {e}")

        for tentat in range(4):
            try:
                time.sleep(0.7)  # Garantisce max 80 RPM, rigorosamente sotto il limite di 100 RPM
                res = self.client_genai.models.embed_content(
                    model=MODELLO_EMBEDDING,
                    contents=contents,
                    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
                )
                v = res.embeddings[0]
                return v.values if hasattr(v, "values") else list(v)
            except Exception as e:
                if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and tentat < 3:
                    time.sleep(3 * (tentat + 1))
                    continue
                raise e

    def indicizza_nuovi_prodotti(self, cartelle_target: list[str] = None, max_nuovi: int = None):
        """Scansiona la cartella PRODOTTI SOFOOD (in sola lettura) e inserisce in ChromaDB solo i prodotti mancanti."""
        print("\n" + "="*70)
        print("FASE 1: VERIFICA E INDICIZZAZIONE NUOVI PRODOTTI IN CHROMADB")
        print("="*70)

        tutti_ids_esistenti = set(self.collezione.get()["ids"])
        print(f"[INFO] Prodotti attualmente presenti in ChromaDB: {len(tutti_ids_esistenti)}")

        cartelle_da_scansionare = []
        for d in CARTELLA_PRODOTTI.iterdir():
            if d.is_dir() and d.name.startswith("19010"):
                if cartelle_target:
                    if d.name in cartelle_target:
                        cartelle_da_scansionare.append(d)
                else:
                    cartelle_da_scansionare.append(d)

        nuovi_trovati = 0
        inseriti = 0

        for cartella_forn in sorted(cartelle_da_scansionare):
            if max_nuovi is not None and inseriti >= max_nuovi:
                break
            an_forn = cartella_forn.name
            nome_forn = self.fornitori_dict.get(an_forn, f"Fornitore {an_forn}")

            for cartella_prod in cartella_forn.iterdir():
                if max_nuovi is not None and inseriti >= max_nuovi:
                    break
                if not cartella_prod.is_dir():
                    continue

                codice_prod = cartella_prod.name.strip().upper()
                id_record = f"{an_forn}_{codice_prod}"

                if id_record in tutti_ids_esistenti:
                    continue

                nuovi_trovati += 1
                txt_file = cartella_prod / f"{cartella_prod.name}.txt"
                jpg_file = cartella_prod / f"{cartella_prod.name}.jpg"

                if not txt_file.exists():
                    # Prova case insensitive
                    txts = list(cartella_prod.glob("*.txt"))
                    if txts:
                        txt_file = txts[0]
                    else:
                        continue

                try:
                    testo = txt_file.read_text(encoding="utf-8", errors="replace").strip()
                except Exception:
                    continue

                if not testo:
                    continue

                ha_img = jpg_file.exists()
                print(f"  [NUOVO] Inserimento {id_record} ({nome_forn}) - Foto: {ha_img}...")

                try:
                    vettore = self.embed_documento(testo, jpg_file if ha_img else None)
                    meta = {
                        "codice_prodotto": codice_prod,
                        "codice_fornitore": an_forn,
                        "nome_fornitore": nome_forn,
                        "categoria_prodotto": "Mare" if an_forn == "19010925" else ("Formaggi" if an_forn == "19010926" else "Dispensa"),
                        "varianti_prodotto": "Nessuna variante",
                        "allergeni": "Non specificato",
                        "tracce_di": "Nessuna",
                        "biologico": "NO",
                        "vegano": "NO",
                        "vegetariano": "SI" if an_forn == "19010926" else "NO",
                        "senza_glutine": "NO",
                        "senza_lattosio": "NO",
                        "milk_free": "NO",
                        "kosher": "NO",
                        "ha_immagine_primaria": ha_img,
                        "percorso_immagine": str(jpg_file.resolve()) if ha_img else "",
                        "ha_pdf_tecnico": False,
                        "percorso_cartella_locale": str(cartella_prod.resolve()),
                        "sottocategoria": "Non assegnata",
                        "categoria_tassonomia": "Non assegnata",
                        "reparto": "Non assegnato",
                    }
                    self.collezione.upsert(
                        ids=[id_record],
                        embeddings=[vettore],
                        documents=[testo],
                        metadatas=[meta]
                    )
                    tutti_ids_esistenti.add(id_record)
                    inseriti += 1
                    time.sleep(0.2)
                except Exception as e:
                    print(f"    [ERRORE] Indicizzazione {id_record} fallita: {e}")

        print(f"\n[INFO] Nuovi prodotti trovati su disco: {nuovi_trovati} | Inseriti in ChromaDB: {inseriti}")
        print(f"[INFO] Totale prodotti ora in ChromaDB: {len(tutti_ids_esistenti)}")

    def classifica_tassonomia_batch(self, batch_limit: int = 50):
        """Assegna sottocategoria, categoria e reparto a tutti i prodotti usando gemini-3.5-flash-lite."""
        print("\n" + "="*70)
        print(f"FASE 2: ASSEGNAZIONE TASSONOMIA (BATCH DA {batch_limit} PRODOTTI)")
        print("="*70)

        # Recupera tutti i record di ChromaDB
        dati = self.collezione.get(include=["metadatas", "documents"])
        ids = dati["ids"]
        metas = dati["metadatas"]
        docs = dati["documents"]

        # Trova prodotti che non hanno ancora una sottocategoria valida
        da_classificare = []
        for pid, meta, doc in zip(ids, metas, docs):
            sc = (meta.get("sottocategoria") or "").strip()
            if not sc or sc == "Non assegnata" or sc.lower() not in MAPPA_SOTTOCATEGORIA:
                da_classificare.append({
                    "id": pid,
                    "metadata": meta,
                    "document": doc
                })

        totale_mancanti = len(da_classificare)
        print(f"[INFO] Prodotti totali da classificare: {totale_mancanti} su {len(ids)}")

        if totale_mancanti == 0:
            print("[OK] Tutti i prodotti hanno già i metadati tassonomici completi!")
            return

        limite_corrente = min(batch_limit, totale_mancanti) if batch_limit > 0 else totale_mancanti
        lotto = da_classificare[:limite_corrente]
        print(f"[INFO] Elaborazione di questo blocco: {len(lotto)} prodotti...\n")

        chunk_size = 20  # 20 prodotti per prompt: altissima efficienza e minimo numero di richieste
        elaborati_ora = 0

        for i in range(0, len(lotto), chunk_size):
            chunk = lotto[i:i+chunk_size]
            prompt_chunk = self._costruisci_prompt_classificazione(chunk)

            try:
                risposta = self._chiama_gemini_classificazione(prompt_chunk)
                for ass in risposta.classificazioni:
                    sc_lower = ass.sottocategoria.strip().lower()
                    info_tass = MAPPA_SOTTOCATEGORIA.get(sc_lower)
                    if not info_tass:
                        # Fallback fuzzy se il nome ha leggere variazioni
                        for k, v in MAPPA_SOTTOCATEGORIA.items():
                            if k in sc_lower or sc_lower in k:
                                info_tass = v
                                break

                    if info_tass:
                        rec = next((x for x in chunk if x["id"] == ass.id_prodotto), None)
                        if rec:
                            nuovi_meta = rec["metadata"].copy()
                            nuovi_meta["sottocategoria"] = info_tass["sottocategoria"]
                            nuovi_meta["categoria_tassonomia"] = info_tass["categoria"]
                            nuovi_meta["reparto"] = info_tass["reparto"]
                            self.collezione.update(
                                ids=[ass.id_prodotto],
                                metadatas=[nuovi_meta]
                            )
                            nome_prod = rec['document'].splitlines()[0] if rec.get('document') else ass.id_prodotto
                            print(f"  [OK] {ass.id_prodotto} -> {info_tass['reparto']} > {info_tass['categoria']} > {info_tass['sottocategoria']}", flush=True)
                            elaborati_ora += 1
                    else:
                        print(f"  [WARN] Sottocategoria '{ass.sottocategoria}' non riconosciuta per {ass.id_prodotto}", flush=True)

                # Pausa di 4.5s per garantire un rate massimo di 10-12 RPM (ben sotto il limite di 15 RPM)
                time.sleep(4.5)
            except Exception as e:
                print(f"  [ERRORE] Blocco {i}-{i+chunk_size} fallito: {e}", flush=True)
                time.sleep(6)

        print(f"\n[FINE BLOCCO] Prodotti classificati in questa sessione: {elaborati_ora}", flush=True)
        rimanenti = totale_mancanti - elaborati_ora
        print(f"[STATO] Prodotti ancora da classificare per i prossimi batch: {rimanenti}")

    def _costruisci_prompt_classificazione(self, chunk: list) -> str:
        prodotti_desc = []
        for item in chunk:
            doc_linee = item["document"].splitlines()[:5]
            testo_breve = " | ".join(l.strip() for l in doc_linee if l.strip())
            prodotti_desc.append(f"- ID: {item['id']}\n  TESTO: {testo_breve}")

        elenco_p = "\n\n".join(prodotti_desc)
        elenco_sottocategorie_str = "\n".join(f"- {s}" for s in SOTTOCATEGORIE_NOMI)

        prompt = f"""Sei il maestro classificatore merceologico B2B di So Food.
Assegna a ciascuno dei seguenti prodotti ESATTAMENTE UNA sottocategoria presa TASSATIVAMENTE da questo elenco chiuso ufficiale.

ELENCO UFFICIALE DELLE SOTTOCATEGORIE CONSENTITE:
{elenco_sottocategorie_str}

PRODOTTI DA CLASSIFICARE:
{elenco_p}

Regola: Rispondi esclusivamente in formato JSON conforme allo schema BatchClassificazione."""
        return prompt

    def _chiama_gemini_classificazione(self, prompt: str) -> BatchClassificazione:
        for tentat in range(4):
            try:
                res = self.client_genai.models.generate_content(
                    model=MODELLO_CLASSIFICAZIONE,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        response_mime_type="application/json",
                        response_schema=BatchClassificazione,
                    )
                )
                return BatchClassificazione.model_validate_json(res.text)
            except Exception as e:
                if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and tentat < 3:
                    tempo_attesa = 8 * (tentat + 1)
                    print(f"      [RATE LIMIT 429] Attesa prudenziale di {tempo_attesa}s...", flush=True)
                    time.sleep(tempo_attesa)
                    continue
                raise e


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arricchimento catalogo So Food")
    parser.add_argument("--batch-size", type=int, default=50, help="Numero di prodotti da classificare in questo blocco")
    parser.add_argument("--nuovi-soltanto", action="store_true", help="Indicizza solo i nuovi prodotti (Italfish, San Salvatore)")
    parser.add_argument("--nuovi-max", type=int, default=None, help="Massimo numero di nuovi prodotti da indicizzare in questa sessione")
    parser.add_argument("--target-fornitori", nargs="+", default=["19010925", "19010926"], help="Fornitori da scansionare per nuovi prodotti")
    args = parser.parse_args()

    gestore = GestoreCatalogo()
    # 1. Carica i prodotti mancanti dei nuovi fornitori
    gestore.indicizza_nuovi_prodotti(cartelle_target=args.target_fornitori, max_nuovi=args.nuovi_max)

    # 2. Se non richiesto solo nuovi, esegue il batch di classificazione
    if not args.nuovi_soltanto:
        gestore.classifica_tassonomia_batch(batch_limit=args.batch_size)
