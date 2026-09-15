import os
import time
import chromadb
import pandas as pd
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY non trovata nel file .env")

MODELLO_EMBEDDING = "models/gemini-embedding-2"
PERCORSO_DB = "./database_vettoriale"
NOME_COLLEZIONE_RICETTE = "ricette_sofood"
FILE_RICETTARIO = "./Ricettario_SO_FOOD.xlsx"


class EmbedderGemini:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    def embed_documento(self, testo: str) -> list:
        for tentat in range(4):
            try:
                response = self.client.models.embed_content(
                    model=MODELLO_EMBEDDING,
                    contents=[testo],
                    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
                )
                vettore = response.embeddings[0]
                return vettore.values if hasattr(vettore, "values") else list(vettore)
            except Exception as e:
                if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and tentat < 3:
                    time.sleep(2 * (tentat + 1))
                    continue
                raise e


def carica_ricettario():
    if not os.path.exists(FILE_RICETTARIO):
        print(f"[ERRORE] File '{FILE_RICETTARIO}' non trovato.")
        return

    print(f"[TASK 1] Caricamento ricette da '{FILE_RICETTARIO}'...")
    ricette_df = pd.read_excel(FILE_RICETTARIO, sheet_name="Ricette")
    ingredienti_df = pd.read_excel(FILE_RICETTARIO, sheet_name="Ingredienti")

    total_ricette = len(ricette_df)
    print(f"[TASK 1] Trovate {total_ricette} ricette e {len(ingredienti_df)} ingredienti.")

    client_db = chromadb.PersistentClient(path=PERCORSO_DB)
    # Crea o ottiene la collection dedicata per le ricette
    collezione = client_db.get_or_create_collection(name=NOME_COLLEZIONE_RICETTE)
    embedder = EmbedderGemini(api_key=GEMINI_API_KEY)

    indicizzate = 0
    for idx, riga in ricette_df.iterrows():
        id_ricetta = str(riga["ID_RICETTA"]).strip()
        ingr_ricetta = ingredienti_df[ingredienti_df["ID_RICETTA"] == id_ricetta]

        elenco_ingredienti = ", ".join(ingr_ricetta["INGREDIENTE_GENERICO"].astype(str))
        testo_embedding = (
            f"{riga['NOME_PIATTO']} ({riga['CATEGORIA']}). "
            f"Stile: {riga.get('STILE_CUCINA', '')}. "
            f"Ingredienti: {elenco_ingredienti}. "
            f"{riga.get('DESCRIZIONE_BREVE', '')}"
        )

        meta = {
            "tipo_voce": "ricetta",
            "nome_piatto": str(riga["NOME_PIATTO"]),
            "categoria": str(riga["CATEGORIA"]),
            "stile_cucina": str(riga.get("STILE_CUCINA", "")),
            "canali_adatti": str(riga.get("CANALI_ADATTI", "")),
            "canali_sconsigliati": str(riga.get("CANALI_SCONSIGLIATI", "")),
            "note_composizione": str(riga.get("NOTE_COMPOSIZIONE", "")),
            "ingredienti_json": ingr_ricetta[
                ["INGREDIENTE_GENERICO", "CATEGORIA_ATTESA", "RUOLO", "NOTE_INGREDIENTE"]
            ].to_json(orient="records", force_ascii=False),
        }

        try:
            embedding = embedder.embed_documento(testo_embedding)
            collezione.upsert(
                documents=[testo_embedding],
                metadatas=[meta],
                ids=[id_ricetta],
                embeddings=[embedding],
            )
            indicizzate += 1
            if indicizzate % 25 == 0 or indicizzate == total_ricette:
                print(f"[TASK 1] Progresso: {indicizzate}/{total_ricette} ricette indicizzate...")
            time.sleep(0.15)
        except Exception as e:
            print(f"[ERRORE] Indicizzazione fallita per {id_ricetta}: {e}")

    totale_db = collezione.count()
    print(f"\n[TASK 1] Completato! Totale ricette in '{NOME_COLLEZIONE_RICETTE}': {totale_db}")
    return totale_db


if __name__ == "__main__":
    carica_ricettario()