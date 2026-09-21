import os
import re
import json
import time
import datetime
from pathlib import Path
import chromadb
from google import genai
from google.genai import types
from core.system_prompt_v2 import SYSTEM_PROMPT_NINO
from core.query_decomposer import classify_intent, decompose_domain, verify_domain_rules
from core.retrieval_utils import (
    costruisci_indice_codici,
    costruisci_indice_fornitori,
    costruisci_indice_testuale,
    trova_match_esatti_per_codice,
    trova_match_per_fornitore,
    cerca_prodotti,
    costruisci_contesto_testuale,
)
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env
load_dotenv()

# ====================================================================
# CONFIGURAZIONE
# ====================================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("ATTENZIONE: GEMINI_API_KEY non trovata. Controlla di aver creato correttamente il file .env")

MODELLO_EMBEDDING = "models/gemini-embedding-2"
PERCORSO_DATABASE_VETTORIALE = "./database_vettoriale"
NOME_COLLEZIONE = "catalogo_sofood"
MODELLO_GEMINI = "models/gemini-3.6-flash" # Modello principale per rispondere
MODELLO_BACKGROUND = "models/gemini-3.5-flash-lite" # Modello leggero per background

PAUSA_TRA_CHIAMATE_SEC = 2.0
TENTATIVI_MASSIMI_PER_CHIAMATA = 3

N_RISULTATI_RAG = 45
MAX_SCAMBI_STORICO = 7
MAX_PRODOTTI_MOSTRATI_TRACCIATI = 60

# Logging delle conversazioni
CARTELLA_LOG_CHAT = Path("./log/chat")
CARTELLA_LOG_CHAT.mkdir(parents=True, exist_ok=True)
FREQUENZA_SALVATAGGIO_LOG = 3  # salva ogni N messaggi utente


class EmbedderMultimodaleGemini:
    """Calcola vettori con gemini-embedding-2 per interrogare ChromaDB."""

    def __init__(self, api_key: str, model_name: str):
        if not api_key:
            raise ValueError("GEMINI_API_KEY mancante.")
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
                print(f"      [ATTENZIONE] Tentativo {tentativo} fallito; riprovo...")
                time.sleep(attesa)
        raise RuntimeError(f"Embedding fallito: {ultimo_errore}")

    def embed_query(self, testo: str) -> list:
        return self._chiamata_con_retry([testo], task_type="RETRIEVAL_QUERY")


def salva_log_chat(log_chat: list, session_id: str):
    """Salva la conversazione su file JSON."""
    data_oggi = datetime.datetime.now().strftime("%Y-%m-%d")
    percorso_log = CARTELLA_LOG_CHAT / f"chat_cli_{session_id}_{data_oggi}.json"
    try:
        with open(percorso_log, "w", encoding="utf-8") as f:
            json.dump(log_chat, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ATTENZIONE] Salvataggio log chat fallito: {e}")


def avvia_chatbot():
    print("[INFO] Avvio assistente virtuale Nino (v2 — gemini-embedding-2, ricerca ibrida)...")

    client_genai = genai.Client(api_key=GEMINI_API_KEY)
    client_db = chromadb.PersistentClient(path=PERCORSO_DATABASE_VETTORIALE)
    embedder = EmbedderMultimodaleGemini(api_key=GEMINI_API_KEY, model_name=MODELLO_EMBEDDING)

    try:
        collezione = client_db.get_collection(name=NOME_COLLEZIONE)
    except Exception as e:
        print(f"[ERRORE] Impossibile trovare la collezione: {e}")
        return

    indice_codici_prodotto = costruisci_indice_codici(collezione)
    indice_fornitori = costruisci_indice_fornitori(collezione)
    indice_testuale = costruisci_indice_testuale(collezione)

    config_generazione = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT_NINO,
        temperature=0.3,
    )

    storico = []
    prodotti_mostrati = set()
    log_chat = []
    contatore_messaggi = 0
    session_id = datetime.datetime.now().strftime("%H%M%S")

    print("\n" + "="*70)
    print("🤖 NINO (So Food) È ONLINE - Digita la tua richiesta o 'esci' per chiudere.")
    print("="*70)

    while True:
        user_query = input("\nTu: ").strip()
        if user_query.lower() in ["esci", "exit", "quit"]:
            print("Nino: A presto! Buon lavoro in cucina.")
            # Salvataggio finale del log
            if log_chat:
                salva_log_chat(log_chat, session_id)
                print("[INFO] Log conversazione salvato.")
            break
        if not user_query:
            continue


        # RISCRITTURA QUERY CON CONTESTO COMPLETO
        testo_per_ricerca = user_query

        scambi_recenti = []
        if len(storico) > 0:
            for msg in storico[-6:]:
                ruolo = "Cliente" if msg.role == "user" else "Nino"
                testo_msg = "".join(p.text for p in msg.parts if getattr(p, "text", None))
                if len(testo_msg) > 300:
                    testo_msg = testo_msg[:300] + "..."
                scambi_recenti.append(f"{ruolo}: {testo_msg}")

        contesto_conversazione = "\n".join(scambi_recenti) if scambi_recenti else "(Inizio conversazione, nessun messaggio precedente)"

                # 5-NODE ARCHITECTURE: NODO 1 e NODO 2 (Intent & Decomposer)
        for tentat in range(3):
            try:
                intent_res = classify_intent(user_query, contesto_conversazione)
                tree = decompose_domain(user_query, contesto_conversazione)
                
                piano_ricerca = {
                    "intento": "tagliere_o_ricetta" if intent_res.intent == "TAGLIERE_O_RICETTA" else "ricerca_catalogo",
                    "componenti": []
                }
                for slot in tree.slots:
                    piano_ricerca["componenti"].append({
                        "ruolo": slot.macro_family.lower(),
                        "quantita_target": slot.quantity,
                        "sottocategoria_forzata": slot.forced_subcategory,
                        "query_pulita": " ".join(slot.constraints.must_have),
                        "esclusioni": slot.constraints.must_not_have
                    })
                    
                queries = []
                for comp in piano_ricerca.get("componenti", []):
                    q = comp.get("query_pulita")
                    if q:
                        queries.append(q)
                testo_per_ricerca = ", ".join(queries) if queries else user_query
                print(f"\\n[DEBUG RAG] Decomposer JSON generato: {piano_ricerca}\\n")
                break
            except Exception as e:
                if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and tentat < 2:
                    import time
                    time.sleep(4 * (tentat + 1))
                    continue
                print(f"[ATTENZIONE] Riscrittura query fallita, uso l'originale. Errore: {e}")
                testo_per_ricerca = user_query
                piano_ricerca = None
                break

        # RICERCA IBRIDA CON SPLIT MULTI-QUERY
        try:
            record_prodotti = []
            id_visti = set()

            # 1. Match ESATTO su codice direttamente dalla query originale dell'utente E riscritta
            esatti_utente = trova_match_esatti_per_codice(user_query, indice_codici_prodotto, collezione)
            esatti_riscritti = trova_match_esatti_per_codice(testo_per_ricerca, indice_codici_prodotto, collezione)
            for r in esatti_utente + esatti_riscritti:
                if r["id"] not in id_visti:
                    id_visti.add(r["id"])
                    record_prodotti.append(r)

            # 2. Match su fornitore/brand direttamente dalla query originale E riscritta
            fornitori_utente = trova_match_per_fornitore(user_query, indice_fornitori, collezione, max_risultati=8)
            fornitori_riscritti = trova_match_per_fornitore(testo_per_ricerca, indice_fornitori, collezione, max_risultati=8)
            for r in fornitori_utente + fornitori_riscritti:
                if r["id"] not in id_visti:
                    id_visti.add(r["id"])
                    record_prodotti.append(r)

            # 3. SPLIT DELLA QUERY PER RICERCHE MULTIPLE MIRATE
            testi_da_cercare = [t.strip() for t in testo_per_ricerca.split(",") if t.strip()]
            risultati_per_query = max(3, N_RISULTATI_RAG // len(testi_da_cercare)) if len(testi_da_cercare) > 0 else N_RISULTATI_RAG

            for singolo_testo in testi_da_cercare:
                risultati_parziali = cerca_prodotti(
                    collezione, indice_codici_prodotto, embedder, singolo_testo,
                    n_risultati=risultati_per_query,
                    indice_fornitori=indice_fornitori,
                    indice_testuale=indice_testuale
                )
                for r in risultati_parziali:
                    if r["id"] not in id_visti:
                        id_visti.add(r["id"])
                        record_prodotti.append(r)

            record_prodotti = record_prodotti[:N_RISULTATI_RAG]
        except Exception as e:
            print(f"\n[ERRORE] Ricerca prodotti fallita: {e}")
            continue

        contesto_testuale = costruisci_contesto_testuale(record_prodotti, prodotti_mostrati)

        for r in record_prodotti:
            prodotti_mostrati.add(r["id"])
        if len(prodotti_mostrati) > MAX_PRODOTTI_MOSTRATI_TRACCIATI:
            prodotti_mostrati = set(list(prodotti_mostrati)[-MAX_PRODOTTI_MOSTRATI_TRACCIATI:])

        prompt_finale = f"""
        [DATI RAG ESTRATTI DAL CATALOGO - USA QUESTE INFO PER RISPONDERE]
        {contesto_testuale}

        [DOMANDA DELL'UTENTE]
        {user_query}
        """

        max_messaggi = MAX_SCAMBI_STORICO * 2
        if len(storico) > max_messaggi:
            storico[:] = storico[-max_messaggi:]

        # Generazione risposta con ciclo di Auto-Correzione (Reflection)
        for tentativo_riflessione in range(2):
            chat_session = client_genai.chats.create(
                model=MODELLO_GEMINI,
                config=config_generazione,
                history=storico
            )
            
            try:
                response = chat_session.send_message(prompt_finale)
                testo_pulito = response.text or ""
                # 1. Protezione anti-allucinazione fonetica (es. Sottofondo -> Sottovuoto)
                testo_pulito = re.sub(r'\b[Ss]ottofondo\b', 'sottovuoto', testo_pulito)
                # 2. Pulizia nomi in grassetto: rimozione Brand -, rimozione packaging, trim spazi interni
                testo_pulito = re.sub(r'\*\*([^\n*]{1,40}?)\s+-\s+([^\n*]+?)\*\*', r'**\2**', testo_pulito)
                testo_pulito = re.sub(r'(\*\*[^*]*?)\s*\b(?:[Ss]ottovuoto|[Ss]/[Vv]|[Aa][Tt][Mm]|[Ss]/[Oo])\b\s*([^*]*?\*\*)', r'\1\2', testo_pulito)
                testo_pulito = re.sub(r'\*\*([^*]+?)\*\*', lambda m: f'**{m.group(1).strip()}**', testo_pulito)
                
                # --- REFLECTION LOOP ---
                esclusioni_richieste = []
                if 'piano_ricerca' in locals() and piano_ricerca:
                    for c in piano_ricerca.get("componenti", []):
                        esclusioni_richieste.extend(c.get("esclusioni", []))
                
                if esclusioni_richieste and tentativo_riflessione == 0:
                    escl_str = ", ".join(esclusioni_richieste)
                    prompt_riflessione = f"L'utente NON vuole assolutamente questi ingredienti: {escl_str}.\nLa tua risposta li contiene per sbaglio? Rispondi solo 'ERRORE' se sì, altrimenti 'OK'.\n\nRISPOSTA:\n{testo_pulito}"
                    res_rif = client_genai.models.generate_content(model=MODELLO_BACKGROUND, contents=prompt_riflessione)
                    if "ERRORE" in (res_rif.text or "").upper():
                        print(f"[REFLECTION] Errore rilevato: Ingredienti vietati ({escl_str}) inclusi per sbaglio. Rigenero.")
                        prompt_finale += f"\n\nATTENZIONE: Nella tua risposta precedente hai incluso un ingrediente vietato ({escl_str}). Riprova escludendolo totalmente."
                        continue
                
                print(f"\nNino: {testo_pulito}")

                storico.append(types.Content(role="user", parts=[types.Part.from_text(text=user_query)]))
                storico.append(types.Content(role="model", parts=[types.Part.from_text(text=testo_pulito)]))

                # LOGGING
                timestamp_ora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_chat.append({"ruolo": "utente", "testo": user_query, "timestamp": timestamp_ora})
                log_chat.append({"ruolo": "nino", "testo": testo_pulito, "timestamp": timestamp_ora})
                contatore_messaggi += 1

                if contatore_messaggi % FREQUENZA_SALVATAGGIO_LOG == 0:
                    salva_log_chat(log_chat, session_id)
                    print("[INFO] Log conversazione aggiornato.")

                break
            except Exception as e:
                print(f"\n[ERRORE]: {e}")
                break


if __name__ == "__main__":
    avvia_chatbot()
