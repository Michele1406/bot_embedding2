import os
import time
import pandas as pd
import chromadb
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("ATTENZIONE: API Key mancante nel file .env")

# ====================================================================
# CONFIGURAZIONE
# ====================================================================
# Modello di embedding: gemini-embedding-2 è GA (non preview) ed è nativamente
# multimodale (testo, immagini, audio, video, PDF) nello stesso spazio vettoriale.
MODELLO_EMBEDDING = "models/gemini-embedding-2"

# Percorso assoluto aggiornato
FILE_EXCEL_ABSTRACT = r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD\sofood\ABSTRACT.xlsx"
NOME_FOGLIO = "Abstract Database"
PERCORSO_DB = "./database_vettoriale"
NOME_COLLEZIONE = "catalogo_sofood"

# Pausa tra una chiamata embedContent e l'altra (rate limiting lato Google).
PAUSA_TRA_CHIAMATE_SEC = 2.0
TENTATIVI_MASSIMI_PER_CHIAMATA = 3


class EmbedderMultimodaleGemini:
    """Calcola vettori con gemini-embedding-2, combinando testo + immagine
    prodotto in un'unica chiamata quando l'immagine è disponibile. I vettori
    vengono passati a ChromaDB già pronti (non usiamo l'EmbeddingFunction
    automatica di Chroma, che accetta solo testo)."""

    def __init__(self, api_key: str, model_name: str):
        if not api_key:
            raise ValueError("GEMINI_API_KEY mancante. Impostala come variabile d'ambiente.")
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def _chiamata_con_retry(self, contents: list, task_type: str) -> list:
        ultimo_errore = None
        for tentativo in range(1, TENTATIVI_MASSIMI_PER_CHIAMATA + 1):
            try:
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=contents,
                    config=types.EmbedContentConfig(task_type=task_type),
                )
                vettore = response.embeddings[0]
                return vettore.values if hasattr(vettore, "values") else list(vettore)
            except Exception as errore:
                ultimo_errore = errore
                attesa = PAUSA_TRA_CHIAMATE_SEC * tentativo
                print(f"      [ATTENZIONE] Tentativo {tentativo} fallito ({errore}); riprovo tra {attesa:.1f}s")
                time.sleep(attesa)
        raise RuntimeError(f"Embedding fallito dopo {TENTATIVI_MASSIMI_PER_CHIAMATA} tentativi: {ultimo_errore}")

    def embed_documento(self, testo: str) -> list:
        """Embedding per l'indicizzazione (task_type=RETRIEVAL_DOCUMENT).
        Solo testo per le schede fornitori (nessuna immagine associata)."""
        return self._chiamata_con_retry([testo], task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, testo: str) -> list:
        """Embedding per una query utente (task_type=RETRIEVAL_QUERY)."""
        return self._chiamata_con_retry([testo], task_type="RETRIEVAL_QUERY")


def carica_fornitori():
    print(f"[INFO] Lettura file {FILE_EXCEL_ABSTRACT}...")
    try:
        # Leggiamo tutto come stringa per evitare problemi coi codici fornitore
        df = pd.read_excel(FILE_EXCEL_ABSTRACT, sheet_name=NOME_FOGLIO, dtype=str)
    except Exception as e:
        print(f"[ERRORE] Impossibile leggere il file Excel: {e}")
        return

    # Il file ha celle unite/vuote. Copiamo il valore della riga precedente (forward fill)
    colonne_da_riempire = ['an_forn', 'NOME_AZIENDA', 'RAGIONE_SOCIALE', 'TIPOLOGIA (CATALOGO DI APPARTENENZA)']
    for col in colonne_da_riempire:
        if col in df.columns:
            df[col] = df[col].ffill()

    print("[INFO] Connessione a ChromaDB...")
    client_db = chromadb.PersistentClient(path=PERCORSO_DB)

    print(f"[INFO] Attivazione embedder multimodale: {MODELLO_EMBEDDING}")
    embedder = EmbedderMultimodaleGemini(api_key=GEMINI_API_KEY, model_name=MODELLO_EMBEDDING)

    # Nessuna embedding_function qui: i vettori vengono sempre forniti a mano
    collezione = client_db.get_or_create_collection(name=NOME_COLLEZIONE)

    # Rilevamento ID già presenti per inserimento incrementale
    dati_esistenti = collezione.get()
    id_gia_salvati = set(dati_esistenti.get("ids", []))

    prodotti_pronti = []

    print("[INFO] Generazione schede fornitori...")
    # Raggruppiamo i blocchi di testo per singolo fornitore
    for (an_forn, nome), group in df.groupby(['an_forn', 'NOME_AZIENDA']):

        testo = f"--- STORIA E VALORI DEL FORNITORE ---\n"
        testo += f"Azienda: {nome} (Codice: {an_forn})\n"
        if 'RAGIONE_SOCIALE' in group.columns:
            testo += f"Ragione Sociale: {group['RAGIONE_SOCIALE'].iloc[0]}\n"
        testo += "\n"

        for _, row in group.iterrows():
            campo = str(row.get('CAMPO INFORMATIVO', '')).strip()
            contenuto = str(row.get('CONTENUTO', '')).strip()
            if campo and campo != 'nan' and contenuto and contenuto != 'nan':
                testo += f"[{campo.upper()}]\n{contenuto}\n\n"

        doc_id = f"FORNITORE_{an_forn}_{nome.replace(' ', '_')}"

        if doc_id in id_gia_salvati:
            continue

        # Usiamo le stesse chiavi di main_chatbot.py per massima compatibilità
        meta = {
            "nome_fornitore": nome,
            "categoria_prodotto": "SCHEDA AZIENDALE FORNITORE",
            "varianti_prodotto": "Info Storiche/Aziendali"
        }

        print(f"   [...] Embedding testo: {doc_id}")
        try:
            embedding = embedder.embed_documento(testo)
        except Exception as errore:
            print(f"      [ERRORE] Embedding fallito per {doc_id}: {errore}")
            continue
        time.sleep(PAUSA_TRA_CHIAMATE_SEC)

        prodotti_pronti.append({
            "documento": testo,
            "metadati": meta,
            "id": doc_id,
            "embedding": embedding,
        })

    if prodotti_pronti:
        print(f"[INFO] Inserimento di {len(prodotti_pronti)} fornitori nel database...")
        collezione.add(
            documents=[p["documento"] for p in prodotti_pronti],
            metadatas=[p["metadati"] for p in prodotti_pronti],
            ids=[p["id"] for p in prodotti_pronti],
            embeddings=[p["embedding"] for p in prodotti_pronti],
        )
        print(f"\n[SUCCESSO] {len(prodotti_pronti)} fornitori aggiunti al database vettoriale!")
    else:
        print("\n[INFO] Nessun nuovo fornitore da aggiungere. Database già aggiornato.")

if __name__ == "__main__":
    carica_fornitori()
