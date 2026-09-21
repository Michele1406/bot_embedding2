import json
from google import genai
from google.genai import types
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")
client_genai = genai.Client(api_key=api_key)

# Usa Flash Lite per non bruciare quote del modello avanzato
MODELLO_BACKGROUND = "gemini-3.5-flash-lite"

PROMPT_DECOMPOSER = """Sei il Modulo Analisi Query (Query Decomposer) di So Food.
Il tuo compito è leggere l'ultimo messaggio dell'utente (e il contesto precedente) e produrre un piano di ricerca JSON.

Devi estrarre e classificare l'intento in uno di questi tipi:
- "tagliere_o_ricetta" (es. "voglio un tagliere", "fammi un panino", "un antipasto")
- "ricerca_catalogo" (es. "che birre avete?", "quali prosciutti ci sono?", "voglio 2 prosciutti", "elencami le mortadelle")
- "conversazione" (es. "ciao", "ok", "grazie")

Se l'intento è "tagliere_o_ricetta", compila il campo "componenti" con le diverse parti (salumi, formaggi, pane, olive, mare, contorni, extra). Se l'utente non elenca componenti specifiche (es. dice solo "fammi un tagliere classico"), inserisci una componente "generico" o lascia le query_pulita vuote, in modo che il sistema applichi i default (3 salumi, 3 formaggi). Ma se l'utente chiede "4 salumi e 2 formaggi", crea due componenti specifiche.

Per ogni componente estrai:
- "ruolo": (salumi, formaggi, mare, pane, olive, extra, oppure ricerca)
- "quantita_target": (intero, se specificato esplicitamente es. "3 prosciutti" -> 3. Se non specificato, scrivi null).
- "sottocategoria_forzata": (es. se l'utente chiede esplicitamente "2 prosciutti crudi", qui scrivi "prosciutto crudo", altrimenti null)
- "query_pulita": (la stringa testuale senza numeri o esclusioni, ottimizzata per ricerca vettoriale. Es: "prosciutto san daniele")
- "esclusioni": (lista di ingredienti o termini esplicitamente rifiutati dall'utente. Es. se dice "non voglio cotto", scrivi ["cotto", "prosciutto cotto", "wurstel"])

RISPONDI ESCLUSIVAMENTE CON IL SEGUENTE FORMATO JSON (Esempio per "dammi un tagliere con 4 salumi e 2 formaggi, di cui come prosciutto voglio il san daniele, e non voglio prosciutto cotto"):
{
  "intento": "tagliere_o_ricetta",
  "componenti": [
    {
      "ruolo": "salumi",
      "quantita_target": 4,
      "sottocategoria_forzata": "prosciutto",
      "query_pulita": "prosciutto san daniele",
      "esclusioni": ["cotto", "prosciutto cotto", "wurstel"]
    },
    {
      "ruolo": "formaggi",
      "quantita_target": 2,
      "sottocategoria_forzata": null,
      "query_pulita": "formaggio da degustazione",
      "esclusioni": []
    }
  ],
  "filtri_metadati": {
    "dieta": null
  }
}
"""

def scomponi_query(user_query: str, contesto: str = "") -> dict:
    prompt_completo = PROMPT_DECOMPOSER + f"\n\nContesto conversazione:\n{contesto}\n\nRichiesta Utente:\n{user_query}\n\nRisposta JSON:"
    
    try:
        risposta = client_genai.models.generate_content(
            model=MODELLO_BACKGROUND,
            contents=prompt_completo,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json"
            )
        )
        testo = risposta.text.strip()
        if testo.startswith("```json"):
            testo = testo[7:-3].strip()
        return json.loads(testo)
    except Exception as e:
        print(f"Errore Decomposer JSON: {e}")
        # Fallback sicuro
        return {
            "intento": "ricerca_catalogo",
            "componenti": [{"ruolo": "ricerca", "quantita_target": None, "sottocategoria_forzata": None, "query_pulita": user_query, "esclusioni": []}],
            "filtri_metadati": {"dieta": None}
        }
