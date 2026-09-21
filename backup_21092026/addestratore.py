import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FILE_LOG_CORREZIONI = "correzioni_chat.jsonl"
FILE_MEMORIA_DINAMICA = "memoria_dinamica.txt"
MODELLO_GEMINI = "models/gemini-3.5-flash-lite"

PROMPT_ADDESTRATORE = """
Sei un analista comportamentale per un assistente virtuale B2B nel settore alimentare (Nino).
Il tuo compito è analizzare i feedback e le correzioni del supervisore umano e trasformarli in REGOLE OPERATIVE E LOGICHE DI ABBINAMENTO chiare, concise e universali.

Per ogni feedback riceverai anche, quando disponibile, gli ultimi scambi della
conversazione da cui è nato: usali per capire ESATTAMENTE a cosa si riferiva
il supervisore, invece di generalizzare dalla sola frase di feedback isolata.

REGOLE DI OUTPUT:
- Estrapola solo la logica di base (es. "Se l'utente chiede formaggi freschi, proponi sempre salumi stagionati").
- Scrivi un elenco puntato.
- Non usare linguaggio discorsivo, scrivi solo direttive rigide.
- Unisci le nuove regole con quelle già esistenti (che ti fornirò), eliminando i doppioni o le contraddizioni.
"""

def addestra_nino():
    if not os.path.exists(FILE_LOG_CORREZIONI):
        print("[INFO] Nessuna correzione da analizzare al momento.")
        return

    # 1. Leggiamo le correzioni fatte in chat
    correzioni = []
    with open(FILE_LOG_CORREZIONI, "r", encoding="utf-8") as f:
        for linea in f:
            try:
                correzioni.append(json.loads(linea.strip()))
            except:
                pass

    if not correzioni:
        print("[INFO] Il file di log è vuoto.")
        return

    blocchi_correzioni = []
    for c in correzioni:
        blocco = f"Feedback: {c['testo']}"
        contesto = c.get("contesto_conversazione")
        if contesto:
            blocco += "\nContesto della conversazione in cui è nato il feedback:\n" + "\n".join(contesto)
        blocchi_correzioni.append(blocco)
    testo_correzioni = "\n\n---\n\n".join(blocchi_correzioni)

    # 2. Leggiamo le regole già apprese in passato (se esistono)
    regole_esistenti = ""
    if os.path.exists(FILE_MEMORIA_DINAMICA):
        with open(FILE_MEMORIA_DINAMICA, "r", encoding="utf-8") as f:
            regole_esistenti = f.read()

    # 3. Chiediamo a Gemini di estrapolare la logica aggiornata
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt_finale = f"""
    [REGOLE ATTUALI]
    {regole_esistenti if regole_esistenti else "Nessuna regola precedente."}

    [NUOVI FEEDBACK DA INTEGRARE]
    {testo_correzioni}
    """

    print("🧠 Analisi dei feedback in corso. Estrazione logiche di abbinamento...")
    
    try:
        response = client.models.generate_content(
            model=MODELLO_GEMINI,
            contents=prompt_finale,
            config=types.GenerateContentConfig(system_instruction=PROMPT_ADDESTRATORE)
        )
        
        nuove_regole = response.text.strip()
        
        # 4. Salviamo la nuova intelligenza nel file
        with open(FILE_MEMORIA_DINAMICA, "w", encoding="utf-8") as f:
            f.write(nuove_regole)
            
        # Svuotiamo il log delle correzioni ora che sono state assimilate
        open(FILE_LOG_CORREZIONI, 'w').close()
        
        print("\n✅ Addestramento completato! Nuove regole salvate in memoria_dinamica.txt.")
        print("Ecco cosa ha imparato Nino:\n")
        print(nuove_regole)
        
    except Exception as e:
        print(f"Errore durante l'addestramento: {e}")

if __name__ == "__main__":
    addestra_nino()
    