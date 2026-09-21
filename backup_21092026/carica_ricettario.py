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

# Il ricettario ora è diviso in un foglio per tipologia di piatto (invece di un
# unico foglio "Ricette"): aggiungere una nuova tipologia di piatto significa
# aggiungere un nuovo foglio con lo stesso schema di colonne e una riga qui
# sotto, nessun'altra modifica al codice è necessaria.
FOGLI_RICETTE = [
    "Antipasti", "Primi", "Secondi", "Contorni", "Pizze",
    "Dolci", "Taglieri", "Aperitivi_Tris", "Panini_Burger",
]
FOGLIO_INGREDIENTI = "Ingredienti"


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


def carica_ricettario(solo_nuove_o_modificate: bool = True):
    """Carica tutti i fogli-ricetta in ChromaDB.

    Se solo_nuove_o_modificate=True (default), salta le ricette il cui
    testo indicizzato non è cambiato dall'ultima volta (confrontando col
    documento già salvato in ChromaDB), per non rifare l'embedding via API
    di centinaia di ricette invariate ogni volta che se ne aggiunge una
    manciata di nuove. Passa False per forzare un reindex completo (utile
    dopo un cambio del formato del testo di embedding)."""
    if not os.path.exists(FILE_RICETTARIO):
        print(f"[ERRORE] File '{FILE_RICETTARIO}' non trovato.")
        return

    print(f"[TASK 1] Caricamento ricette da '{FILE_RICETTARIO}' ({len(FOGLI_RICETTE)} fogli categoria)...")
    fogli_disponibili = pd.ExcelFile(FILE_RICETTARIO).sheet_names
    frame_ricette = []
    for nome_foglio in FOGLI_RICETTE:
        if nome_foglio not in fogli_disponibili:
            print(f"  [AVVISO] Foglio '{nome_foglio}' non trovato nel file, salto.")
            continue
        df_foglio = pd.read_excel(FILE_RICETTARIO, sheet_name=nome_foglio)
        df_foglio["_foglio_origine"] = nome_foglio
        frame_ricette.append(df_foglio)

    if not frame_ricette:
        print("[ERRORE] Nessun foglio ricetta trovato/valido.")
        return

    ricette_df = pd.concat(frame_ricette, ignore_index=True)
    ingredienti_df = pd.read_excel(FILE_RICETTARIO, sheet_name=FOGLIO_INGREDIENTI)

    total_ricette = len(ricette_df)
    print(f"[TASK 1] Trovate {total_ricette} ricette totali e {len(ingredienti_df)} ingredienti.")

    client_db = chromadb.PersistentClient(path=PERCORSO_DB)
    collezione = client_db.get_or_create_collection(name=NOME_COLLEZIONE_RICETTE)
    embedder = EmbedderGemini(api_key=GEMINI_API_KEY)

    documenti_esistenti = {}
    if solo_nuove_o_modificate:
        dati_esistenti = collezione.get(include=["documents"])
        documenti_esistenti = dict(zip(dati_esistenti["ids"], dati_esistenti["documents"]))

    indicizzate = 0
    saltate = 0
    for idx, riga in ricette_df.iterrows():
        id_ricetta = str(riga["ID_RICETTA"]).strip()
        ingr_ricetta = ingredienti_df[ingredienti_df["ID_RICETTA"] == id_ricetta]

        elenco_ingredienti = ", ".join(ingr_ricetta["INGREDIENTE_GENERICO"].astype(str))
        testo_embedding = (
            f"{riga['NOME_PIATTO']} ({riga['CATEGORIA']}). "
            f"Stile: {riga.get('STILE_CUCINA', '')}. "
            f"Adatto a dieta: {riga.get('TAG_DIETA', '')}. "
            f"Ingredienti: {elenco_ingredienti}. "
            f"{riga.get('DESCRIZIONE_BREVE', '')}"
        )

        if solo_nuove_o_modificate and documenti_esistenti.get(id_ricetta) == testo_embedding:
            saltate += 1
            continue

        meta = {
            "tipo_voce": "ricetta",
            "nome_piatto": str(riga["NOME_PIATTO"]),
            "categoria": str(riga["CATEGORIA"]),
            "foglio_origine": str(riga.get("_foglio_origine", "")),
            "stile_cucina": str(riga.get("STILE_CUCINA", "")),
            # Tag per il redirect verso il locale/canale giusto (vedi Legenda nel file Excel):
            "tag_dieta": str(riga.get("TAG_DIETA", "")),
            "tag_protagonista": str(riga.get("TAG_PROTAGONISTA", "")),
            "canale": str(riga.get("CANALE", "")),
            "canali_adatti": str(riga.get("CANALI_ADATTI", "")),
            "canali_sconsigliati": str(riga.get("CANALI_SCONSIGLIATI", "")),
            "stato_verifica_catalogo": str(riga.get("STATO_VERIFICA_CATALOGO", "da_verificare")),
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
                print(f"[TASK 1] Progresso: {indicizzate} indicizzate, {saltate} invariate saltate...")
            time.sleep(0.15)
        except Exception as e:
            print(f"[ERRORE] Indicizzazione fallita per {id_ricetta}: {e}")

    totale_db = collezione.count()
    print(f"\n[TASK 1] Completato! Indicizzate/aggiornate: {indicizzate} | Invariate saltate: {saltate}")
    print(f"[TASK 1] Totale ricette in '{NOME_COLLEZIONE_RICETTE}': {totale_db}")
    return totale_db


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Carica il ricettario multi-foglio in ChromaDB")
    parser.add_argument("--forza-tutto", action="store_true",
                         help="Rifà l'embedding di TUTTE le ricette anche se invariate (costa più chiamate API)")
    args = parser.parse_args()
    carica_ricettario(solo_nuove_o_modificate=not args.forza_tutto)
