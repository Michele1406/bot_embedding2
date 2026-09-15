import os
import sys
import argparse
from pathlib import Path
import chromadb
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODELLO_EMBEDDING = "models/gemini-embedding-2"
PERCORSO_DB = "./database_vettoriale"
NOME_COLLEZIONE = "catalogo_sofood"
ESTENSIONI_SUPPORTATE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def calcola_embedding(client, testo: str, img_path: Path):
    contents = [testo]
    if img_path and img_path.exists():
        mime = ESTENSIONI_SUPPORTATE.get(img_path.suffix.lower())
        if mime:
            contents.append(types.Part.from_bytes(data=img_path.read_bytes(), mime_type=mime))
    
    resp = client.models.embed_content(
        model=MODELLO_EMBEDDING,
        contents=contents,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    v = resp.embeddings[0]
    return v.values if hasattr(v, "values") else list(v)


def aggiorna_immagine_prodotto(codice_prodotto: str, percorso_immagine: str = None):
    """Aggiorna l'immagine e l'embedding di un singolo prodotto in ChromaDB."""
    codice_upper = codice_prodotto.strip().upper()
    client_db = chromadb.PersistentClient(path=PERCORSO_DB)
    collezione = client_db.get_collection(NOME_COLLEZIONE)

    print(f"[INFO] Ricerca prodotto con codice: {codice_upper}...")
    res = collezione.get(where={"codice_prodotto": codice_upper}, include=["metadatas", "documents"])

    if not res["ids"]:
        res = collezione.get(include=["metadatas", "documents"])
        ids_trovati = [i for i, m in zip(res["ids"], res["metadatas"]) if str(m.get("codice_prodotto", "")).upper() == codice_upper]
        if not ids_trovati:
            print(f"[ERRORE] Nessun prodotto trovato con codice '{codice_upper}'.")
            return
        res = collezione.get(ids=ids_trovati, include=["metadatas", "documents"])

    doc_id = res["ids"][0]
    metadata = res["metadatas"][0]
    documento = res["documents"][0]

    print(f"[TROVATO] ID: {doc_id} | Nome: {documento.splitlines()[0]}")
    vecchio_percorso = metadata.get("percorso_immagine", "Nessuno")
    print(f"[STATO ATTUALE] Immagine: {vecchio_percorso}")

    if percorso_immagine:
        nuovo_path = Path(percorso_immagine).resolve()
    else:
        cartella_locale = metadata.get("percorso_cartella_locale")
        if cartella_locale and os.path.exists(cartella_locale):
            candidati = list(Path(cartella_locale).glob(f"{codice_upper}.*")) or list(Path(cartella_locale).glob("*.jpg"))
            if candidati:
                nuovo_path = candidati[0].resolve()
            else:
                print(f"[ERRORE] Nessun file immagine trovato nella cartella {cartella_locale}.")
                return
        else:
            print("[ERRORE] Specifica il percorso del nuovo file con --file 'C:\\...\\foto.jpg'")
            return

    if not nuovo_path.exists():
        print(f"[ERRORE] Il file '{nuovo_path}' non esiste sul disco!")
        return

    print(f"[AGGIORNAMENTO] Nuovo file immagine: {nuovo_path}")
    metadata["percorso_immagine"] = str(nuovo_path)
    metadata["ha_immagine_primaria"] = True

    print("[CALCOLO] Ricalcolo dell'embedding multimodale con gemini-embedding-2...")
    client_genai = genai.Client(api_key=GEMINI_API_KEY)
    nuovo_vettore = calcola_embedding(client_genai, documento, nuovo_path)

    collezione.update(
        ids=[doc_id],
        embeddings=[nuovo_vettore],
        metadatas=[metadata],
    )
    print(f"[SUCCESSO] Prodotto {codice_upper} aggiornato con successo nel database vettoriale!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggiorna immagine ed embedding di un singolo prodotto in ChromaDB.")
    parser.add_argument("codice", help="Codice prodotto (es. LPSFIESP, CAP00001)")
    parser.add_argument("--file", default=None, help="Percorso opzionale del nuovo file immagine (.jpg, .png)")
    args = parser.parse_args()

    aggiorna_immagine_prodotto(args.codice, args.file)
