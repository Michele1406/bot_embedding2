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
# CONFIGURAZIONE FILE
# ====================================================================
# Modello di embedding: gemini-embedding-2 è GA (non preview) ed è nativamente
# multimodale (testo, immagini, audio, video, PDF) nello stesso spazio vettoriale.
MODELLO_EMBEDDING = "models/gemini-embedding-2"

# Percorsi assoluti verso la cartella sofood
FILE_CALENDARIO = r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD\sofood\calendario_freschi.xlsx"
FILE_CONSEGNE = r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD\sofood\consegne.xlsx"
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
        Solo testo per i dati logistici (nessuna immagine associata)."""
        return self._chiamata_con_retry([testo], task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, testo: str) -> list:
        """Embedding per una query utente (task_type=RETRIEVAL_QUERY)."""
        return self._chiamata_con_retry([testo], task_type="RETRIEVAL_QUERY")


def carica_logistica():
    client_db = chromadb.PersistentClient(path=PERCORSO_DB)

    print(f"[INFO] Attivazione embedder multimodale: {MODELLO_EMBEDDING}")
    embedder = EmbedderMultimodaleGemini(api_key=GEMINI_API_KEY, model_name=MODELLO_EMBEDDING)

    # Nessuna embedding_function qui: i vettori vengono sempre forniti a mano
    collezione = client_db.get_or_create_collection(name=NOME_COLLEZIONE)

    dati_esistenti = collezione.get()
    id_gia_salvati = set(dati_esistenti.get("ids", []))

    prodotti_pronti = []

    # ==========================================================
    # 1. ELABORAZIONE CALENDARIO FRESCHI
    # ==========================================================
    if os.path.exists(FILE_CALENDARIO):
        print(f"[INFO] Lettura file {FILE_CALENDARIO}...")
        try:
            df_cal = pd.read_excel(FILE_CALENDARIO, sheet_name="Calendario Ordini", dtype=str)
            for _, row in df_cal.iterrows():
                azienda = str(row.get('NOME AZIENDA', '')).strip()
                giorno_ordine = str(row.get("GIORNO ENTRO CUI FARE L'ORDINE", '')).strip()
                giorno_arrivo = str(row.get('GIORNO ARRIVO', '')).strip()

                if azienda and azienda != 'nan':
                    testo = f"--- CALENDARIO ORDINI FRESCHI ---\n"
                    testo += f"Fornitore / Azienda: {azienda}\n"
                    testo += f"Giorno limite per inviare l'ordine: {giorno_ordine}\n"
                    testo += f"Giorno previsto di arrivo merce: {giorno_arrivo}\n"

                    doc_id = f"CALENDARIO_{azienda.replace(' ', '_')}"
                    meta = {
                        "nome_fornitore": azienda,
                        "categoria_prodotto": "INFO LOGISTICA E ORDINI",
                        "varianti_prodotto": "Calendario Freschi"
                    }

                    if doc_id not in id_gia_salvati:
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
        except Exception as e:
            print(f"[ERRORE] Elaborazione calendario fallita: {e}")
    else:
        print(f"[AVVISO] File {FILE_CALENDARIO} non trovato.")

    # ==========================================================
    # 2. ELABORAZIONE PROGRAMMA CONSEGNE
    # ==========================================================
    if os.path.exists(FILE_CONSEGNE):
        print(f"[INFO] Lettura file {FILE_CONSEGNE}...")
        try:
            df_cons = pd.read_excel(FILE_CONSEGNE, sheet_name="Programma Consegne", dtype=str)

            # Rimuoviamo eventuali righe vuote o righe di sola intestazione extra
            df_cons = df_cons.dropna(subset=['NOME'])

            for _, row in df_cons.iterrows():
                zona = str(row.get('NOME', '')).strip()
                if not zona or zona == 'nan':
                    continue

                id_zona = str(row.get('ZONA (ID)', '')).strip()
                pagamento = str(row.get('METODI DI PAGAMENTO', '')).strip()
                minimo_ordine = str(row.get('MINIMO ORDINE PER CONSEGNA GRATUITA', '')).strip()
                costo_sped = str(row.get('COSTI SPEDIZIONE ', row.get('COSTI SPEDIZIONE', ''))).strip()
                tempi = str(row.get('TEMPI MEDI', '')).strip()
                condizioni = str(row.get('CONDIZIONI DI CONSEGNA', '')).strip()
                sede = str(row.get('SEDE LEGALE', '')).strip()
                deposito = str(row.get('DEPOSITO', '')).strip()

                testo = f"--- PROGRAMMA SPEDIZIONI E CONSEGNE ---\n"
                testo += f"Zona di Consegna: {zona} (ID: {id_zona})\n"
                testo += f"Tempi medi di consegna: {tempi}\n"
                testo += f"Minimo ordine per consegna gratuita: {minimo_ordine}\n"
                testo += f"Costo spedizione (se minimo non raggiunto): {costo_sped}\n"
                testo += f"Metodi di pagamento accettati: {pagamento}\n"
                testo += f"Condizioni di consegna: {condizioni}\n"
                testo += f"Sede Legale: {sede}\n"
                testo += f"Deposito logistico: {deposito}\n"

                doc_id = f"CONSEGNA_{zona.replace(' ', '_').upper()}"
                meta = {
                    "nome_fornitore": "SO FOOD (Logistica Interna)",
                    "categoria_prodotto": "REGOLE DI CONSEGNA ZONALE",
                    "varianti_prodotto": f"Spedizioni {zona}"
                }

                if doc_id not in id_gia_salvati:
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
        except Exception as e:
            print(f"[ERRORE] Elaborazione consegne fallita: {e}")
    else:
        print(f"[AVVISO] File {FILE_CONSEGNE} non trovato.")

    # ==========================================================
    # 3. SCRITTURA NEL DATABASE
    # ==========================================================
    if prodotti_pronti:
        print(f"[INFO] Inserimento di {len(prodotti_pronti)} regole logistiche nel database...")
        collezione.add(
            documents=[p["documento"] for p in prodotti_pronti],
            metadatas=[p["metadati"] for p in prodotti_pronti],
            ids=[p["id"] for p in prodotti_pronti],
            embeddings=[p["embedding"] for p in prodotti_pronti],
        )
        print(f"\n[SUCCESSO] {len(prodotti_pronti)} dati logistici aggiunti al database vettoriale!")
    else:
        print("\n[INFO] Nessuna nuova regola da aggiungere. Database già aggiornato.")

if __name__ == "__main__":
    carica_logistica()
