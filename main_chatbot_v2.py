import os
import json
import time
import datetime
from pathlib import Path
import chromadb
from google import genai
from google.genai import types
from system_prompt_v2 import SYSTEM_PROMPT_NINO
from retrieval_utils import (
    costruisci_indice_codici,
    costruisci_indice_fornitori,
    trova_match_esatti_per_codice,
    trova_match_per_fornitore,
    cerca_prodotti,
    costruisci_contesto_testuale,
    rileva_intent_query,
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
MODELLO_GEMINI = "models/gemini-3.5-flash-lite"

PAUSA_TRA_CHIAMATE_SEC = 2.0
TENTATIVI_MASSIMI_PER_CHIAMATA = 3

N_RISULTATI_RAG = 25
MAX_SCAMBI_STORICO = 7
MAX_PRODOTTI_MOSTRATI_TRACCIATI = 40

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

        # EASTER EGG SPECIALE
        q_norm = user_query.strip().lower()
        q_raw = user_query.lower()
        if q_raw == " ano fame" or q_norm == "ano fame":
            risposta = "KI?"
            print(f"\nNino: {risposta}\n")
            storico.append(types.Content(role="user", parts=[types.Part.from_text(text=user_query)]))
            storico.append(types.Content(role="model", parts=[types.Part.from_text(text=risposta)]))
            continue

        if q_norm in ["le scimie", "le scimmie"]:
            ultimo_bot = ""
            if storico:
                for msg in reversed(storico):
                    if msg.role == "model":
                        ultimo_bot = "".join(p.text for p in msg.parts if getattr(p, "text", None)).strip()
                        break
            if ultimo_bot == "KI?" or not ultimo_bot:
                risposta = "GAS ei ou lo vuoi , ki Maicol, no volerlo, tu lo vuoi u u u , Nino essersi rotto u u u "
                print(f"\nNino: {risposta}\n")
                storico.append(types.Content(role="user", parts=[types.Part.from_text(text=user_query)]))
                storico.append(types.Content(role="model", parts=[types.Part.from_text(text=risposta)]))
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

        prompt_riscrittura = f"""Sei un estrattore di chiavi di ricerca per un database di prodotti alimentari.

Ecco gli ultimi scambi della conversazione tra il cliente e l'assistente Nino:
{contesto_conversazione}

Il cliente ha appena scritto:
"{user_query}"

COMPITO:
- Se il cliente usa pronomi o riferimenti (es. "questi", "quelli", "fammeli vedere", "mostrameli", "anche quelli di prima", "quello da 500g", "la prima opzione"):
  * Estrai il NOME COMPLETO e SPECIFICO del prodotto a cui si riferisce nel messaggio precedente (es. se Nino ha citato "nocciole da 500g" e il cliente dice "mi fai vedere quello da 500g", devi estrarre "nocciole al tartufo 500g", NON solo "500g"!).
  * Se nel messaggio precedente si parlava di un codice prodotto (es. LPSFIESP) o di un prodotto specifico, includi quel codice e nome esatto.
- Se il cliente risponde semplicemente "sì", "si", "ok", "certo", "volentieri" a una proposta di Nino nel messaggio precedente (es. se Nino ha chiesto "ti va di abbinare delle birre?" e il cliente risponde "si"): estrai l'argomento o i prodotti appena proposti da Nino (es. se proponeva birre, estrai "Birrificio Messina birre").
- Se il cliente menziona "inglesi", "pub inglese", "siamo inglesi": estrai "Nino Galli roast beef pastrami, Farino buns burger, Oberto burger fassona, Birrificio Messina birre".
- Se il cliente chiede birre (es. "birre", "che birre", "dimmi tutte le birre", "tutte birre", "altre birre"): estrai "Birrificio Messina birre BM1503 BM1505 BM1553 BM1555 BM1560 BM1580".
- Se il cliente chiede formati piccoli, assaggi o campionature (es. "formati piccoli", "per provarli tutti", "espositore"), estrai "espositore, degustazione, spicchi, skin, monodose".
- Se il cliente chiede formati professionali o ristorazione (es. "formato horeca", "formato ristorante", "per la ristorazione", "secchiello"): estrai "De Filippis olive secchiello 5kg, Casa Marrazzo pelati latta, Il Convento baba 3kg".
- Se il cliente menziona uno stile di cucina, locale o tipologia di menu:
  * Cucina spagnola / tapas / paella: estrai "Solera Bellota iberico, Cecinas Nieto cecina, Medimer acciughe cantabrico, Capuano olive, Acquerello riso, Oberto fassona tagliata costata, Delfino pesce spada, Birrificio Messina, Il Convento baba"
  * Hamburgeria / burger / street food / pub con cucina: estrai "Oberto fassona macinato burger, Patrone hamburger maiale nero porchetta, Nino Galli pastrami roast beef, Farino buns padellino puccia, Ado pancetta, Agricola Buongiorno ketchup crusco, Biobonta salse, Valle di Gresta patatine, Birrificio Messina, Il Convento baba"
  * Cucina pugliese: estrai "Gentile orecchiette, Di Tria cime di rapa sponsali assassina bite, Agricola Buongiorno peperone crusco, Pessolani podolico, Martina Franca capocollo, Farino taralli, Birrificio Messina, Il Convento baba"
  * Cucina napoletana: estrai "Casa Marrazzo San Marzano friarielli, Gentile pasta gragnano, Cosi Com'e datterini, Delfino colatura alici, Latte Nobile, Il Convento baba limoncello, Birrificio Messina"
  * Cucina italiana classica / trattoria / primi e secondi: estrai "Gentile pasta gragnano, Scudellaro pollo eviscerato uova, Oberto fassona cube roll costata, Di Tria cardoncelli carciofi, Casa Marrazzo san marzano, Guglielmi olio, Boschi soffritto spezie, Il Convento baba, Birrificio Messina"
  * Cucina romana / carbonara / amatriciana / cacio e pepe: estrai "Gentile pasta gragnano, Ado pancetta, Scudellaro uova, Abbondanza pecorino, Casa Marrazzo pelati, Il Convento baba, Birrificio Messina"
  * Polpettificio / polpette: estrai "Oberto fassona macinato trita, Scudellaro pollo uova, Casa Marrazzo pelati, Di Tria assassina bite cardoncelli, Boschi soffritto, Guglielmi olio, Il Convento baba, Birrificio Messina"
  * Cucina paninoteca / kebab gourmet / street food: estrai "Farino puccia padellino buns, Fresco Piada, Oberto fassona sfilaccio, Patrone porchetta, Nino Galli pastrami roast beef, Agricola Buongiorno ketchup crusco, Valle di Gresta patatine, Birrificio Messina, Il Convento baba"
  * Cucina di mare / pesce / primi di mare: estrai "Gentile pasta fusilli spaghettoni, Delfino colatura alici tonno filetti, Medimer alici cantabrico, Smeralda bottarga granchio, Cosi Com'e datterini, Guglielmi olio limone"
  * Cucina messicana / tex-mex: estrai "Oberto fassona macinato, Fresco Piada, Casa Marrazzo san marzano, Boschi peperoncino cipolla, Birrificio Messina birre, Il Convento baba"
  * Cucina greca: estrai "Capuano olive cerignola, De Filippis olive secchiello, Medimer alici cantabrico, Farino puccia, Oberto fassona tagliata, La Nicchia origano, Birrificio Messina birre, Il Convento baba"
  * Cucina cinese / giapponese / asiatica: estrai "Medimer alici cantabrico, Smeralda salmone selvaggio, Colimena tonno, Guglielmi olio limone, Birrificio Messina birre, Il Convento baba"
  * Dolci / dessert / fine pasto: estrai "Il Convento baba limoncello rum, Fratelli Lunardi cantucci cookies crema gianduia, Latte Nobile yogurt limoni albicocche, Scudellaro uova biologiche, Evergreen crema pistacchio"
  * Tiramisù / cheesecake / dolci al cucchiaio: estrai "Fratelli Lunardi cantucci cookies cioccolato mandorle, Scudellaro uova biologiche, Latte Nobile yogurt fresco, Evergreen crema pistacchio, Il Convento limoncello"
  * Menu completo: se il cliente chiede un menu completo, estrai sempre "Birrificio Messina birre, Il Convento baba rum limoncello, Fratelli Lunardi cantucci cookies, Latte Nobile yogurt, Capuano olive cerignola, Farino taralli, Di Tria assassina bite" unito ai prodotti della cucina menzionata.
  * Soffritto / spezie / aromi: estrai "Boschi preparato aromatico soffritto marinara spezie"
  * Tapas per aperitivo: estrai "Di Tria assassina bite parmigianine frittelline, Medimer acciughe cantabrico, Colimena tonno, De Giorgi carciofini"
- Se il cliente menziona un codice articolo (es. LPSFIESP, CAP00001, ecc.), mantieni sempre il codice esatto nella chiave.
- Se il cliente cambia argomento o fa una nuova richiesta, restituisci la sua richiesta riformulata in modo chiaro per una ricerca nel catalogo.
- Se ci sono più prodotti, separali con virgola.

REGOLA ASSOLUTA: Niente convenevoli, nessuna spiegazione, solo le chiavi di ricerca."""

        for tentat in range(3):
            try:
                risposta_riscrittura = client_genai.models.generate_content(
                    model=MODELLO_GEMINI,
                    contents=prompt_riscrittura,
                    config=types.GenerateContentConfig(temperature=0.0)
                )
                testo_per_ricerca = risposta_riscrittura.text.strip()
                print(f"\n[DEBUG RAG] Query Originale: '{user_query}' -> Query Riscritta: '{testo_per_ricerca}'\n")
                break
            except Exception as e:
                if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and tentat < 2:
                    import time
                    time.sleep(4 * (tentat + 1))
                    continue
                print(f"[ATTENZIONE] Riscrittura query fallita, uso l'originale. Errore: {e}")
                break

        # RICERCA IBRIDA CON SPLIT MULTI-QUERY
        try:
            # Rileva se l'utente ha un intent specifico (es. tagliere, aperitivo)
            intent_rilevato = rileva_intent_query(user_query) or rileva_intent_query(testo_per_ricerca)

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
                if r["id"] not in id_visti:
                    id_visti.add(r["id"])
                    record_prodotti.append(r)

            # 3. SPLIT DELLA QUERY PER RICERCHE MULTIPLE MIRATE
            testi_da_cercare = [t.strip() for t in testo_per_ricerca.split(",") if t.strip()]
            risultati_per_query = max(3, N_RISULTATI_RAG // len(testi_da_cercare)) if len(testi_da_cercare) > 0 else N_RISULTATI_RAG

            for singolo_testo in testi_da_cercare:
                risultati_parziali = cerca_prodotti(
                    collezione, indice_codici_prodotto, embedder, singolo_testo,
                    risultati_per_query, config_intent=intent_rilevato,
                    indice_fornitori=indice_fornitori
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

        except Exception as e:
            print(f"\n[ERRORE]: {e}")


if __name__ == "__main__":
    avvia_chatbot()
