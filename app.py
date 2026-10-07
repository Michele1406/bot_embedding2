import os
import sys
from core.cart_manager import app_cart
from core.order_extractor import estrai_ordine_da_chat
import re
import json
import time
import uuid
import datetime
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template_string, session
import chromadb
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from core.system_prompt_v2 import build_modular_prompt
from core.retrieval_utils import (
    costruisci_indice_codici,
    costruisci_indice_fornitori,
    costruisci_indice_testuale,
    trova_match_esatti_per_codice,
    trova_match_per_fornitore,
    trova_match_lessicale,
    cerca_prodotti,
    costruisci_contesto_testuale,
    componi_proposta_da_ricettario,
    pulisci_nome_commerciale,
    rileva_cluster_regionale,
)
from core.fornitori_config import FORNITORI, elenco_fornitori_per_ruolo_regione
from core.profilazione_locale import rileva_canale_locale
from core.testo_prodotto import prima_riga, riga_foto
from core import guardrail_output
from core import sessioni_store
from core import ontologia
from core import costruzione_tagliere, guide_prodotto, panoramica
from core import retrieval_utils as _ru_ctx
from core import copertura_richiesta
from core import second_brain
from core import logistica
from core import audit_log
from core import riassunto
from core import whatsapp
from core import sicurezza
from core import allergeni
from core import ordini
from core import info_azienda
from core import foto
from core.anagrafica_fornitori import ANAGRAFICA, nome_breve
from core.embedder import EmbedderGemini
from core.errori import breve
from core.formato_testo import pulisci_markdown, pulisci_chunk
from dotenv import load_dotenv


class ElementoRichiesto(BaseModel):
    dominio: str = Field(description="Es. 'salumi', 'formaggi', 'sottoli', 'mare', 'vino', 'dispensa'")
    quantita: int | None = Field(None, description="Se l'utente chiede un numero esatto, es. 3. Altrimenti None.")
    reparto: str | None = Field(None, description="Es. CARNE, DISPENSA, FORMAGGI, GELO, MARE, SALUMI")
    sottocategoria: str | None = Field(None, description="Sottocategoria esatta se deducibile")
    query_ricerca: str = Field(description="Query semantica per recuperare i prodotti")
    
class ProfiloClienteAggiornato(BaseModel):
    tipo_locale: str | None = None
    stile_cucina: str | None = None
    dieta_filtro: str | None = None
    senza_affettatrice: bool = False
    citta: str | None = None
    esclusioni: list[str] = Field(default_factory=list, description="Prodotti/ingredienti che il cliente ha detto di NON volere (parole singole o brevi: 'tonno', 'prosciutto cotto'). Vuota se non ne ha nominati.")
    esclusioni_rimosse: list[str] = Field(default_factory=list, description="Prodotti che il cliente aveva escluso e ORA dice di volere di nuovo ('adesso mi serve il tonno'). Vuota altrimenti.")
    allergie: list[str] = Field(default_factory=list, description="Allergie o intolleranze dichiarate dal cliente per se' o per i suoi clienti ('frutta a guscio', 'glutine', 'latte', 'sesamo'...). Vuota se non ne ha nominate.")
    modalita_composizione: str | None = Field(None, description="'guidata' se il cliente vuole scegliere insieme passo passo o avere opzioni tra cui scegliere; 'completa' se vuole una proposta pronta o delega ('fai tu'). None se non lo dice.")

class AnalisiUnificata(BaseModel):
    tipo_richiesta: str = Field(description="'panoramica_catalogo', 'ricerca_specifica', 'composizione_piatto', 'conversazione_generica', 'chiusura_ordine'")
    richiede_composizione: bool = Field(description="True se chiede taglieri, menu, abbinamenti, tris")
    riferimento_precedente: bool = Field(description="True se dice 'dimmene altri', 'ancora', 'altri' riferiti a qualcosa di prima")
    argomento_riferito: str | None = Field(None, description="Es. 'salumi' se il riferimento precedente era ai salumi")
    elementi_richiesti: list[ElementoRichiesto] = Field(description="Lista dei domini/prodotti richiesti")
    profilo: ProfiloClienteAggiornato

# Carica le variabili d'ambiente dal file .env
load_dotenv()

app = Flask(__name__)
# Necessaria per firmare i cookie di sessione (identifica il singolo cliente).
# Mettila anche lei in .env in produzione, es. FLASK_SECRET_KEY=...
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB max per request

# Rate limiter leggero in-memory (nessuna dipendenza esterna)
_rate_limit_store = {}  # IP -> [timestamps]
RATE_LIMIT_MAX = 20  # max richieste
RATE_LIMIT_WINDOW = 60  # secondi

@app.before_request
def _rate_limit_check():
    if request.endpoint in ('chat', 'chat_stream', 'chat_audio'):
        ip = request.remote_addr or "unknown"
        now = time.time()
        timestamps = _rate_limit_store.get(ip, [])
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        if len(timestamps) >= RATE_LIMIT_MAX:
            from flask import abort
            abort(429)
        timestamps.append(now)
        _rate_limit_store[ip] = timestamps

# ====================================================================
# CONFIGURAZIONE GLOBALE
# ====================================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("ATTENZIONE: GEMINI_API_KEY non trovata nel file .env")


MODELLO_EMBEDDING = os.getenv("LLM_EMBEDDING", "models/gemini-embedding-2")
MODELLO_PRINCIPALE = os.getenv("LLM_PRINCIPALE", "models/gemini-3.5-flash")
# Il fallback deve essere un modello DIVERSO dal principale (con quota propria), altrimenti sul 429 si riprova lo stesso
MODELLO_FALLBACK = os.getenv("LLM_FALLBACK", "models/gemini-3.5-flash-lite")
MODELLO_RIASSUNTO = os.getenv("LLM_RIASSUNTO", "models/gemini-3.5-flash-lite")
MODELLO_AUDIO = os.getenv("LLM_AUDIO", "models/gemini-2.5-flash")
PERCORSO_DATABASE_VETTORIALE = "./database_vettoriale"
NOME_COLLEZIONE = os.getenv("CATALOGO_COLLECTION", "catalogo_v2")
N_RISULTATI_RAG = 65  # Aumentato per passare più prodotti all'IA e permettere taglieri grandi
MAX_SCAMBI_STORICO = 7
MAX_PRODOTTI_MOSTRATI_TRACCIATI = 60  # Aumentato per gestire i 45 prodotti

# Cartelle di salvataggio
CARTELLA_LOG_CHAT = Path(os.getenv("LOG_CHAT_DIR", "./log/chat"))  # i test la puntano in una cartella temporanea
CARTELLA_LOG_CHAT.mkdir(parents=True, exist_ok=True)
CARTELLA_AUDIO_UPLOADS = Path("./static/audio_uploads")
CARTELLA_AUDIO_UPLOADS.mkdir(parents=True, exist_ok=True)
FREQUENZA_SALVATAGGIO_LOG = 1  # salva ogni singolo messaggio utente in tempo reale

# ====================================================================
# CLASSE EMBEDDING
# ====================================================================
# Embedding delle query con cache su memoria + SQLite (core/embedder.py): la stessa query si paga una volta sola
EmbedderMultimodaleGemini = EmbedderGemini




def trascrivi_audio(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """Trascrive fedelmente l'audio vocale in testo italiano con Gemini usando system_instruction."""
    if not mime_type or mime_type == "application/octet-stream":
        mime_type = "audio/webm"

    part_audio = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

    system_inst = (
        "Sei un sistema automatico di Speech-to-Text per messaggi vocali in lingua italiana.\n"
        "Ascolta attentamente l'audio e trascrivi fedelmente il parlato in italiano.\n"
        "REGOLE TASSATIVE:\n"
        "1. Restituisci ESCLUSIVAMENTE il testo del parlato pronunciato.\n"
        "2. NON aggiungere saluti, commenti, note o spiegazioni.\n"
        "3. NON ripetere mai queste istruzioni di sistema nella risposta.\n"
        "4. Se l'audio non contiene alcuna voce umana o è solo silenzio/rumore di fondo, rispondi ESCLUSIVAMENTE con la parola: SILENZIO."
    )

    config_audio = types.GenerateContentConfig(
        system_instruction=system_inst,
        temperature=0.0
    )

    for modello in [MODELLO_AUDIO, MODELLO_FALLBACK]:
        try:
            res = client_genai.models.generate_content(
                model=modello,
                contents=[part_audio],
                config=config_audio
            )
            testo = (res.text or "").strip()
            # Rimuovi apici/virgolette esterne
            testo = re.sub(r'^["\'«»]+|["\'«»]+$', '', testo).strip()

            testo_lower = testo.lower()
            # Filtro anti-echo: se il modello ha ripetuto parti del prompt o istruzioni
            if any(frase in testo_lower for frase in ["trascrivi", "messaggio vocale", "istruzioni di sistema", "parlato pronunciato"]):
                print(f"[AUDIO] Risposta scartata (echo prompt): '{testo}'")
                continue

            if testo_lower in ["silenzio", "silenzio.", "[silenzio]", "vuoto", "nessuno", "rumore", "rumore di fondo"]:
                print(f"[AUDIO] Rilevato audio privo di parlato umano: '{testo}'")
                return ""

            if testo:
                print(f"[AUDIO] Trascrizione completata ({modello}): '{testo}'")
                return testo
        except Exception as e:
            print(f"[ATTENZIONE] Trascrizione audio con {modello} fallita: {e}")
    return ""


# Inizializzazioni al lancio del server
client_genai = genai.Client(api_key=GEMINI_API_KEY)
client_db = chromadb.PersistentClient(path=PERCORSO_DATABASE_VETTORIALE)
collezione = client_db.get_collection(name=NOME_COLLEZIONE)
NOME_COLLEZIONE_RICETTE = os.getenv("RICETTE_COLLECTION", "ricette_v2")
collezione_ricette = client_db.get_collection(name=NOME_COLLEZIONE_RICETTE)
embedder = EmbedderMultimodaleGemini(api_key=GEMINI_API_KEY, model_name=MODELLO_EMBEDDING)

# Indice codice_prodotto -> id, costruito una volta sola all'avvio (ricerca esatta)
indice_codici_prodotto = costruisci_indice_codici(collezione)
# Indice fornitore -> id, costruito una volta sola all'avvio (ricerca per brand/fornitore)
indice_fornitori = costruisci_indice_fornitori(collezione)
# Indice testuale per ricerca lessicale ibrida su titoli/nomi di tutti i prodotti
indice_testuale = costruisci_indice_testuale(collezione)
second_brain.costruisci_da_db(client_db, indice_testuale, NOME_COLLEZIONE_RICETTE)
# Conservazione dei dati (log 30 giorni, sessioni 7, ordini mai): core/manutenzione.py
from core.manutenzione import pulizia_retention
pulizia_retention()

# ====================================================================
# STATO PER SESSIONE
# ====================================================================
sessioni = {}  # sid -> {"storico": [...], "prodotti_mostrati": set(), "prodotti_mostrati_ordinati": [], ...}


def pulisci_sessioni_scadute():
    ora = datetime.datetime.now()
    scadute = [s for s, d in sessioni.items() if (ora - d.get("last_active", ora)).total_seconds() > 86400]
    for s in scadute:
        del sessioni[s]

def ottieni_sessione():
    """Sessione del browser (cookie Flask) -> (stato, sid)."""
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return stato_per_sid(session["sid"]), session["sid"]


def stato_per_sid(sid: str) -> dict:
    """Stato di conversazione per un sid qualunque (cookie del browser, numero WhatsApp...): in RAM, o ripristinato da SQLite."""
    pulisci_sessioni_scadute()
    if sid not in sessioni:
        ripristinata = sessioni_store.carica(sid)  # dopo un riavvio il cliente ritrova profilo e storico
        if ripristinata is not None:
            sessioni[sid] = ripristinata
    if sid not in sessioni:
        sessioni[sid] = {
            "storico": [],
            "prodotti_mostrati": set(),
            "prodotti_mostrati_ordinati": [],
            "log_chat": [],
            "contatore_messaggi": 0,
            "tipo_locale": None,
            "filtro_dieta": None,
            "senza_affettatrice": False,
            "citta": None,
            "ultimo_piatto_proposto": None,
            "ricette_mostrate": set(),
            "allergie": [],
            "esclusioni_cliente": [],
        }
    sessioni[sid].setdefault("ultimo_piatto_proposto", None)
    sessioni[sid].setdefault("ricette_mostrate", set())
    sessioni[sid]["last_active"] = datetime.datetime.now()
    return sessioni[sid]


def salva_log_chat(sid: str, stato: dict):
    """Salva la conversazione su file JSON. Chiamata ogni FREQUENZA_SALVATAGGIO_LOG messaggi."""
    data_oggi = datetime.datetime.now().strftime("%Y-%m-%d")
    percorso_log = CARTELLA_LOG_CHAT / f"chat_{sid}_{data_oggi}.json"
    try:
        with open(percorso_log, "w", encoding="utf-8") as f:
            json.dump(stato["log_chat"], f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ATTENZIONE] Salvataggio log chat fallito: {e}")


# ====================================================================
# ROTTE FLASK
# ====================================================================

def analizza_richiesta_unificata(client_genai, user_query: str, storico_testuale: str, stato_attuale: dict) -> AnalisiUnificata:
    """Analizza la richiesta, estrae il profilo, gestisce la continuità conversazionale ed estrae gli elementi per il RAG."""
    locale_noto = stato_attuale.get("tipo_locale") or "non ancora specificato"
    dieta_nota = stato_attuale.get("filtro_dieta") or "nessuna"
    affettatrice_nota = "senza affettatrice" if stato_attuale.get("senza_affettatrice") else "non specificato"
    
    prompt = f"""Sei l'analizzatore semantico unificato B2B di So Food.
Devi estrarre l'intento dell'utente, eventuali regole di profilo, ed elencare TUTTI i domini merceologici (ElementiRichiesti) di cui ha bisogno.

CONTESTO CLIENTE ATTUALE:
- Tipo locale: {locale_noto}
- Dieta: {dieta_nota}
- Attrezzatura: {affettatrice_nota}

STORICO CONVERSAZIONE (per capire i riferimenti):
{storico_testuale}

MESSAGGIO ATTUALE DEL CLIENTE:
"{user_query}"

REGOLE PER L'ESTRAZIONE DEGLI ELEMENTI (ElementoRichiesto):
1. Se il cliente chiede più domini (es. "3 salumi e 2 formaggi"), DEVI creare 2 elementi distinti.
2. `quantita`: se l'utente chiede "3 salumi", imposta quantita=3. Se chiede genericamente "dei salumi" o "che salumi hai", imposta quantita=None.
3. `dominio`: usa termini semplici e chiari ("salumi", "formaggi", "sottoli", "mare", "pane", "dispensa", "carne").
4. `reparto`: opzionale, usa valori come CARNE, DISPENSA, FORMAGGI, GELO, MARE, SALUMI.
5. `sottocategoria`: opzionale, se l'utente è specifico (es. "erborinato", "olive", "taralli").
6. `query_ricerca`: formula una query semantica utile a trovare i prodotti di quel dominio. Usa parole chiave espansive.
   - Esempio pub: "buns panini burger Farino maionese"
   - Esempio mare: "bresaola tonno salmone affumicato Italfish"
7. FRITTI E FINGER FOOD: Se il cliente chiede finger food, fritti, roba calda da rigenerare per aperitivi, DEVI USARE ASSOLUTAMENTE nella query_ricerca queste keyword magiche per trovarli nel database: "pastella frittelline pettole stick verdorate arancini crocchette di tria gelo". Altrimenti il database non troverà i nostri prodotti!
8. REGOLA CRUCIALE PER RICHIESTE MISTE NELLO STESSO DOMINIO ("di cui"):
   Se l'utente chiede "N prodotti di cui M con attributo specifico", DEVI CREARE DUE ElementoRichiesto SEPARATI nello stesso dominio:
   - Elemento 1: quantita=M, sottocategoria="attributo specifico" (es. "PARMA", "SAN DANIELE", "erborinato")
   - Elemento 2: quantita=N-M, sottocategoria=None (generico)
   Esempio: "3 formaggi di cui 1 erborinato" → Elemento(dominio="formaggi", quantita=1, sottocategoria="erborinato") + Elemento(dominio="formaggi", quantita=2, sottocategoria=None)
   Esempio: "2 prosciutti di cui 1 parma" → Elemento(dominio="salumi", quantita=1, sottocategoria="PARMA") + Elemento(dominio="salumi", quantita=1, sottocategoria=None)
   MAI accorpare attributi forzati con quantità miste in un solo elemento.


REGOLE PER IL TIPO DI RICHIESTA (tipo_richiesta):
- Imposta 'chiusura_ordine' SE E SOLO SE l'utente conferma ESPLICITAMENTE che vuole procedere all'ordine, ad esempio fornendo la Partita IVA, dicendo "ok procediamo con l'ordine", "confermo questi", "aggiungi tutto al carrello ed emetti fattura".
- Altrimenti usa 'panoramica_catalogo', 'ricerca_specifica', 'composizione_piatto' o 'conversazione_generica' a seconda del contesto.

REGOLE PER LE ESCLUSIONI:
- Se il cliente dice di NON volere un prodotto o ingrediente ("niente tonno", "non voglio il prosciutto cotto", "senza peperoncino"), mettilo in profilo.esclusioni (parola singola o breve). Non inserire esclusioni dedotte da te.
- Se il cliente dice che ORA vuole di nuovo qualcosa che aveva escluso ("adesso il tonno mi serve", "va bene anche il cotto"), mettilo in profilo.esclusioni_rimosse.

REGOLE PER ALLERGIE E PROFILO:
- Allergie o intolleranze dichiarate ("sono allergico alle noci", "ho clienti celiaci", "niente lattosio per allergia") -> profilo.allergie (es. "frutta a guscio", "glutine", "latte"). Solo se dette dal cliente.
- profilo.citta: SOLO la citta' in cui si trova il locale DEL CLIENTE, detta dal CLIENTE. Ignora le citta' citate da Nino (So Food ha sede a Bari: non e' la citta' del cliente).
- profilo.modalita_composizione: 'guidata' se il cliente vuole scegliere insieme, avere opzioni o procedere passo passo ("aiutami a scegliere", "fammi vedere le opzioni", "costruiamolo insieme"); 'completa' se vuole una proposta pronta o delega ("fai tu", "proponimi tu un tagliere completo", "decidi tu"). None se non lo dice.

REGOLE PER IL RIFERIMENTO PRECEDENTE:
- Se il cliente dice "dimmene altri", "ancora", o "altri" senza specificare cosa, devi capire dallo STORICO a cosa si riferisce e impostare `riferimento_precedente`=true e `argomento_riferito` al dominio di cui parlavate.

Rispondi rigorosamente con il JSON dello schema AnalisiUnificata.
"""
    try:
        ultimo_errore = None
        # modello principale; su quota/servizio non disponibile il fallback (prima si passava subito alle regole e
        # una richiesta d'ordine diventava "conversazione": Nino diceva "ho registrato" senza registrare nulla)
        for modello_analisi in dict.fromkeys([MODELLO_PRINCIPALE, MODELLO_FALLBACK]):
            try:
                risposta = client_genai.models.generate_content(
                    model=modello_analisi,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        response_mime_type="application/json",
                        response_schema=AnalisiUnificata,
                    ),
                )
                return AnalisiUnificata.model_validate_json(risposta.text)
            except Exception as e_mod:
                ultimo_errore = e_mod
                if not any(k in str(e_mod) for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")):
                    break
                print(f"[ATTENZIONE] Analisi con {modello_analisi} non disponibile ({breve(e_mod)})")
        raise ultimo_errore
    except Exception as e:
        print(f"[ATTENZIONE] Analisi unificata fallita ({breve(e)}), fallback euristico.")
        richiede_comp = any(p in user_query.lower() for p in ["tagliere", "tris", "aperitivo", "ciotoline", "menu", "menù", "burger", "hamburger"])
        
        import re
        numeri_liberi = [int(x) for x in re.findall(r'\b(\d+)\b', user_query) if 1 < int(x) <= 15]
        
        def estrai_q(kws, txt):
            # 1. Cerca prossimità stretta (numero prima o dopo la parola)
            for k in kws:
                m = re.search(r'(\d+)\s+(?:\w+\s+){0,2}' + k, txt)
                if m: 
                    num = int(m.group(1))
                    if num in numeri_liberi: numeri_liberi.remove(num)
                    return num
            for k in kws:
                m = re.search(k + r'\s+(?:\w+\s+){0,3}(\d+)', txt)
                if m: 
                    num = int(m.group(1))
                    if num in numeri_liberi: numeri_liberi.remove(num)
                    return num
            # 2. Assegnazione posizionale (consuma il primo numero libero disponibile)
            if numeri_liberi:
                return numeri_liberi.pop(0)
            return None

        elementi_fb = []
        q_lower = user_query.lower()
        domini_config = [
            ("salumi", ["salum", "prosciutt", "affettat", "coppa", "pancetta", "bresaola", "salam", "mortadella"], "salumi affettati prosciutti"),
            ("formaggi", ["formagg", "pecorino", "caciocavallo", "parmigiano", "mozzarella", "burrata"], "formaggi stagionati"),
            ("mare", ["mare", "pesce", "ittico", "salmone", "tonno", "gamber", "polpo"], "mare pesce ittico"),
            ("dispensa", ["sottoli", "sottaceti", "marmellat", "confettur", "miele", "crem", "pasta", "riso"], "marmellata conserve sottoli"),
            ("gelo", ["finger", "fritt", "cald", "rigenerare", "arancin", "crocchett", "snack", "tria"], "pastella frittelline pettole stick verdorate")
        ]

        m_dicui_tutti = list(re.finditer(r'di cui\s+(\d+)\s+([a-zA-Z][a-zA-Z\s]*?)(?:\s*[,;]|\s+e\s+|\s*$)', q_lower))
        elementi_fb = []
        
        for dominio, kws, default_query in domini_config:
            # Trova l'indice di prima comparsa del dominio
            matched_kw = next((w for w in kws if q_lower.find(w) != -1), None)
            
            if matched_kw:
                idx_dominio = q_lower.find(matched_kw)
                q_base = estrai_q(kws, q_lower)
                base_query = f"{dominio} {matched_kw} {default_query}"
                # Cerca il primo 'di cui' disponibile che sia successivo alla comparsa del dominio
                assegnato = False
                for m_dicui in list(m_dicui_tutti):
                    if m_dicui.start() > idx_dominio:
                        q_spec = int(m_dicui.group(1))
                        spec_text = m_dicui.group(2).strip()
                        elementi_fb.append(ElementoRichiesto(dominio=dominio, query_ricerca=f"{dominio} {spec_text}", quantita=q_spec, sottocategoria=spec_text.upper()))
                        if q_base is not None and q_base > q_spec:
                            elementi_fb.append(ElementoRichiesto(dominio=dominio, query_ricerca=base_query, quantita=q_base - q_spec))
                        m_dicui_tutti.remove(m_dicui)
                        assegnato = True
                        break
                if not assegnato:
                    elementi_fb.append(ElementoRichiesto(dominio=dominio, query_ricerca=base_query, quantita=q_base))
        
        if not elementi_fb:
            elementi_fb = [ElementoRichiesto(dominio="generale", quantita=None, query_ricerca=user_query)]
        vuole_ordinare = bool(re.search(r"\b(ordinare|ordino|procediamo con l.ordine|p\.?\s?iva|partita iva)\b", q_lower)
                              or re.search(r"\b\d{11}\b", user_query))
        citta_fb = next((w for w in re.findall(r"[a-zà-ù']+", q_lower) if len(w) >= 4 and logistica.zona_cliente(w) != "sconosciuta"
                         and w not in ("puglia", "basilicata")), None)
        from core.profilazione_locale import TIPI_LOCALE_HORECA, TIPI_LOCALE_RETAIL
        locale_fb = next((k for k in TIPI_LOCALE_HORECA + TIPI_LOCALE_RETAIL if re.search(r"\b" + re.escape(k) + r"\b", q_lower)
                          and k not in ("cucina", "chef")), None)
        escl_fb = [m.strip() for m in re.findall(r"\b(?:niente|senza)\s+([a-zà-ù]+(?:\s+[a-zà-ù]+)?)", q_lower)
                   if m.split()[0] not in ("glutine", "lattosio", "problemi", "fretta", "dubbio")][:3]
        return AnalisiUnificata(
            tipo_richiesta="chiusura_ordine" if vuole_ordinare else ("conversazione_generica" if not richiede_comp else "composizione_piatto"),
            richiede_composizione=richiede_comp,
            riferimento_precedente=False,
            argomento_riferito=None,
            elementi_richiesti=elementi_fb,
            # anche senza modello le allergie dichiarate non vanno perse (vincolo di sicurezza)
            profilo=ProfiloClienteAggiornato(allergie=sorted(allergeni.categorie_da_testo(user_query))
                                             if re.search(r"allergi|celiac|intolleran", user_query, re.IGNORECASE) else [],
                                             citta=citta_fb.title() if citta_fb else None, tipo_locale=locale_fb,
                                             esclusioni=[e.split()[0] for e in escl_fb])
        )


def _nome_e_produttore(rec: dict) -> str:
    meta = rec.get("metadata", {}) or {}
    fornitore = nome_breve(meta.get("nome_fornitore", "") or "", rec.get("document", ""))
    prima_linea = prima_riga(rec.get("document", "")) if rec.get("document") else ""
    nome = pulisci_nome_commerciale(prima_linea, fornitore) if prima_linea else str(rec.get("id"))
    return nome + (f" (Produttore: {fornitore})" if fornitore else "")


def riga_formato(meta: dict, doc: str) -> str:
    """'Formato: 1.5 kg (pezzatura HORECA)' dal parser deterministico (stessa riga del contesto prodotti)."""
    from core.parse_formato import formato_prodotto
    f = formato_prodotto(meta, doc)
    if f.get("valore") is None:
        return ""
    g = f["valore"]
    txt = f"{g / 1000:g} {'kg' if f['unita'] == 'g' else 'L'}" if g >= 1000 else f"{g:g} {f['unita']}"
    return f"Formato: {txt} (pezzatura {f['canale_formato'].upper()})" + (" - peso variabile" if f.get("peso_variabile") else "")


def aggiungi_opzioni_b(contesto_ricetta: dict, stato: dict) -> None:
    """Per la modalita' GUIDATA (e per offrire sostituzioni in quella completa): per ogni prodotto scelto, un'opzione B
    della stessa tipologia, verificata dal second brain con gli stessi vincoli del cliente (dieta, esclusioni,
    allergie, zona, canale) e mai gia' presente nella proposta. Nessuna chiamata API."""
    usati = {s["prodotto_trovato"]["id"] for s in contesto_ricetta.get("slot", []) if s.get("prodotto_trovato")}
    cat = str(contesto_ricetta.get("template", {}).get("categoria") or "").lower()
    # in un piatto i prodotti "solo ingrediente" (passata, trita...) sono opzioni valide; in tagliere e tris no
    ingredienti_ok = cat not in ("tagliere", "aperitivo", "antipasto")
    for s in contesto_ricetta.get("slot", []):
        p = s.get("prodotto_trovato")
        if s.get("esito") != "TROVATO" or not p:
            continue
        if s.get("opzione_b"):
            usati.add(s["opzione_b"]["id"])  # proposta riusata: l'opzione B resta quella gia' mostrata
            continue
        alt = second_brain.BRAIN.alternative(p, 8, dieta=stato.get("filtro_dieta") or None, esclusi=usati,
                                             canale=stato.get("canale_locale") or None, ammetti_ingredienti=ingredienti_ok)
        nomi_proposta = {_chiave_nome(x["prodotto_trovato"]) for x in contesto_ricetta.get("slot", []) if x.get("prodotto_trovato")}
        # stessa tipologia ma NON lo stesso prodotto con un altro codice/formato (chat reale: B della Culatta = la Culatta)
        alt = [a for a in alt if _stessa_tipologia(p, a) and _chiave_nome(a) not in nomi_proposta and not _stesso_prodotto(p, a)]
        if alt:
            s["opzione_b"] = alt[0]
            usati.add(alt[0]["id"])


def _chiave_nome(rec: dict) -> str:
    """Nome del prodotto senza pesi, formati e produttore: due record con la stessa chiave sono lo stesso prodotto."""
    nome = pulisci_nome_commerciale(prima_riga(rec.get("document", "")), (rec.get("metadata") or {}).get("nome_fornitore", ""))
    return re.sub(r"[^a-z]+", " ", nome.lower()).strip()


def _stesso_prodotto(a: dict, b: dict) -> bool:
    """Stesso produttore e un nome contenuto nell'altro: e' lo stesso prodotto in un'altra pezzatura
    (chat reale: "Finocchiona IGP Gigante" con alternativa "Finocchiona IGP")."""
    if str(a["metadata"].get("nome_fornitore") or "").lower() != str(b["metadata"].get("nome_fornitore") or "").lower():
        return False
    pa, pb = set(_chiave_nome(a).split()), set(_chiave_nome(b).split())
    return bool(pa and pb) and (pa <= pb or pb <= pa)


def _stessa_tipologia(a: dict, b: dict) -> bool:
    """L'opzione B deve poter sostituire il prodotto: stessa famiglia da tagliere (crudo con crudo, erborinato con
    erborinato) o, per gli altri prodotti, stessa prima parola del tipo ("spaghetti" ~ "spaghettini", "pasta")."""
    from core import famiglie_tagliere as ft
    fa = ft.famiglia_prodotto(a["metadata"], a.get("document", ""))
    fb = ft.famiglia_prodotto(b["metadata"], b.get("document", ""))
    if fa or fb:
        return fa == fb
    ta = str(a["metadata"].get("tipo_prodotto") or "").lower().split()
    tb = str(b["metadata"].get("tipo_prodotto") or "").lower().split()
    if not ta or not tb:
        return str(a["metadata"].get("sottocategoria")) == str(b["metadata"].get("sottocategoria"))
    return ta[0][:5] == tb[0][:5] or str(a["metadata"].get("sottocategoria")) == str(b["metadata"].get("sottocategoria"))


def istruzioni_modalita(modalita: str) -> str:
    """Come presentare la proposta composta (D5: Nino deve saper fare entrambe le cose)."""
    if modalita == "guidata":
        return ("[MODALITA' GUIDATA] Il cliente vuole scegliere: racconta in breve l'idea della proposta e, per ogni "
                "elemento, offri le due strade con parole naturali (il prodotto indicato oppure quello IN ALTERNATIVA, es. "
                "\"per il crudo puoi andare sul Parma di BBS oppure, se preferisci, su quello di Pellizzari\"). Se un "
                "elemento non ha alternativa presentalo e basta. Non elencare altri prodotti. Chiudi chiedendo cosa "
                "preferisce; quando ha scelto, riepiloga la composizione finale.")
    return ("[MODALITA' COMPLETA] Presenta la proposta pronta, spiegando in breve perche' funziona. Se utile accenna a UNA "
            "sola sostituzione possibile (un prodotto IN ALTERNATIVA) con parole naturali. Chiudi con una domanda breve "
            "e concreta; puoi offrire di costruirla insieme, ma con parole tue e non sempre con la stessa formula.")


def costruisci_contesto_ricetta_testuale(contesto_ricetta: dict) -> str:
    """Formatta la proposta composta da ricettario per il contesto RAG."""
    template = contesto_ricetta["template"]
    righe = [
        f"PROPOSTA COMPOSTA: {template['nome_piatto']} ({template['categoria']})",
        f"Note di composizione: {template['note_composizione']}" if template.get("note_composizione") else "",
        "",
    ]
    for s in contesto_ricetta["slot"]:
        if s.get("esito") == "TROVATO":
            doc = s["prodotto_trovato"].get("document", "")
            meta = s["prodotto_trovato"].get("metadata", {})
            righe.append(f"- {s['ingrediente_richiesto']}: {_nome_e_produttore(s['prodotto_trovato'])} (MATCH REALE A CATALOGO)")
            from core import famiglie_tagliere as _ft
            _fam = _ft.famiglia_prodotto(meta, doc)
            if _fam:
                _latte = _ft.latte(meta, doc)
                righe.append(f"  Tipologia verificata: {'salume di muscolo intero' if _fam == 'muscolo_stagionato' else _fam.replace('_', ' ')}" + (f", latte {_latte}" if _latte else ""))
            _f = riga_formato(meta, doc)
            if _f:
                righe.append(f"  {_f}")
            for _riga_extra in ontologia.righe_extra_contesto(meta):
                righe.append(f"  {_riga_extra}")
            if s.get("opzione_b"):
                righe.append(f"  IN ALTERNATIVA (stessa tipologia, verificata): {_nome_e_produttore(s['opzione_b'])}")
            righe.append(f"  {riga_foto(meta)}")
        elif s.get("esito") == "SOSTITUITO":
            righe.append(f"- {s['ingrediente_richiesto']}: NON A CATALOGO, sostituito con {s.get('sostituto_nome', 'prodotto alternativo')}")
        elif s.get("esito") == "NON_TROVATO":
            if s.get("alternativa"):
                righe.append(f"- {s['ingrediente_richiesto']}: NON A CATALOGO. ALTERNATIVA VERIFICATA a catalogo (presentala "
                             f"dichiarando che e' un'alternativa): {_nome_e_produttore(s['alternativa'])}")
                for _riga_extra in ontologia.righe_extra_contesto(s["alternativa"].get("metadata", {})):
                    righe.append(f"  {_riga_extra}")
            else:
                righe.append(f"- {s['ingrediente_richiesto']}: NON A CATALOGO e nessuna alternativa verificata: dillo al cliente "
                             "(l'ingrediente va reperito altrove), NON proporre prodotti che non sono in questo elenco")
        elif s.get("esito") == "OMESSO":
            righe.append(f"- {s['ingrediente_richiesto']}: NON A CATALOGO, nessun sostituto valido — omettere dalla proposta")
        if s.get("note_ingrediente"):
            # nota del TEMPLATE (generica): prima il modello la attribuiva al prodotto ("Caprea di Capra: formaggio a
            # pasta dura o semidura", chat reale)
            righe.append(f"  (ruolo nella ricetta, NON e' una caratteristica del prodotto: {s['note_ingrediente']})")
    return "\n".join(r for r in righe if r)


PAROLE_PRODOTTO_SAFETY = [
    "taralli", "tarallo", "grissini", "grissino",
    "olive", "oliva", "nocciole", "nocciola", "anacardi", "anacardo", "arachidi", "arachide",
    "focaccia", "friselle", "friselline", "pane", "carasau",
    "parmigiano", "grana", "pecorino", "ricotta", "gorgonzola", "taleggio", "fontina", "asiago", "provola", "scamorza", "stracchino",
    "burrata", "stracciatella", "mozzarella", "caciocavallo",
    "salame", "capocollo", "guanciale", "pancetta", "prosciutto", "lardo", "culatello", "carne salada", "cecina", "jamon", "bellota",
    "tonno", "salmone", "bottarga", "polpo", "baccala", "baccalà", "alici", "acciughe", "spada", "pesce spada", "ricci", "riccio",
    "capperi", "cappero", "cucunci",
    "ketchup", "burger", "hamburger", "trita", "fassona",
    "pomodorini", "datterini", "passata", "pelati", "sugo", "sughi", "pesto", "ragù", "ragu",
    "pasta", "spaghetti", "rigatoni", "orecchiette", "paccheri", "calamarata", "linguine", "fusilli", "risotto", "riso", "fresca", "fresche",
    "legumi", "ceci", "lenticchie", "fagioli", "vegano", "vegana", "vegetariano", "vegetariana",
    "colatura", "cantucci", "cantuccino", "cantuccini", "limoncello", "uova", "uovo", "spalmabile", "spalmabili", "crucoloso",
    "carnaroli", "acquerello", "burro",
    "marmellata", "marmellate", "confettura", "confetture", "mostarda", "mostarde", "composta", "composte", "cugnà", "cugna",
    "dolce", "dolci", "dessert", "fine pasto", "tiramisù", "tiramisu", "cremoso", "cremosi", "gelato", "gelati", "sorbetto", "sorbetti", "babà", "baba",
    "birra", "birre", "vino"
]


# Alias parola-chiave -> sottocategoria/e ufficiali della tassonomia (vedi
# tassonomia_sofood.py). NIENTE nomi di fornitore qui dentro: questa mappa
# lega solo linguaggio naturale del cliente a categorie merceologiche
# ufficiali, quindi resta valida qualunque sia il fornitore che oggi (o in
# futuro) copre quella categoria a catalogo. Se una parola ha più di una
# sottocategoria plausibile, si prova la prima e poi le altre finché non si
# trova qualcosa.
ALIAS_PAROLA_SOTTOCATEGORIA = {
    "taralli": ["TARALLI"], "tarallo": ["TARALLI"],
    "grissini": ["GRISSINI"], "grissino": ["GRISSINI"],
    "olive": ["OLIVE"], "oliva": ["OLIVE"],
    "capperi": ["SOTTACETI", "ALTRI CONDIMENTI"], "cappero": ["SOTTACETI", "ALTRI CONDIMENTI"], "cucunci": ["SOTTACETI"],
    "burger": ["HAMBURGER"], "hamburger": ["HAMBURGER"], "trita": ["HAMBURGER", "MACINATO"], "macinato": ["MACINATO"],
    "sugo": ["SUGHI PRONTI E BASI"], "sughi": ["SUGHI PRONTI E BASI"],
    "pesto": ["SALSE/SPALMABILI VEGETALI", "SUGHI PRONTI E BASI"], "ragù": ["SUGHI PRONTI E BASI"], "ragu": ["SUGHI PRONTI E BASI"],
    "maionese": ["MAIONESE"],
    "cantucci": ["BISCOTTI TRADIZIONALI"], "cantuccino": ["BISCOTTI TRADIZIONALI"], "cantuccini": ["BISCOTTI TRADIZIONALI"],
    "biscotti": ["BISCOTTI TRADIZIONALI", "BISCOTTI ALL'UOVO"],
    "limoncello": ["ALTRI LIQUORI"], "liquore": ["ALTRI LIQUORI"], "liquori": ["ALTRI LIQUORI"],
    "spalmabile": ["CREME SPALMABILI DOLCI", "PATE' E SPALMABILI SALATI", "SALSE/SPALMABILI VEGETALI"],
    "spalmabili": ["CREME SPALMABILI DOLCI", "PATE' E SPALMABILI SALATI", "SALSE/SPALMABILI VEGETALI"],
    "crucoloso": ["CREME SPALMABILI DOLCI", "PATE' E SPALMABILI SALATI"],
    "riso": ["RISO BIANCO", "RISO PARBOILED", "SPECIALITA' RISO"], "risotto": ["RISO BIANCO"], "carnaroli": ["RISO BIANCO"],
    "marmellata": ["CONFETTURE/SPALMABILI FRUTTA"], "marmellate": ["CONFETTURE/SPALMABILI FRUTTA"],
    "confettura": ["CONFETTURE/SPALMABILI FRUTTA"], "confetture": ["CONFETTURE/SPALMABILI FRUTTA"],
    "mostarda": ["MOSTARDA"], "mostarde": ["MOSTARDA"], "composta": ["MOSTARDA"], "composte": ["MOSTARDA"],
    "cugnà": ["MOSTARDA"], "cugna": ["MOSTARDA"], "miele": ["MIELE"],
    "legumi": ["ALTRI LEGUMI/VEGETALI/CEREALI", "FAGIOLI CONSERVATI"], "ceci": ["ALTRI LEGUMI/VEGETALI/CEREALI"],
    "lenticchie": ["ALTRI LEGUMI/VEGETALI/CEREALI"], "fagioli": ["FAGIOLI CONSERVATI", "ALTRI LEGUMI/VEGETALI/CEREALI"],
    "dolce": ["PASTICCERIA", "CREME SPALMABILI DOLCI", "SNACK DOLCI", "ALTRI PRODOTTI RICORRENZA"],
    "dolci": ["PASTICCERIA", "CREME SPALMABILI DOLCI", "SNACK DOLCI", "ALTRI PRODOTTI RICORRENZA"],
    "dessert": ["GELATI DESSERT", "PASTICCERIA"],
    "gelato": ["GELATI VASCHETTE", "GELATI DESSERT"], "gelati": ["GELATI VASCHETTE", "GELATI DESSERT"],
    "sorbetto": ["SORBETTO DA BERE", "GELATI VASCHETTE"], "sorbetti": ["SORBETTO DA BERE"],
    "birra": ["BIRRE ALCOLICHE"], "birre": ["BIRRE ALCOLICHE"],
    "pomodorini": ["PELATI E POMODORINI"], "datterini": ["PELATI E POMODORINI"],
    "passata": ["PASSATA DI POMODORO"], "pelati": ["PELATI E POMODORINI"], "polpa": ["POLPA DI POMODORO"],
    "pasta": ["PASTA DI SEMOLA", "PASTA ALL'UOVO", "PASTA INT/FAR/KAMUT/LEG/MAIS"],
    "salmone": ["SALMONE FRESCO CONFEZIONATO"],
    "parmigiano": ["GRANA E SIMILI"], "grana": ["GRANA E SIMILI"], "pecorino": ["PECORINO"],
    "ricotta": ["RICOTTA"], "gorgonzola": ["GORGONZOLA"],
    "mozzarella": ["MOZZARELLE", "BUFALA"], "bufala": ["BUFALA"],
    "salame": ["SALAME", "SALAMI"], "mortadella": ["MORTADELLA"], "pancetta": ["PANCETTA"],
    "prosciutto": ["PROSC CRUDO", "PROSC COTTO", "PROSCIUTTO"], "bresaola": ["BRESAOLA"],
    "focaccia": ["SPECIALITA' MORBIDE"], "friselle": ["SPECIALITA' CROCCANTI"],
    "pane": ["SPECIALITA' MORBIDE", "SPECIALITA' CROCCANTI", "PANINI"], "piadine": ["PIADINE"], "panini": ["PANINI"],
    "nocciole": ["FRUTTA SECCA SENZA GUSCIO"], "nocciola": ["FRUTTA SECCA SENZA GUSCIO"],
    "anacardi": ["FRUTTA SECCA SENZA GUSCIO"], "arachidi": ["FRUTTA SECCA SENZA GUSCIO"],
    "patatine": ["PATATINE"], "aceto": ["ACETO"], "olio": ["OLIO EXTRAVERGINE DI OLIVA", "OLIO DI SEMI"],
}

_ESCLUSIONI_QUALITA_SAFETY = {
    "OLIVE": ("sugo", "paté", "pate", "candit", "granell", "pastella", "crema", "carciof"),
}


def esegui_safety_net_prodotti(user_query: str, contesto_testuale: str, collezione, indice_codici: dict, embedder, indice_fornitori: dict, indice_testuale: list = None) -> str:
    """Se l'utente cita esplicitamente un prodotto/brand a catalogo, o una
    categoria merceologica (taralli, olive, sugo...), ma il contesto RAG non
    ne contiene traccia, recupera referenze mirate per evitare che Nino
    neghi falsamente la disponibilità di qualcosa che è invece a catalogo.

    A differenza della versione precedente, qui NON compaiono nomi di
    fornitore hardcoded per ciascuna parola: la ricerca è vincolata dalla
    sottocategoria ufficiale (ALIAS_PAROLA_SOTTOCATEGORIA, sopra), che è
    l'unica fonte di verità su "quale categoria" — "quale fornitore la copre
    oggi" lo decide sempre e solo la ricerca a catalogo. Aggiungere o
    togliere un fornitore da una categoria non richiede toccare questa
    funzione."""
    query_lower = user_query.lower()
    contesto_lower = contesto_testuale.lower()

    prodotti_aggiuntivi = []
    id_gia_inclusi = set()

    # 1. Fornitori/brand a catalogo citati esplicitamente nella query
    if indice_fornitori:
        # fornitori riconosciuti con gli alias calcolati dal catalogo ("masciarelli", "italfish", "la valletta"...)
        for forn_chiave in ANAGRAFICA.fornitori_in_testo(user_query):
            if forn_chiave in indice_fornitori and forn_chiave not in contesto_lower:
                for r in trova_match_per_fornitore(forn_chiave, indice_fornitori, collezione, max_risultati=4):
                    if r["id"] not in id_gia_inclusi:
                        id_gia_inclusi.add(r["id"])
                        prodotti_aggiuntivi.append(r)

    # 2. Categorie merceologiche citate ma assenti dal contesto
    for parola in PAROLE_PRODOTTO_SAFETY:
        if not re.search(r"\b" + re.escape(parola) + r"\b", query_lower):
            continue

        sottocategorie_candidate = ALIAS_PAROLA_SOTTOCATEGORIA.get(parola)
        manca_nel_contesto = parola not in contesto_lower and not (
            sottocategorie_candidate and any(sc.lower() in contesto_lower for sc in sottocategorie_candidate)
        )
        if not manca_nel_contesto:
            continue

        trovati_per_parola = []
        if sottocategorie_candidate:
            for sc in sottocategorie_candidate:
                extra = cerca_prodotti(
                    collezione, indice_codici, embedder, parola,
                    n_risultati=4, indice_fornitori=indice_fornitori,
                    filtro_sottocategoria=sc,
                )
                esclusioni = _ESCLUSIONI_QUALITA_SAFETY.get(sc, ())
                for r in extra:
                    doc_p = prima_riga(r["document"]).lower() if r.get("document") else ""
                    if esclusioni and any(w in doc_p for w in esclusioni):
                        continue
                    trovati_per_parola.append(r)
                if trovati_per_parola:
                    break  # la prima sottocategoria che produce risultati basta

        if not trovati_per_parola and indice_testuale:
            trovati_per_parola = trova_match_lessicale(parola, indice_testuale, max_risultati=3)

        if not trovati_per_parola:
            trovati_per_parola = cerca_prodotti(
                collezione, indice_codici, embedder, parola, n_risultati=3, indice_fornitori=indice_fornitori
            )

        for r in trovati_per_parola:
            if r["id"] not in id_gia_inclusi:
                id_gia_inclusi.add(r["id"])
                prodotti_aggiuntivi.append(r)

        # Caso "vegano/vegetariano": oltre alla parola stessa, garantisci
        # sempre almeno qualche alternativa proteica/verdura strutturalmente
        # marcata SI nei campi dietetici (non per nome di fornitore).
        if parola in ("vegano", "vegana", "vegetariano", "vegetariana"):
            extra_dieta = cerca_prodotti(
                collezione, indice_codici, embedder, "legumi e verdure pronte per secondi piatti",
                n_risultati=4, indice_fornitori=indice_fornitori,
                filtro_dieta="vegano" if "vegan" in parola else "vegetariano",
            )
            for r in extra_dieta:
                if r["id"] not in id_gia_inclusi:
                    id_gia_inclusi.add(r["id"])
                    prodotti_aggiuntivi.append(r)

    # Gli stessi vincoli del cliente (dieta, esclusioni) valgono anche per i prodotti della safety-net
    _dieta_c = _ru_ctx._DIETA_CORRENTE.get()
    _prima_filtro = prodotti_aggiuntivi
    prodotti_aggiuntivi = [
        r for r in prodotti_aggiuntivi
        if _ru_ctx.prodotto_compatibile_con_dieta(r["metadata"], _dieta_c)
        and not ontologia.prodotto_escluso_da_cliente(r["metadata"], r.get("document", ""))
    ]

    if not prodotti_aggiuntivi:
        if _prima_filtro:
            # Il produttore c'e' ma i suoi prodotti non rispettano i vincoli del cliente: non va detto "non a catalogo"
            _forn = sorted({str(r["metadata"].get("nome_fornitore") or "") for r in _prima_filtro} - {""})
            motivo = f"dieta {_dieta_c}" if _dieta_c else "esclusioni del cliente"
            return contesto_testuale + (
                f"\n\n[PRODUTTORE PRESENTE A CATALOGO: {', '.join(_forn)}] I suoi prodotti trovati NON rispettano il vincolo del cliente "
                f"({motivo}). NON dire che il produttore non e' a catalogo: spiega che i suoi prodotti non sono adatti al vincolo e proponi un'alternativa adatta.")
        return contesto_testuale

    blocco_extra = "\n\n[PRODOTTI SPECIFICI RICHIESTI DALL'UTENTE — PRESENTI A CATALOGO SO FOOD (NON NEGARE LA DISPONIBILITÀ!)]\n"
    for r in prodotti_aggiuntivi:
        meta = r["metadata"]
        prima_linea = prima_riga(r["document"]) if r.get("document") else ""
        nome_pulito = pulisci_nome_commerciale(prima_linea, meta.get("nome_fornitore", ""))
        blocco_extra += f"- Prodotto: {nome_pulito} (Produttore: {nome_breve(meta.get('nome_fornitore'), r.get('document', ''))}, Codice: {meta.get('codice_prodotto')}, Categoria: {meta.get('categoria_prodotto')})\n"
        for _riga_extra in ontologia.righe_extra_contesto(meta):
            blocco_extra += f"  {_riga_extra}\n"
        blocco_extra += f"  {riga_foto(meta)}\n"

    return contesto_testuale + blocco_extra


def elabora_messaggio_nino(user_query: str, stato: dict, sid: str) -> dict:
    """Versione sincrona che consuma il generatore stream per retrocompatibilita'."""
    testo_completo, finale = "", None
    for sse_msg in stream_messaggio_nino(user_query, stato, sid):
        if sse_msg.startswith("data: "):
            try:
                payload = json.loads(sse_msg[6:].strip())
                if "final" in payload:
                    finale = payload["final"]  # testo gia' ripulito dai guardrail (righe non verificate tolte)
                elif "chunk" in payload:
                    testo_completo += payload["chunk"]
                elif "error" in payload:
                    print(f"[ERRORE] {payload['error']}")
                    testo_completo += "\n\n" + MSG_ERRORE_CLIENTE
            except Exception as e:
                print("Errore JSON parse in elabora_messaggio_nino:", e)
    if finale is not None:
        return {"reply": finale}
    # Applica lo stesso post-processing fatto nello storico per pulire il testo finale
    tc = testo_completo
    tc = pulisci_markdown(tc)  # core/formato_testo.py
    return {"reply": tc}

MSG_ERRORE_CLIENTE = "Mi scuso, ho avuto un problema tecnico nel rispondere. Puoi riscrivermi la richiesta tra qualche istante?"


def sse_chunk(testo: str) -> str:
    """Un evento SSE con un blocco di testo."""
    return "data: " + json.dumps({"chunk": testo}) + "\n\n"


def _messaggio_fisso(stato: dict, sid: str, testo: str, etichetta: str):
    """Risposta deterministica (ordini, conferme...): va nello storico e nella sessione come quelle del modello,
    cosi' al turno dopo il modello sa che cosa Nino ha chiesto (prima la domanda sulla P.IVA spariva)."""
    stato["storico"].append(types.Content(role="model", parts=[types.Part.from_text(text=testo)]))
    stato["log_chat"].append({"ruolo": "nino", "testo": testo, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    stato["contatore_messaggi"] = stato.get("contatore_messaggi", 0) + 1
    salva_log_chat(sid, stato)
    sessioni_store.salva(sid, stato)
    audit_log.nota("risposta_fissa", etichetta)
    audit_log.chiudi(stato, testo)
    yield sse_chunk(testo)
    yield 'data: {"final": ' + json.dumps(testo) + '}\n\n'
    yield 'data: {"done": true}\n\n'


def _conversazione_per_ordine(stato: dict) -> list:
    return [{"ruolo": r.get("ruolo"), "testo": r.get("testo"), "ora": r.get("timestamp")} for r in stato.get("log_chat", [])]


def _imposta_vincoli_turno(stato: dict) -> None:
    """Tutti i vincoli del cliente valgono per OGNI ricerca del turno. Si impostano all'inizio e sempre: con un
    server a thread riusati un ramo anticipato non deve leggere i valori del turno (o del cliente) precedente."""
    ontologia.ESCLUSIONI_CORRENTI.set(list(stato.get("esclusioni_cliente") or []))
    allergeni.ALLERGIE_CORRENTI.set(list(stato.get("allergie") or []))
    logistica.ZONA_CORRENTE.set(stato.get("zona_consegna"))
    _ru_ctx._DIETA_CORRENTE.set(stato.get("filtro_dieta"))
    _ru_ctx._CANALE_CORRENTE.set(stato.get("canale_locale"))
    _ru_ctx._QUERY_ORIGINALE.set(None)


def _aggiorna_profilo(stato: dict, profilo) -> None:
    if profilo.tipo_locale:
        stato["tipo_locale"] = profilo.tipo_locale.lower()
    if profilo.stile_cucina:
        stato["stile_cucina"] = profilo.stile_cucina.lower()
    if profilo.dieta_filtro:
        dieta_raw = profilo.dieta_filtro.lower()
        if "vegan" in dieta_raw:
            stato["filtro_dieta"] = "vegano"
        elif "vegetariano" in dieta_raw or "vegetariana" in dieta_raw:
            stato["filtro_dieta"] = "vegetariano"
        elif "glutin" in dieta_raw or "celiac" in dieta_raw:
            stato["filtro_dieta"] = "senza_glutine"
        elif "lattosi" in dieta_raw:
            stato["filtro_dieta"] = "senza_lattosio"
        else:
            stato["filtro_dieta"] = None
    if profilo.senza_affettatrice:
        stato["senza_affettatrice"] = True
    if profilo.citta:
        stato["citta"] = profilo.citta
    # Esclusioni del cliente: persistono per tutta la sessione e valgono per ogni ricerca (core/ontologia.py);
    # si tolgono solo se il cliente dice che ora le vuole di nuovo
    escl_cliente = stato.setdefault("esclusioni_cliente", [])
    rimosse = {str(x).strip().lower() for x in (getattr(profilo, "esclusioni_rimosse", None) or []) if str(x).strip()}
    if rimosse:
        stato["esclusioni_cliente"] = escl_cliente = [e for e in escl_cliente
                                                       if not any(r in e or e in r for r in rimosse)]
    for _e in getattr(profilo, "esclusioni", None) or []:
        _e = str(_e).strip().lower()
        if _e and _e not in escl_cliente and _e not in rimosse and len(escl_cliente) < 20:
            escl_cliente.append(_e)
    # Allergie: categorie dei 14 allergeni UE (core/allergeni.py), vincolo permanente
    nuove = set()
    for a in getattr(profilo, "allergie", None) or []:
        nuove |= allergeni.categorie_da_testo(str(a))
    if nuove:
        stato["allergie"] = sorted(set(stato.get("allergie") or []) | nuove)
    mod = (getattr(profilo, "modalita_composizione", None) or "").strip().lower()
    if mod in ("guidata", "completa"):
        stato["modalita_composizione"] = mod
    stato["zona_consegna"] = logistica.zona_cliente(stato.get("citta"))


_RE_GUIDATA = re.compile(r"\b(scegliamo insieme|costruiamolo insieme|aiutami a scegliere|passo passo|fammi scegliere|"
                         r"dammi (delle |qualche )?opzioni|che opzioni|quali opzioni|voglio scegliere io)\b", re.IGNORECASE)
_RE_COMPLETA = re.compile(r"\b(fai tu|decidi tu|scegli tu|fammi tu|proposta completa|proponimi tu|componi tu|"
                          r"pensaci tu|vai tu)\b", re.IGNORECASE)


def modalita_composizione(user_query: str, stato: dict) -> str:
    """'guidata' (opzioni A/B per componente, il cliente sceglie) o 'completa' (proposta pronta, sostituzioni su richiesta).
    Le parole esplicite del messaggio vincono; poi la preferenza memorizzata; di default 'completa'."""
    if _RE_COMPLETA.search(user_query or ""):
        stato["modalita_composizione"] = "completa"
    elif _RE_GUIDATA.search(user_query or ""):
        stato["modalita_composizione"] = "guidata"
    return stato.get("modalita_composizione") or "completa"


# Parole che indicano una portata/composizione: se il messaggio ne nomina una DIVERSA dalla proposta attiva,
# e' una richiesta nuova, non la gestione di quella attiva
_PORTATE_PAROLE = {
    "aperitivo": ("tris", "aperitivo", "stuzzichini", "ciotoline"), "tagliere": ("tagliere", "taglieri"),
    "primo": ("primo", "pasta", "spaghetti", "risotto"), "secondo": ("secondo", "carne", "pesce al forno"),
    "pizza": ("pizza", "pinsa", "focaccia"), "panino": ("panino", "burger", "hamburger"),
    "dolce": ("dolce", "dessert"), "antipasto": ("antipasto", "antipasti"), "contorno": ("contorno",),
    "menu": ("menu", "menù"),
}
_RE_GESTIONE_PROPOSTA = re.compile(
    r"\b(sostitu\w*|cambia\w*|metti|mettimi|togli\w*|al posto|opzione b|opzioni|scelgo|preferisco|vada per|prendo|"
    r"intendevo|intendo|lo stesso|la stessa|facciamol[oa]|costruiamol[oa]|insieme|passo passo)\b",
    re.IGNORECASE)


# radici (5 lettere) di parole che servono a riferirsi a un prodotto, non a nominarlo
_PAROLE_ANAFORA = {w[:5] for w in ("vedere", "questa", "questo", "queste", "questi", "quella", "quello", "quelle", "quelli",
                                   "parli", "immagine", "foto", "fammi", "fammelo", "mostrami", "prodotto", "dello", "della",
                                   "detto", "proposto", "consigliato", "citato", "anche", "ancora", "come", "fatto",
                                   "aspetto", "sicuro", "conosco", "capito", "dimmi", "parlami", "info", "informazioni")}


def messaggio_su_proposta_attiva(testo: str, piatto: dict) -> bool:
    """True se il messaggio lavora sulla proposta appena fatta (cambio di modalita', sostituzioni, scelte,
    riferimenti) e non chiede una portata diversa. Regola generale, non legata a un piatto."""
    t = (testo or "").lower()
    if not (_RE_GESTIONE_PROPOSTA.search(t) or _RE_GUIDATA.search(t) or _RE_COMPLETA.search(t)):
        return False
    cat = str(piatto.get("categoria") or "").lower()
    nome = str(piatto.get("nome_piatto") or "").lower()
    mie = set(_PORTATE_PAROLE.get(cat, ())) | {w for ws in _PORTATE_PAROLE.values() for w in ws if w in nome}
    altre = {w for c, ws in _PORTATE_PAROLE.items() if c != cat for w in ws} - mie
    return not any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in altre)


def ricostruisci_proposta(piatto: dict) -> "dict | None":
    """Proposta attiva ricostruita dagli id salvati in sessione (stessi prodotti, opzioni B e alternative)."""
    per_id = {p["id"]: p for p in indice_testuale}
    slot = []
    for s in piatto.get("slot") or []:
        rec = {k: s.get(k) for k in ("ingrediente_richiesto", "esito", "ruolo", "note_ingrediente")}
        for campo, chiave in (("prodotto_trovato", "prodotto_id"), ("opzione_b", "opzione_b_id"), ("alternativa", "alternativa_id")):
            if s.get(chiave) in per_id:
                rec[campo] = per_id[s[chiave]]
        if rec.get("esito") == "TROVATO" and not rec.get("prodotto_trovato"):
            rec["esito"] = "NON_TROVATO"  # prodotto uscito dal catalogo nel frattempo
        slot.append(rec)
    if not slot:
        return None
    return {"template": {"id_ricetta": piatto.get("id_ricetta"), "nome_piatto": piatto.get("nome_piatto"),
                         "categoria": piatto.get("categoria"), "note_composizione": piatto.get("note_composizione", "")},
            "slot": slot}


def stream_messaggio_nino(user_query: str, stato: dict, sid: str) :
    """Esegue l'elaborazione RAG e la generazione della risposta di Nino."""
    user_query = sicurezza.pulisci_input(user_query)   # niente caratteri di controllo, max 2000 caratteri
    injection = sicurezza.tentativo_injection(user_query)
    user_query_clean = re.sub(r'\b(hamburge|hamburgher|amburgher)\b', 'hamburger', user_query, flags=re.IGNORECASE)
    user_query_clean = re.sub(r'\b(kechup|chechup)\b', 'ketchup', user_query_clean, flags=re.IGNORECASE)
    testo_per_ricerca = user_query_clean
    audit_log.inizia(sid, user_query)
    _imposta_vincoli_turno(stato)
    if injection:
        print("[SICUREZZA] possibile tentativo di injection nel messaggio")
        audit_log.nota("injection", True)

    # [FIX CRITICO 1]: Aggiungiamo subito il messaggio utente allo storico.
    # Altrimenti, se il flusso si interrompe prima (es. checkout), l'ultimo messaggio (es. P.IVA) va perso.
    stato["storico"].append(types.Content(role="user", parts=[types.Part.from_text(text=user_query)]))
    timestamp_ora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stato["log_chat"].append({"ruolo": "utente", "testo": user_query, "timestamp": timestamp_ora})

    # ORDINE IN ATTESA DI CONFERMA: "CONFERMO" / "annulla" si gestiscono senza chiamate al modello
    bozza = stato.get("ordine_in_attesa")
    if bozza and ordini.e_conferma(user_query):
        ok, msg, id_ordine = ordini.registra(bozza, stato, sid, _conversazione_per_ordine(stato))
        if ok:
            stato["ordine_in_attesa"] = None
        audit_log.nota("ordine", {"registrato": ok, "id": id_ordine, "righe": len(bozza.get("righe", []))})
        yield from _messaggio_fisso(stato, sid, msg + ("\nPosso aiutarti con altro?" if ok else ""), "ordine_confermato")
        return
    if bozza and ordini.e_annullamento(user_query):
        stato["ordine_in_attesa"] = None
        yield from _messaggio_fisso(stato, sid, "Ok, ordine annullato: non e' stato inviato nulla. Posso aiutarti con altro?",
                                    "ordine_annullato")
        return

    # COSTRUZIONE DEL TAGLIERE INSIEME, passo passo (core/costruzione_tagliere.py): "non mi convince" -> quanti salumi e
    # formaggi -> rosa numerata -> il cliente sceglie -> altre proposte fino al numero -> riepilogo. Domande e liste sono
    # testi fissi (nessuna chiamata API, numeri esatti); solo il riepilogo finale passa dal modello.
    turno_costruzione = None
    try:
        turno_costruzione = costruzione_tagliere.turno(stato, user_query, indice_testuale, stato.get("ultimo_piatto_proposto"))
    except Exception as _e_ct:
        print(f"[COSTRUZIONE] errore (ignorato): {breve(_e_ct)}")
        stato["costruzione"] = None
    if turno_costruzione and turno_costruzione.get("testo_fisso"):
        if turno_costruzione["prodotti"]:
            stato["prodotti_in_focus"] = [p["id"] for p in turno_costruzione["prodotti"]]
            for _p in turno_costruzione["prodotti"]:
                if _p["id"] not in stato["prodotti_mostrati_ordinati"]:
                    stato["prodotti_mostrati_ordinati"].append(_p["id"])
            stato["prodotti_mostrati"] = set(stato["prodotti_mostrati_ordinati"])
        audit_log.nota("costruzione_tagliere", turno_costruzione["fase"])
        yield from _messaggio_fisso(stato, sid, turno_costruzione["testo_fisso"], "costruzione_" + turno_costruzione["fase"])
        return

    # 1. RISCRITTURA QUERY CON CONTESTO COMPLETO DELLA CONVERSAZIONE
    scambi_recenti = []
    if len(stato["storico"]) > 0:
        for msg in stato["storico"][-6:]:  # ultimi 3 scambi (6 messaggi: user+model)
            ruolo = "Cliente" if msg.role == "user" else "Nino"
            testo_msg = "".join(p.text for p in msg.parts if getattr(p, "text", None))
            if len(testo_msg) > 1500:
                testo_msg = testo_msg[:1500] + "..."
            scambi_recenti.append(f"{ruolo}: {testo_msg}")

    contesto_conversazione = "\n".join(scambi_recenti) if scambi_recenti else "(Inizio conversazione, nessun messaggio precedente)"

    # ANALISI SEMANTICA UNIFICATA (Intent, Profile, RAG Elements)
    yield 'data: {"status": "Analizzo la richiesta..."}\n\n'
    analisi = analizza_richiesta_unificata(client_genai, user_query_clean, contesto_conversazione, stato)
    _aggiorna_profilo(stato, analisi.profilo)
    _imposta_vincoli_turno(stato)

    # ORDINE: estrazione dalla chat -> validazione -> RIEPILOGO da confermare (mai inviato senza "CONFERMO")
    if not turno_costruzione and analisi.tipo_richiesta == "chiusura_ordine" or (bozza and re.search(r"\bordin|\bp\.?\s?iva|partita iva", user_query, re.IGNORECASE)):
        print(f"[CHECKOUT] preparazione ordine per sessione {sid}")
        ordine_estratto = estrai_ordine_da_chat(client_genai, stato["storico"], MODELLO_FALLBACK, stato.get("riassunto", ""))
        nuova, messaggio = ordini.prepara(ordine_estratto, indice_testuale, stato)
        stato["ordine_in_attesa"] = nuova
        audit_log.nota("ordine", {"bozza": bool(nuova), "righe": len(nuova["righe"]) if nuova else 0})
        yield from _messaggio_fisso(stato, sid, messaggio, "ordine_riepilogo" if nuova else "ordine_dati_mancanti")
        return
    if bozza:
        # il cliente ha cambiato discorso: la bozza resta, ma glielo si ricorda nel profilo
        audit_log.nota("ordine_in_attesa", True)

    # Continuità conversazionale: se l'utente chiede "dimmene altri", recuperiamo l'argomento precedente
    if analisi.riferimento_precedente and analisi.argomento_riferito:
        ha_nuovi = any(e.dominio.lower() != "generale" for e in analisi.elementi_richiesti)
        if not ha_nuovi:

            analisi.elementi_richiesti = [ElementoRichiesto(
                dominio=analisi.argomento_riferito,
                quantita=None,
                query_ricerca=analisi.argomento_riferito
            )]

    # Estrai la query di ricerca combinata per il fallback / checks testuali storici
    queries = [e.query_ricerca for e in analisi.elementi_richiesti if e.query_ricerca]
    testo_per_ricerca = " ".join(queries) if queries else user_query_clean

    query_bassa_combinata = f"{user_query_clean} {testo_per_ricerca}".lower()

    # Riconoscimento esplicito stile/locale di mare
    if any(k in query_bassa_combinata for k in ["locale mare", "locale di mare", "ristorante mare", "ristorante di mare", "ristorante di pesce", "cucina di mare", "cucina marinara", "locale marinaro"]):
        stato["stile_cucina"] = "mare"
        if not stato.get("tipo_locale"):
            stato["tipo_locale"] = "ristorante di mare"

    # Canale HORECA/RETAIL dedotto dal tipo di locale (vedi profilazione_locale.py):
    # bar/pub/ristorante/pizzeria... -> horeca (formati grandi/da lavorazione);
    # bottega/negozio/gastronomia/supermercato... -> retail (formati da rivendita).
    # È un BOOST di ordinamento nella ricerca, mai un'esclusione: se per un
    # prodotto esiste solo un formato, resta comunque proponibile a chiunque.
    stato["canale_locale"] = rileva_canale_locale(stato.get("tipo_locale"))
    # Dieta e canale del cliente valgono per OGNI ricerca del turno (anche safety-net e fallback), non solo
    # per la composizione del ricettario: E2E reale, un cliente vegano riceveva carciofi surgelati e petali di
    # tartufo non vegani da una ricerca che non applicava il filtro dieta.
    _ru_ctx._DIETA_CORRENTE.set(stato.get("filtro_dieta"))
    _ru_ctx._CANALE_CORRENTE.set(stato.get("canale_locale"))

    print(f"[DEBUG PROFILO] Tipo Locale: {stato.get('tipo_locale')} | Canale: {stato.get('canale_locale')} | Stile: {stato.get('stile_cucina')} | Dieta: {stato.get('filtro_dieta')} | No Affettatrice: {stato.get('senza_affettatrice')} | Città: {stato.get('citta')}")
    print(f"[DEBUG INTENT] Richiede Composizione: {analisi.richiede_composizione} ({analisi.tipo_richiesta})")
    audit_log.nota("intento", {"tipo": analisi.tipo_richiesta, "composizione": bool(analisi.richiede_composizione)})

    stato.setdefault("ultimo_piatto_proposto", None)
    stato.setdefault("ricette_mostrate", set())

    # Riconoscimento se la query contiene parole forti di nuova richiesta o variante
    ha_parole_nuova_richiesta = any(k in query_bassa_combinata for k in [
        "vorrei", "fammi", "fammene", "proponimi",
        "passiamo a", "un altro", "un'altra",
        "altra proposta", "un panino", "un burger", "un primo piatto", "un secondo piatto", "un piatto", "uno con", "una con",
        "fammene una", "fammene uno"
    ])

    ha_parole_tecniche_formati = any(k in query_bassa_combinata for k in [
        "formati", "formato", "pezzatura", "pezzature", "grammatura", "grammature",
        "confezione", "confezioni", "peso", "pesi", "in che formato", "come li vendete",
        "questi ingredienti", "questi prodotti", "di questi", "su questi",
        "come si prepara", "come si cucina", "cottura", "tempi di cottura",
        "quanto costa", "prezzo", "prezzi", "scheda", "schede", "allergeni"
    ])

    # Riconoscimento esplicito: chiede un'alternativa / un altro piatto / nuova portata o variante
    chiede_alternativa_o_nuovo_piatto = bool(re.search(
        r"\b(altr[oaie]|alternativ[ae]|divers[oa]|cambiam[oa]|secondo piatto|un secondo|un primo|nuov[ao] piatt[oa]|altra proposta|fammene|uno con|una con)\b",
        query_bassa_combinata
    )) or ha_parole_nuova_richiesta

    # Follow-up su ingredienti / formati del piatto attivo (esce dal ricettario e gestisce i prodotti correnti)
    ultimo_piatto = stato.get("ultimo_piatto_proposto")
    ha_piatto_attivo = bool(ultimo_piatto and ultimo_piatto.get("prodotti_ids"))

    chiede_dettagli_formati_correnti = (
        ha_piatto_attivo
        and ha_parole_tecniche_formati
        and not ha_parole_nuova_richiesta
        and not chiede_alternativa_o_nuovo_piatto
    )
    
    piano_ricerca = None

    # TRIGGER GASTRONOMICO: Se chiede approfondimento sui prodotti correnti esce dal ricettario!
    if chiede_dettagli_formati_correnti:
        usa_ricettario = False
    elif chiede_alternativa_o_nuovo_piatto and any(k in query_bassa_combinata for k in ["tagliere", "menu", "tris", "primo", "secondo", "aperitivo"]):
        usa_ricettario = True
    else:
        usa_ricettario = analisi.richiede_composizione
    # "e del finger food?", "che olive avete?": una famiglia di prodotto nominata senza una portata e' una ricerca di
    # prodotti, non un nuovo piatto (chat reale: diventava un tagliere con giardiniera e nessun finger food)
    if usa_ricettario and ontologia.spec_da_query(user_query_clean) and not any(
            re.search(r"\b" + re.escape(w) + r"\b", user_query_clean.lower()) for ps in _PORTATE_PAROLE.values() for w in ps):
        usa_ricettario = False

    contesto_ricetta = None
    ids_da_tracciare = []

    if turno_costruzione:
        usa_ricettario = False
        chiede_dettagli_formati_correnti = False
        audit_log.nota("costruzione_tagliere", turno_costruzione["fase"])
        print(f"[COSTRUZIONE] fase: {turno_costruzione['fase']}")

    # Messaggi che GESTISCONO la proposta attiva ("facciamolo insieme", "metti l'opzione B", "no intendo il tris"):
    # si riusa la proposta salvata invece di cercarne una nuova (chat reale: "facciamolo insieme" dopo un tris
    # produceva tagliatelle al ragu')
    proposta_riusata = False
    if (not turno_costruzione and ultimo_piatto and ultimo_piatto.get("slot")
            and messaggio_su_proposta_attiva(user_query_clean, ultimo_piatto)):
        contesto_ricetta = ricostruisci_proposta(ultimo_piatto)
        proposta_riusata = contesto_ricetta is not None
        if proposta_riusata:
            usa_ricettario = False
            chiede_dettagli_formati_correnti = False
            print(f"[DEBUG STATO] Riuso della proposta attiva: '{ultimo_piatto.get('nome_piatto')}'")

    # Domande sui prodotti appena citati ("mi fai vedere questa pancetta", "hai un'immagine?", "cos'e'?"): il contesto
    # sono QUEI prodotti, non una nuova ricerca (chat reale: "hai un'immagine?" faceva cercare "immagine" e Nino
    # proponeva un altro prodotto)
    turno_su_prodotti_in_focus = False
    focus_scelti = []
    # domanda su una delle proposte numerate della costruzione ("com'e' il terzo?")
    _nominati_lista = costruzione_tagliere.nominati(stato, user_query_clean, indice_testuale) if not turno_costruzione else []
    if _nominati_lista:
        focus_scelti = _nominati_lista
        turno_su_prodotti_in_focus = True
        usa_ricettario = False
        chiede_dettagli_formati_correnti = False
    elif (not proposta_riusata and not turno_costruzione and stato.get("prodotti_in_focus") and foto.anaforico(user_query_clean)
            and not chiede_alternativa_o_nuovo_piatto):
        per_id_focus = {p["id"]: p for p in indice_testuale}
        focus = [per_id_focus[i] for i in stato["prodotti_in_focus"] if i in per_id_focus]
        parole_q = {w[:5] for w in re.findall(r"[a-zàèéìòù]+", user_query_clean.lower()) if len(w) >= 4}
        nominati = [p for p in focus if parole_q & {w[:5] for w in re.findall(r"[a-zàèéìòù]+", prima_riga(p["document"]).lower()) if len(w) >= 4}]
        parole_contenuto = parole_q - _PAROLE_ANAFORA
        if nominati:
            focus_scelti = nominati
        elif not parole_contenuto:
            focus_scelti = focus[:1]  # "hai un'immagine?": l'ultimo prodotto di cui si e' parlato
        else:
            focus_scelti = []  # nomina un prodotto che non era in primo piano: ricerca normale (chat reale: pancetta -> Parma)
        if focus_scelti:
            turno_su_prodotti_in_focus = True
            usa_ricettario = False
            chiede_dettagli_formati_correnti = False

    if usa_ricettario:
        try:
            ha_gia_prodotti = len(stato.get("prodotti_mostrati", set())) > 0
            target_salumi, target_formaggi = None, None
            for elem in analisi.elementi_richiesti:
                if elem.dominio.lower() == "salumi" and getattr(elem, "quantita", None) is not None:
                    target_salumi = elem.quantita
                elif elem.dominio.lower() == "formaggi" and getattr(elem, "quantita", None) is not None:
                    target_formaggi = elem.quantita

            # Determinazione categoria/portata ereditata dal piatto precedente se la query è anaforica o di variante
            categoria_ereditata = None
            piatto_precedente_nome = None
            if ultimo_piatto and ultimo_piatto.get("categoria"):
                cat_prec = ultimo_piatto.get("categoria")
                nome_prec = ultimo_piatto.get("nome_piatto", "")
                piatto_precedente_nome = nome_prec

                if cat_prec in ["primo", "risotto"] and any(r in nome_prec.lower() for r in ["risotto", "riso"]):
                    cat_prec = "risotto"

                # Mappatura famiglie di portata per rilevare se l'utente chiede esplicitamente un CAMBIO di portata
                FAMIGLIE_PORTATE = {
                    "pizza": {"pizza", "pinsa", "padellino", "focaccia"},
                    "risotto": {"risotto", "riso"},
                    "primo": {"primo", "primi", "pasta", "spaghetti", "spaghettone", "paccheri", "rigatoni", "gnocchi", "fregola", "calamarata", "orecchiette", "linguine", "fusilli", "scialatielli"},
                    "secondo": {"secondo", "secondi", "tagliata", "carne", "pesce al forno", "arrosto"},
                    "panino": {"panino", "panini", "burger", "hamburger", "sandwich"},
                    "tagliere": {"tagliere", "taglieri"},
                    "dolce": {"dolce", "dolci", "dessert"},
                    "contorno": {"contorno", "contorni"},
                    "antipasto": {"antipasto", "antipasti"},
                    # senza questa voce "fammi un tris" dopo un tagliere ereditava la categoria tagliere (chat reale)
                    "aperitivo": {"tris", "aperitivo", "aperitivi", "stuzzichini", "ciotoline"},
                }

                famiglia_corrente = cat_prec
                altre_famiglie = [parola for fam, parole in FAMIGLIE_PORTATE.items() if fam != famiglia_corrente for parola in parole]

                # Controlla solo nella query dell'utente (non nella riscritta, che potrebbe contenere la categoria dedotta)
                ha_cambio_portata_esplicito = any(re.search(r"\b" + re.escape(w) + r"\b", user_query_clean.lower()) for w in altre_famiglie)
                if not ha_cambio_portata_esplicito:
                    categoria_ereditata = cat_prec

            contesto_ricetta = componi_proposta_da_ricettario(
                richiesta_cliente=testo_per_ricerca,
                tipo_locale=stato.get("tipo_locale"),
                collezione_ricette=collezione_ricette,
                collezione_prodotti=collezione,
                indice_codici=indice_codici_prodotto,
                embedder=embedder,
                indice_fornitori=indice_fornitori,
                indice_testuale=indice_testuale,
                target_salumi=target_salumi,
                target_formaggi=target_formaggi,
                query_completa=user_query_clean,
                prodotti_esclusi=stato.get("prodotti_mostrati", set()),
                filtro_dieta=stato.get("filtro_dieta"),
                ricette_escluse=stato.get("ricette_mostrate", set()),
                categoria_ereditata=categoria_ereditata,
                piatto_precedente_nome=piatto_precedente_nome,
                piano_ricerca=piano_ricerca,
            )
        except Exception as e:
            print(f"[ATTENZIONE] Composizione da ricettario fallita ({e}), fallback su RAG generico.")
            contesto_ricetta = None

    if contesto_ricetta:
        tmpl = contesto_ricetta["template"]
        print(f"[DEBUG RICETTARIO] Proposta trovata: {tmpl['nome_piatto']}")
        modalita = modalita_composizione(user_query, stato)
        aggiungi_opzioni_b(contesto_ricetta, stato)
        audit_log.nota("ricetta", {"id": tmpl.get("id_ricetta") or tmpl.get("id"), "nome": tmpl.get("nome_piatto"),
                                   "modalita": modalita, "riusata": proposta_riusata,
                                   "slot": [(s.get("ingrediente_richiesto"), s.get("esito")) for s in contesto_ricetta.get("slot", [])]})
        contesto_testuale = costruisci_contesto_ricetta_testuale(contesto_ricetta) + "\n" + istruzioni_modalita(modalita)
        # Piu' portate chieste ("un primo di mare, poi tutto il menu"): se ne compone una per turno; va detto, non
        # ignorato (chat reale)
        _portate_chieste = {c for c, ws in _PORTATE_PAROLE.items()
                            if any(re.search(r"\b" + re.escape(w) + r"\b", user_query_clean.lower()) for w in ws)}
        if not proposta_riusata and ("menu" in _portate_chieste or len(_portate_chieste) >= 2):
            contesto_testuale += (f"\n[RICHIESTA DI PIU' PORTATE: qui e' composta solo la portata '{tmpl.get('categoria')}'. "
                                  "Presentala, poi di' chiaramente che puoi comporre anche le altre portate del menu e "
                                  "chiedi da quale continuare (antipasto, secondo, dolce...).]")
            audit_log.nota("piu_portate", sorted(_portate_chieste))
        record_prodotti = []
        for s in contesto_ricetta.get("slot", []):
            if s.get("esito") == "TROVATO" and s.get("prodotto_trovato"):
                ids_da_tracciare.append(s["prodotto_trovato"]["id"])
                record_prodotti.append(s["prodotto_trovato"])
            elif s.get("alternativa"):
                ids_da_tracciare.append(s["alternativa"]["id"])
                record_prodotti.append(s["alternativa"])

        # MACCHINA A STATI: Registra il piatto proposto ed esce dal ricettario per le domande successive
        stato["ultimo_piatto_proposto"] = {
            "id_ricetta": tmpl.get("id_ricetta"),
            "nome_piatto": tmpl.get("nome_piatto"),
            "categoria": tmpl.get("categoria"),
            "note_composizione": tmpl.get("note_composizione", ""),
            "prodotti_ids": list(ids_da_tracciare),
            # proposta completa (solo id, serializzabile in sessione): serve a riusarla nei messaggi successivi
            "slot": [{"ingrediente_richiesto": s.get("ingrediente_richiesto"), "esito": s.get("esito"),
                      "ruolo": s.get("ruolo"), "note_ingrediente": s.get("note_ingrediente"),
                      "prodotto_id": (s.get("prodotto_trovato") or {}).get("id"),
                      "opzione_b_id": (s.get("opzione_b") or {}).get("id"),
                      "alternativa_id": (s.get("alternativa") or {}).get("id")}
                     for s in contesto_ricetta.get("slot", [])],
        }
        if tmpl.get("id_ricetta"):
            stato["ricette_mostrate"].add(tmpl.get("id_ricetta"))
    elif turno_costruzione:
        record_prodotti = list(turno_costruzione["prodotti"])
        ids_da_tracciare += [p["id"] for p in record_prodotti]
        contesto_testuale = turno_costruzione["contesto"] + (
            "\n" + costruisci_contesto_testuale(record_prodotti) if record_prodotti else "")
        if turno_costruzione["fase"] == "fatto":
            # il tagliere scelto diventa la proposta attiva (un "non mi convince" successivo riparte da qui)
            stato["ultimo_piatto_proposto"] = {
                "id_ricetta": None, "nome_piatto": "Tagliere costruito con il cliente", "categoria": "tagliere",
                "note_composizione": "", "prodotti_ids": [p["id"] for p in record_prodotti],
                "slot": [{"ingrediente_richiesto": ("Salumi" if p["metadata"].get("reparto") == "SALUMI" else "Formaggi")
                          + " scelti dal cliente", "esito": "TROVATO", "ruolo": "protagonista", "note_ingrediente": "",
                          "prodotto_id": p["id"], "opzione_b_id": None, "alternativa_id": None} for p in record_prodotti]}
    elif turno_su_prodotti_in_focus:
        record_prodotti = [dict(p, match_esatto=True) for p in focus_scelti]
        ids_da_tracciare += [p["id"] for p in focus_scelti]
        audit_log.nota("prodotti_in_focus", [p["id"] for p in focus_scelti])
        contesto_testuale = ("[PRODOTTI DI CUI SI STA PARLANDO: il cliente si riferisce a questi prodotti gia' citati. "
                             "Rispondi su questi, non proporne altri se non li chiede. Se c'e' una foto il sistema la allega]\n"
                             + costruisci_contesto_testuale(record_prodotti))
    elif chiede_dettagli_formati_correnti:
        print(f"[DEBUG STATO] Follow-up su piatto attivo: '{ultimo_piatto.get('nome_piatto')}' — recupero schede formati")
        prodotti_ids = ultimo_piatto.get("prodotti_ids", [])
        res_esatti = collezione.get(ids=prodotti_ids, include=["metadatas", "documents"])
        record_prodotti = [
            {"id": d_id, "metadata": meta, "document": doc_text, "match_esatto": True}
            for d_id, meta, doc_text in zip(
                res_esatti.get("ids", []), res_esatti.get("metadatas", []), res_esatti.get("documents", [])
            )
        ]
        ordine_map = {pid: i for i, pid in enumerate(prodotti_ids)}
        record_prodotti.sort(key=lambda r: ordine_map.get(r["id"], 99))
        for r in record_prodotti:
            ids_da_tracciare.append(r["id"])

        contesto_testuale = (
            f"[DETTAGLIO E FORMATI DEI PRODOTTI DEL PIATTO PROPOSTO: {ultimo_piatto.get('nome_piatto')}]\n"
            f"Il cliente ha chiesto chiarimenti, formati, pezzature, confezioni o dettagli sui prodotti che compongono il piatto appena presentato.\n"
            f"Descrivi con precisione per ciascuno dei prodotti elencati il formato/peso esatto specificato nella sua scheda tecnica (es. confezione 500g, vaso 20g, busta 1.2/1.4kg, vaso 50g, ecc.).\n"
            f"Mantieni ESATTAMENTE gli stessi prodotti, NON inventare ingredienti diversi né proporre nuovi piatti!\n\n"
            + costruisci_contesto_testuale(record_prodotti)
        )
    else:
        # Se la richiesta è completamente diversa da un piatto (es. catalogo, singola categoria), resettiamo il piatto attivo
        if not any(k in query_bassa_combinata for k in ["piatto", "primo piatto", "secondo piatto", "tagliere", "pasta", "questi"]):
            stato["ultimo_piatto_proposto"] = None
        try:
            record_prodotti = []
            id_visti = set()

            # 1. Match ESATTO su codice direttamente dalla query originale E riscritta
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

            # 2b. Match su Cluster Regionali / Geografici (Spagna, Toscana, Puglia,
            # Piemonte, Trentino, Emilia, Sardegna...). L'elenco fornitori per
            # regione arriva SEMPRE da fornitori_config.py: aggiungere/togliere
            # un fornitore da un cluster si fa solo lì, non qui.
            regione_rilevata = rileva_cluster_regionale(query_bassa_combinata)
            cluster_attivo_fornitori = set()
            if regione_rilevata:
                for ruolo_possibile in ("salumi", "formaggi", "mare", "pane", "olive",
                                         "sottoli", "mostarde_confetture", "snack_secco", "contorni"):
                    for slug in elenco_fornitori_per_ruolo_regione(ruolo_possibile, regione_rilevata):
                        cluster_attivo_fornitori.update(FORNITORI[slug]["nomi_match"])
                for f_nome in cluster_attivo_fornitori:
                    prodotti_forn = trova_match_per_fornitore(f_nome, indice_fornitori, collezione, max_risultati=4)
                    for r in prodotti_forn:
                        if r["id"] not in id_visti:
                            id_visti.add(r["id"])
                            r["match_regionale"] = True
                            record_prodotti.append(r)

            def calcola_n_risultati(elemento, tipo_richiesta: str) -> int:
                if tipo_richiesta == "panoramica_catalogo":
                    return 60
                elif getattr(elemento, "quantita", None) is not None:
                    return max(elemento.quantita * 12, 40)
                else:
                    return 40

            elementi_da_cercare = analisi.elementi_richiesti if analisi.elementi_richiesti else []
            if not elementi_da_cercare:
                
                elementi_da_cercare = [ElementoRichiesto(dominio="generale", query_ricerca=testo_per_ricerca)]

            for elem in elementi_da_cercare:
                filtro_rep = elem.reparto.strip().upper() if elem.reparto else None
                filtro_sotto = elem.sottocategoria.strip().upper() if elem.sottocategoria else None

                n_risultati = calcola_n_risultati(elem, analisi.tipo_richiesta)
                
                yield 'data: {"status": "Ricerco a catalogo..."}\n\n'
                risultati_parziali = cerca_prodotti(
                    collezione, indice_codici_prodotto, embedder, elem.query_ricerca,
                    n_risultati, indice_fornitori=indice_fornitori,
                    indice_testuale=indice_testuale,
                    filtro_categoria=None,
                    filtro_reparto=filtro_rep,
                    filtro_sottocategoria=filtro_sotto,
                    tipo_locale=stato.get("tipo_locale"),
                    filtro_dieta=stato.get("filtro_dieta"),
                    canale_locale=stato.get("canale_locale"), intento=analisi.tipo_richiesta,
                )

                # --- ACTIVE RAG FALLBACK ---
                q_richiesta = getattr(elem, "quantita", None) or 3
                if len(risultati_parziali) < max(3, q_richiesta) and elem.dominio.lower() != "generale":
                    print(f"[ACTIVE RAG] Trovati solo {len(risultati_parziali)} risultati per '{elem.query_ricerca}'. Avvio contro-query sul dominio '{elem.dominio}'...")
                    risultati_fallback = cerca_prodotti(
                        collezione, indice_codici_prodotto, embedder, elem.dominio,
                        n_risultati, indice_fornitori=indice_fornitori,
                        indice_testuale=indice_testuale,
                        filtro_categoria=None,
                        filtro_reparto=filtro_rep,
                        filtro_sottocategoria=None,  # Allentato: il filtro esatto ha già fallito, cerchiamo nel dominio ampio
                        tipo_locale=stato.get("tipo_locale"),
                        filtro_dieta=stato.get("filtro_dieta"),
                        canale_locale=stato.get("canale_locale"), intento=analisi.tipo_richiesta,
                    )
                    
                    # Evita duplicati tra parziali e fallback
                    id_gia_presi = {r['id'] for r in risultati_parziali}
                    for r_fb in risultati_fallback:
                        if r_fb['id'] not in id_gia_presi:
                            risultati_parziali.append(r_fb)
                if any(w in query_bassa_combinata for w in ["finger food", "frittellin", "pastellat", "aperitiv", "snack", "caldo", "caldi", "fritti", "fritto"]) and not any(w in query_bassa_combinata for w in ["gelato", "sorbetto", "dolce", "dessert"]):
                    # Un finger food "caldo" non può essere un gelato
                    risultati_parziali = [
                        r for r in risultati_parziali
                        if "gelato" not in str(r["metadata"].get("sottocategoria", "")).lower()
                        and "gelato" not in str(r["metadata"].get("categoria_tassonomia", "")).lower()
                        and str(r["metadata"].get("reparto", "")).upper() != "GELO"
                    ]
                
                if "tagliere" in query_bassa_combinata or "taglieri" in query_bassa_combinata:
                    esclude_mare = "mare" not in query_bassa_combinata and "pesce" not in query_bassa_combinata
                    risultati_parziali = [
                        r for r in risultati_parziali
                        if "wurstel" not in str(r.get("document", "")).lower()
                        and "würstel" not in str(r.get("document", "")).lower()
                        and str(r["metadata"].get("sottocategoria", "")).upper() not in ["FORMAGGI FUSI", "PASTE FILATE USO CUCINA", "FORMAGGI FRESCHI INDUSTRIALI"]
                        and "JULIENNE" not in str(r["metadata"].get("formato_variante_liv5", "")).upper()
                        and "JULIENNE" not in str(r["metadata"].get("specifiche_liv4", "")).upper()
                        and not (esclude_mare and str(r["metadata"].get("reparto", "")).upper() == "MARE")
                    ]
                for r in risultati_parziali:
                    if r["id"] not in id_visti:
                        id_visti.add(r["id"])
                        r["dominio_assegnato"] = elem.dominio
                        record_prodotti.append(r)

            # Cap globale per fornitore su record_prodotti
            is_beer_query = bool(re.search(r"\bbirr[ae]\b", user_query.lower()))
            is_jam_query = bool(re.search(r"\b(marmellat[ae]|confettur[ae]|mostard[ae]|compost[ae])\b", user_query.lower()))
            is_warm_request = bool(re.search(r"\b(cald[oi]|fritt[oi]|friggere|cuocere|surgelat[oi]|gelo)\b", user_query.lower()))

            max_req_q = max((getattr(e, "quantita", 3) or 3) for e in elementi_da_cercare) if elementi_da_cercare else 3
            
            conteggio_globale = {}
            record_diversificati = []
            for r in record_prodotti:
                forn = r["metadata"].get("nome_fornitore", "")
                conteggio_globale[forn] = conteggio_globale.get(forn, 0) + 1
                is_regional_brand = bool(cluster_attivo_fornitori and any(cf.lower() in forn.lower() for cf in cluster_attivo_fornitori))
                max_brand = max(10, max_req_q + 2) if (
                    r.get("match_esatto")
                    or r.get("match_fornitore")
                    or r.get("match_regionale")
                    or is_regional_brand
                    or (is_beer_query and "messina" in forn.lower())
                    or (is_jam_query and "mongetto" in forn.lower())
                    or (is_warm_request and "di tria" in forn.lower())
                ) else max(4, max_req_q)
                if r.get("match_esatto") or r.get("match_regionale") or conteggio_globale[forn] <= max_brand:
                    record_diversificati.append(r)
            record_prodotti = record_diversificati

            # Priorità ai prodotti non ancora mostrati nella sessione corrente
            if is_warm_request:
                nuovi = [r for r in record_prodotti if (r["id"] not in stato["prodotti_mostrati"] or str(r["metadata"].get("categoria_prodotto","")).lower() == "gelo" or "di tria" in str(r["metadata"].get("nome_fornitore","")).lower())]
                gia_visti = [r for r in record_prodotti if r not in nuovi]
            else:
                nuovi = [r for r in record_prodotti if r["id"] not in stato["prodotti_mostrati"]]
                gia_visti = [r for r in record_prodotti if r["id"] in stato["prodotti_mostrati"]]
            record_prodotti = (nuovi + gia_visti)[:N_RISULTATI_RAG]

            # Il filtraggio dietetico viene già applicato in modo dinamico e coerente
            # all'interno di cerca_prodotti() usando le regole dal file YAML.
            # Non lo facciamo qui a valle altrimenti potremmo svuotare la lista.

        except Exception as e:
            err_msg = str(e).replace('"', "'").replace('\n', ' ')
            print(f"[ERRORE] ricerca fallita: {err_msg}")
            yield f'data: {{"error": "Errore nella ricerca"}}\n\n'
            return

        prodotti_mostrati_per_contesto = stato["prodotti_mostrati"].copy()
        if is_warm_request:
            prodotti_mostrati_per_contesto = {
                pid for pid in prodotti_mostrati_per_contesto
                if not any(r["id"] == pid and ("gelo" in str(r["metadata"].get("categoria_prodotto","")).lower() or "di tria" in str(r["metadata"].get("nome_fornitore","")).lower()) for r in record_prodotti)
            }
        if panoramica.e_panoramica(user_query_clean, analisi.tipo_richiesta):
            # schede complete per prodotti di gruppi diversi, non per i primi risultati della ricerca
            _rapp = panoramica.rappresentanti(user_query_clean, record_prodotti, indice_testuale)
            _ids_r = {p["id"] for p in _rapp}
            record_prodotti = _rapp + [r for r in record_prodotti if r["id"] not in _ids_r]
        # "Che taglio per la griglia?", "che legumi per una zuppa?": guida alla scelta dal second brain (core/guide_prodotto.py)
        _blocco_guida = ""
        if not contesto_ricetta and not turno_costruzione:
            try:
                _gs = guide_prodotto.consiglio(user_query_clean, second_brain.BRAIN, stato.get("filtro_dieta") or None,
                                               stato.get("prodotti_mostrati"), stato.get("canale_locale") or None)
                if _gs:
                    _ids_g = {p["id"] for p in _gs["prodotti"]}
                    record_prodotti = _gs["prodotti"] + [r for r in record_prodotti if r["id"] not in _ids_g]
                    _blocco_guida = _gs["blocco"]
                    audit_log.nota("guida_scelta", [p["id"] for p in _gs["prodotti"]])
            except Exception as _e_g:
                print(f"[GUIDA] errore (ignorato): {_e_g}")
        contesto_testuale = costruisci_contesto_testuale(record_prodotti, prodotti_mostrati_per_contesto)
        if _blocco_guida:
            contesto_testuale = _blocco_guida + "\n\n" + contesto_testuale
        # Domande d'insieme ("che prodotti avete di X?", "che pasta avete?"): il quadro dal catalogo intero (core/panoramica.py)
        if panoramica.e_panoramica(user_query_clean, analisi.tipo_richiesta):
            _pan = panoramica.blocco(user_query_clean, record_prodotti, indice_testuale)
            if _pan:
                contesto_testuale = _pan + "\n\n" + contesto_testuale
                audit_log.nota("panoramica", True)
        # NB: i prodotti del contesto NON sono "mostrati": lo diventano solo quelli citati nella risposta (sotto,
        # dopo il guardrail). Prima tutti i ~40-65 del contesto finivano tra i gia' mostrati e venivano messi in coda
        # nelle ricerche successive anche se il cliente non li aveva mai visti.

    # Safety net per richieste specifiche (solo se non siamo in follow-up formati sul piatto corrente)
    if not chiede_dettagli_formati_correnti and not turno_su_prodotti_in_focus and not proposta_riusata and not turno_costruzione:
        contesto_testuale = esegui_safety_net_prodotti(
            user_query, contesto_testuale, collezione, indice_codici_prodotto, embedder, indice_fornitori,
            indice_testuale=indice_testuale
        )

    # TRACCIAMENTO FIFO DEI PRODOTTI MOSTRATI (preserva rigorosamente l'ordine temporale)
    for pid in ids_da_tracciare:
        if pid not in stato["prodotti_mostrati_ordinati"]:
            stato["prodotti_mostrati_ordinati"].append(pid)
    if len(stato["prodotti_mostrati_ordinati"]) > MAX_PRODOTTI_MOSTRATI_TRACCIATI:
        stato["prodotti_mostrati_ordinati"] = stato["prodotti_mostrati_ordinati"][-MAX_PRODOTTI_MOSTRATI_TRACCIATI:]
    stato["prodotti_mostrati"] = set(stato["prodotti_mostrati_ordinati"])

    # Second brain: abbinamenti verificati (ricettario + catalogo) e scheda azienda se richiesta
    try:
        if not chiede_dettagli_formati_correnti and 'record_prodotti' in locals() and record_prodotti:
            # nei piatti composti (primi, secondi, pizze...) il piatto e' gia' l'abbinamento: niente abbinamenti per ingrediente
            _cat_prop = str(((contesto_ricetta or {}).get("template") or {}).get("categoria") or "").lower()
            _blocco_sb = second_brain.BRAIN.blocco_contesto(
                record_prodotti, user_query, dieta=stato.get("filtro_dieta") or None,
                esclusi=stato.get("prodotti_mostrati"), canale=stato.get("canale_locale") or None,
                con_abbinamenti=(turno_costruzione["fase"] == "fatto") if turno_costruzione
                else (not contesto_ricetta or _cat_prop in ("tagliere", "aperitivo", "antipasto")),
                dettaglio_prodotto=not contesto_ricetta and not turno_costruzione)
            if _blocco_sb:
                contesto_testuale = contesto_testuale + "\n\n" + _blocco_sb
    except Exception as _e_sb:
        print(f"[SECOND BRAIN] errore (ignorato): {_e_sb}")

    # Abstention: parole della richiesta che non compaiono MAI nel catalogo (es. "arancini", "wagyu")
    try:
        _senza_riscontro = copertura_richiesta.termini_senza_riscontro(user_query, indice_testuale)
        if _senza_riscontro:
            print(f"[COPERTURA] termini senza riscontro a catalogo: {_senza_riscontro}")
            audit_log.nota("termini_senza_riscontro", _senza_riscontro)
            contesto_testuale = contesto_testuale + "\n" + copertura_richiesta.riga_contesto(_senza_riscontro)
    except Exception as _e_c:
        print(f"[COPERTURA] errore (ignorato): {_e_c}")

    # Domande su consegne, ordine minimo, pagamenti, sede, calendario freschi: dati ufficiali (core/info_azienda.py)
    try:
        _info = info_azienda.blocco_contesto(user_query, stato.get("citta"))
        if _info:
            audit_log.nota("info_azienda", True)
            contesto_testuale = contesto_testuale + "\n\n" + _info
    except Exception as _e_i:
        print(f"[INFO AZIENDA] errore (ignorato): {breve(_e_i)}")

    # Storico lungo: i messaggi piu' vecchi non si scartano, si riassumono (core/riassunto.py), una chiamata lite ogni ~2 turni
    max_messaggi = MAX_SCAMBI_STORICO * 2
    if len(stato["storico"]) > max_messaggi + 4:
        scartati = stato["storico"][:-max_messaggi]
        stato["storico"] = stato["storico"][-max_messaggi:]
        stato["riassunto"] = riassunto.aggiorna(client_genai, MODELLO_RIASSUNTO, stato.get("riassunto", ""), scartati)
        audit_log.nota("riassunto_aggiornato", len(stato["riassunto"]))

    audit_log.nota("contesto", {"prodotti": len(record_prodotti) if "record_prodotti" in locals() else 0,
                                 "caratteri": len(contesto_testuale)})
    audit_log.nota("prodotti_contesto", [r["id"] for r in (record_prodotti if "record_prodotti" in locals() else [])][:40])

    # Creazione prompt finale
    prompt_di_sistema_completo = build_modular_prompt(
        analisi.tipo_richiesta,
        stato.get("canale_locale", ""),
        stato.get("filtro_dieta", "")
    )

    info_profilo = []
    if stato.get("tipo_locale"):
        info_profilo.append(f"- Tipo locale: {stato['tipo_locale']}")
    if stato.get("stile_cucina"):
        info_profilo.append(f"- Stile cucina: {stato['stile_cucina']}")
    if stato.get("filtro_dieta"):
        info_profilo.append(f"- Dieta / Vincolo alimentare: {stato['filtro_dieta']}")
    if stato.get("citta"):
        _z = stato.get("zona_consegna")
        _nota = {"fuori": "FUORI zona refrigerata: spedizione SOLO di prodotti a temperatura ambiente (i refrigerati/surgelati sono gia' stati esclusi)",
                 "coperta": "zona coperta dai mezzi refrigerati", "sconosciuta": "zona da verificare al momento dell'ordine"}.get(_z, "")
        info_profilo.append(f"- Il CLIENTE si trova a {stato['citta']} (e' li' che va consegnato; So Food ha sede a Bari)"
                            + (f": {_nota}" if _nota else ""))
    if stato.get("esclusioni_cliente"):
        info_profilo.append(f"- Il cliente NON vuole (vincolo permanente): {', '.join(stato['esclusioni_cliente'])}")
    if stato.get("allergie"):
        info_profilo.append(f"- ALLERGIE dichiarate (i prodotti a rischio sono gia' stati esclusi): "
                            f"{', '.join(allergeni.etichetta(a) for a in stato['allergie'])}")
    if stato.get("ordine_in_attesa"):
        info_profilo.append("- C'e' un ordine in attesa di conferma: ricorda al cliente che per inviarlo basta scrivere CONFERMO "
                            "(o dirti cosa cambiare)")
    blocco_profilo = "\n    [PROFILO CLIENTE MEMORIZZATO]\n    " + "\n    ".join(info_profilo) + "\n" if info_profilo else ""

    istruzioni_conteggio = []
    if getattr(analisi, "elementi_richiesti", None):
        for elem in analisi.elementi_richiesti:
            if getattr(elem, "quantita", None) is not None:
                istruzioni_conteggio.append(f"- Categoria '{elem.dominio}': devi presentare ESATTAMENTE {elem.quantita} prodotti. Nè uno di più, nè uno di meno.\n  (Eccezione: se non ci sono abbastanza risultati perfetti, per raggiungere la quota {elem.quantita} proponi i prodotti più simili o affini presenti nei DATI RAG piuttosto che dire che non ne abbiamo).")
    
    blocco_conteggi = ""
    if istruzioni_conteggio and not turno_costruzione:  # nella costruzione i numeri li gestisce il blocco dedicato
        blocco_conteggi = "\n[VINCOLI DI QUANTITA' OBBLIGATORI (Da rispettare rigorosamente, salvo eccezioni indicate)]\n" + "\n".join(istruzioni_conteggio) + "\n"

    avviso_injection = ("\n    " + sicurezza.AVVISO_INJECTION + "\n") if injection else ""
    prompt_finale = f"""{avviso_injection}{blocco_profilo}{riassunto.blocco_prompt(stato.get("riassunto", ""))}{blocco_conteggi}
    [DATI RAG ESTRATTI DAL CATALOGO - USA QUESTE INFO PER RISPONDERE]
    {contesto_testuale}

    [DOMANDA DELL'UTENTE]
    {user_query}
    """

    # Modello da usare per le risposte utente: Gemini 3.5 Flash-Lite con fallback su Gemini 3.7 Flash
    modello_da_usare = os.getenv("MODELLO_RISPOSTA", MODELLO_PRINCIPALE)

    # Generazione risposta 
    try:
        chat_session = client_genai.chats.create(
            model=modello_da_usare,
            config=types.GenerateContentConfig(system_instruction=prompt_di_sistema_completo, temperature=0.3),
            history=stato["storico"][:-1]  # il messaggio attuale arriva dentro prompt_finale
        )
    
        response = None
        for tentat in range(3):
            try:
                yield 'data: {"status": "Scrivo la risposta..."}\n\n'
                response_stream = chat_session.send_message_stream(prompt_finale)
                testo_pulito = ""
                for chunk in response_stream:
                    if chunk.text:
                        # Rimuoviamo il testo "sottofondo" anche dal chunk se possibile per evitare che l'utente veda la parola sbagliata
                        chunk_text = pulisci_chunk(chunk.text)
                        testo_pulito += chunk_text
                        import json
                        chunk_json = json.dumps({"chunk": chunk_text})
                        yield f"data: {chunk_json}\n\n"
                break
            except Exception as e:
                err_msg = str(e)
                if any(k in err_msg for k in ["429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"]):
                    if modello_da_usare != MODELLO_FALLBACK:
                        print(f"[ATTENZIONE] Fallback da {modello_da_usare} a {MODELLO_FALLBACK} per errore: {breve(err_msg)}")
                        modello_da_usare = MODELLO_FALLBACK
                        try:
                            chat_session = client_genai.chats.create(
                                model=MODELLO_FALLBACK,
                                config=types.GenerateContentConfig(system_instruction=prompt_di_sistema_completo, temperature=0.3),
                                history=stato["storico"][:-1]  # il messaggio attuale arriva dentro prompt_finale
                            )
                            yield 'data: {"status": "Scrivo la risposta..."}\n\n'
                            response_stream = chat_session.send_message_stream(prompt_finale)
                            testo_pulito = ""
                            for chunk in response_stream:
                                if chunk.text:
                                    chunk_text = pulisci_chunk(chunk.text)
                                    testo_pulito += chunk_text
                                    import json
                                    chunk_json = json.dumps({"chunk": chunk_text})
                                    yield f"data: {chunk_json}\n\n"
                            break
                        except Exception as fb_err:
                            err_msg = str(fb_err)
                    if tentat < 2:
                        time.sleep(3 * (tentat + 1))
                        continue
                print(f"[ERRORE] modello non disponibile: {breve(err_msg)}")
                audit_log.nota("errore", breve(err_msg))
                audit_log.chiudi(stato, "")
                # il messaggio resta senza risposta: lo si toglie dallo storico, cosi' il cliente puo' semplicemente riscriverlo
                if stato["storico"] and stato["storico"][-1].role == "user":
                    stato["storico"].pop()
                yield f'data: {json.dumps({"error": MSG_ERRORE_CLIENTE})}\n\n'
                return
    
        testo_pulito = pulisci_markdown(testo_pulito)  # core/formato_testo.py
    
        # 5b. Guardrail anti-allucinazione: ogni prodotto citato (in grassetto) deve esistere a catalogo
        # e rispettare la dieta del cliente. Il testo e' gia' stato mostrato in streaming, quindi se
        # c'e' un problema si accoda una correzione e si ripulisce lo storico (core/guardrail_output.py).
        if os.getenv("GUARDRAIL_OUTPUT", "1") == "1":
            try:
                esito_g = guardrail_output.verifica_prodotti_citati(testo_pulito, indice_testuale, stato.get("filtro_dieta"),
                                                                    stato.get("allergie"))
                for _r, _c, _pid in esito_g["dubbi"]:
                    print(f"[GUARDRAIL] citazione dubbia (probabile parafrasi): '{_c}' ~ {_pid}")
                audit_log.nota("guardrail", {"verificati": esito_g["verificati"],
                                             "non_verificati": [c for _, c in esito_g["non_verificati"]],
                                             "incompatibili": [c for _, c, _m in esito_g["incompatibili"]],
                                             "dubbi": [c for _, c, _p in esito_g["dubbi"]]})
                if esito_g["non_verificati"] or esito_g["incompatibili"]:
                    print(f"[GUARDRAIL] non verificati: {[c for _, c in esito_g['non_verificati']]} | "
                          f"incompatibili: {[c for _, c, _m in esito_g['incompatibili']]}")
                    nota_g = guardrail_output.nota_correzione(esito_g)
                    testo_pulito = guardrail_output.ripulisci_testo(testo_pulito, esito_g)
                    if nota_g:
                        import json as _json_g
                        yield f"data: {_json_g.dumps({'chunk': nota_g})}\n\n"
                        testo_pulito += nota_g
                    stato["log_chat"].append({"ruolo": "guardrail", "testo": nota_g.strip(),
                                              "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            except Exception as _e_g:
                print(f"[GUARDRAIL] errore (ignorato): {_e_g}")
            # Formati e canale dichiarati nel testo (es. "cartoni HORECA" per un prodotto da 300 g)
            try:
                err_formati = guardrail_output.verifica_formati(testo_pulito, indice_testuale)
                if err_formati:
                    print(f"[GUARDRAIL] formati/canale errati: {[m for _, _, m in err_formati]}")
                    audit_log.nota("formati_errati", [m for _, _, m in err_formati])
                    nota_f = guardrail_output.nota_formati(err_formati)
                    import json as _json_f
                    yield f"data: {_json_f.dumps({'chunk': nota_f})}\n\n"
                    testo_pulito += nota_f
                    stato["log_chat"].append({"ruolo": "guardrail", "testo": nota_f.strip(),
                                              "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            except Exception as _e_f:
                print(f"[GUARDRAIL] errore formati (ignorato): {_e_f}")
            # Produttori citati che non esistono a catalogo
            try:
                err_prod = guardrail_output.verifica_produttori(testo_pulito, indice_testuale)
                if err_prod:
                    print(f"[GUARDRAIL] produttori inesistenti: {[c for _, c in err_prod]}")
                    audit_log.nota("produttori_inesistenti", [c for _, c in err_prod])
                    nota_p = ("\n\nCorrezione: " +", ".join(f"«{c}»" for _, c in err_prod)
                              + " non risulta tra i nostri produttori; fai riferimento solo ai prodotti indicati con il loro produttore.")
                    import json as _json_p
                    yield f"data: {_json_p.dumps({'chunk': nota_p})}\n\n"
                    testo_pulito += nota_p
            except Exception as _e_p:
                print(f"[GUARDRAIL] errore produttori (ignorato): {_e_p}")

        # 6. Foto e prodotti "in primo piano" (core/foto.py). Una foto si mostra solo se il prodotto e' identificato
        # senza ambiguita' (guardrail: un solo candidato) e il file esiste; e solo quando serve: il cliente la chiede,
        # e' incerto su un prodotto, o chiede informazioni su uno o due prodotti precisi.
        testo_pulito = re.sub(r'[ \t]*\[IMG:[^\]]*\][ \t]*', '', testo_pulito)  # mai foto scelte dal modello
        try:
            certi = (locals().get("esito_g") or {}).get("certi") or guardrail_output.verifica_prodotti_citati(
                testo_pulito, indice_testuale)["certi"]
        except Exception:
            certi = []
        if certi:
            stato["prodotti_in_focus"] = list(dict.fromkeys(i for _c, i in certi))[:5]
            for _pid in dict.fromkeys(i for _c, i in certi):  # mostrati = citati davvero
                if _pid not in stato["prodotti_mostrati_ordinati"]:
                    stato["prodotti_mostrati_ordinati"].append(_pid)
            stato["prodotti_mostrati"] = set(stato["prodotti_mostrati_ordinati"][-MAX_PRODOTTI_MOSTRATI_TRACCIATI:])
        audit_log.nota("citati", len({i for _c, i in certi}))
        per_id = {p["id"]: p for p in indice_testuale}
        info_su_prodotto = turno_su_prodotti_in_focus or (analisi.tipo_richiesta == "ricerca_specifica"
                                                          and len({i for _c, i in certi}) <= 2)
        foto_ids = foto.scegli(user_query, certi, per_id, info_su_prodotto,
                               preferiti=[p["id"] for p in focus_scelti] if turno_su_prodotti_in_focus else None)
        if foto_ids:
            testo_pulito = foto.inserisci(testo_pulito, certi, foto_ids, per_id)
            audit_log.nota("foto", foto_ids)
            for _fid in foto_ids:
                yield f'data: {json.dumps({"chunk": chr(10) + chr(10) + "[IMG: " + str(foto.percorso(per_id[_fid]["metadata"])) + "]"})}\n\n'

        # Clean up any excessive newlines left behind by stripping
        testo_pulito = re.sub(r'\n{3,}', '\n\n', testo_pulito)
        testo_pulito = re.sub(r' \n', '\n', testo_pulito)
        testo_pulito = re.sub(r'\n ,', ',', testo_pulito)
        
        # Il messaggio utente è già stato salvato all'inizio della funzione. Salviamo solo la risposta del modello.
        stato["storico"].append(types.Content(role="model", parts=[types.Part.from_text(text=testo_pulito)]))
        timestamp_ora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stato["log_chat"].append({"ruolo": "nino", "testo": testo_pulito, "timestamp": timestamp_ora})
        stato["contatore_messaggi"] += 1

        if stato["contatore_messaggi"] % FREQUENZA_SALVATAGGIO_LOG == 0:
            salva_log_chat(sid, stato)
        sessioni_store.salva(sid, stato)
        audit_log.chiudi(stato, testo_pulito)
        # Testo finale gia' ripulito dai guardrail: lo usano i canali che non mostrano lo streaming (/chat, vocali)
        yield 'data: {"final": ' + json.dumps(testo_pulito) + '}\n\n'

    except Exception as general_e:
        import traceback
        traceback.print_exc()
        print(f"[ERRORE] interno: {breve(general_e)}")
        yield f'data: {json.dumps({"error": MSG_ERRORE_CLIENTE})}\n\n'
        audit_log.nota("errore", str(general_e)[:200])
        audit_log.chiudi(stato, "")
    finally:
        yield 'data: {"done": true}\n\n'


# WhatsApp Cloud API (spento di default: WHATSAPP_ENABLED=1, vedi core/whatsapp.py)
whatsapp.registra_whatsapp(app, elabora_messaggio_nino, stato_per_sid, trascrivi_audio)


@app.route("/api/v1/chat/stream", methods=["POST"])
def chat_stream():
    user_query = request.json.get("message", "") if request.json else ""
    if not user_query:
        from flask import jsonify
        return jsonify({"error": "Empty message"}), 400
    stato, sid = ottieni_sessione()
    from flask import Response
    return Response(stream_messaggio_nino(user_query, stato, sid), mimetype="text/event-stream")

@app.route("/chat", methods=["POST"])
def chat():
    user_query = request.json.get("message", "") if request.json else ""

    if not user_query:
        return jsonify({"reply": "Messaggio vuoto."})

    stato, sid = ottieni_sessione()

    # Intercettazione della correzione di addestramento
    comando_normalizzato = user_query.strip().lower()
    if comando_normalizzato.startswith("!impara"):
        # Misura di sicurezza: l'apprendimento libero è disabilitato in produzione
        if os.getenv("ENABLE_IMPARA", "0") != "1":
            return jsonify({"reply": "🚫 Il comando di addestramento <b>!impara</b> è attualmente disabilitato per motivi di sicurezza."})
            
        resto = user_query.strip()[len("!impara"):].lstrip(":").strip()
        if not resto:
            return jsonify({"reply": (
                "✍️ Scrivimi il feedback dopo il comando, es: "
                "<i>!impara: se il cliente chiede formaggi freschi, proponi sempre salumi stagionati</i>"
            )})

        ultimi_scambi_leggibili = []
        for msg in stato["storico"][-6:]:
            ruolo = "Utente" if msg.role == "user" else "Nino"
            testo_msg = "".join(p.text for p in msg.parts if getattr(p, "text", None))
            ultimi_scambi_leggibili.append(f"{ruolo}: {testo_msg}")

        with open("correzioni_chat.jsonl", "a", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "testo": resto,
                "contesto_conversazione": ultimi_scambi_leggibili,
            }, f, ensure_ascii=False)
            f.write("\n")
        return jsonify({"reply": "✅ <i>Feedback registrato con il contesto della conversazione! Lo analizzerò durante il prossimo addestramento offline.</i>"})

    risultato = elabora_messaggio_nino(user_query, stato, sid)
    return jsonify(risultato)


@app.route("/chat_audio", methods=["POST"])
def chat_audio():
    stato, sid = ottieni_sessione()

    audio_file = request.files.get("audio")
    if not audio_file:
        return jsonify({"reply": "Nessun file audio inviato.", "error": "audio_missing"}), 400

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return jsonify({"reply": "Il file audio inviato è vuoto.", "error": "audio_empty"}), 400

    mime_type = audio_file.mimetype or "audio/webm"

    # Determina l'estensione del file
    ext = ".webm"
    if "wav" in mime_type: ext = ".wav"
    elif "mp3" in mime_type or "mpeg" in mime_type: ext = ".mp3"
    elif "ogg" in mime_type: ext = ".ogg"
    elif "mp4" in mime_type or "m4a" in mime_type: ext = ".m4a"

    nome_file = f"vocale_{sid[:8]}_{int(time.time()*1000)}{ext}"
    percorso_salvataggio = CARTELLA_AUDIO_UPLOADS / nome_file
    audio_url = ""
    try:
        percorso_salvataggio.write_bytes(audio_bytes)
        audio_url = f"/static/audio_uploads/{nome_file}"
    except Exception as e:
        print(f"[ATTENZIONE] Salvataggio file audio fallito: {e}")

    # Trascrizione con modello Gemini
    testo_trascritto = trascrivi_audio(audio_bytes, mime_type)
    if not testo_trascritto:
        return jsonify({
            "reply": "Non sono riuscito a comprendere chiaramente l'audio. Potresti ripetere o inviarmelo per iscritto?",
            "transcription": "",
            "audio_url": audio_url
        })

    risultato = elabora_messaggio_nino(testo_trascritto, stato, sid)
    risultato["transcription"] = testo_trascritto
    risultato["audio_url"] = audio_url
    return jsonify(risultato)


@app.route("/immagine")
def servi_immagine():
    """Questa rotta riceve il percorso dal browser e gli invia il file immagine reale.
    Aggiunto controllo estensioni per prevenire Path Traversal su file critici (.env, .py)."""
    percorso = request.args.get("path")
    if not percorso:
        return "Percorso non fornito", 400
        
    percorso = os.path.normpath(percorso)
    estensioni_valide = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    
    if not percorso.lower().endswith(estensioni_valide):
        return "Accesso negato: il file richiesto non è un'immagine valida.", 403
        
    # Sicurezza: assicura che il percorso sia all'interno del DATA_LAKE_PATH
    data_lake_path = os.getenv("DATA_LAKE_PATH")
    if not data_lake_path:
        return "Accesso negato: configurazione di sicurezza mancante (DATA_LAKE_PATH non definito).", 403
        
    base_dir = os.path.abspath(data_lake_path)
    req_dir = os.path.abspath(percorso)
    
    # Prevenzione di base_dir="C:\data" vs req_dir="C:\data_evil" usando commonpath
    try:
        if os.path.commonpath([base_dir, req_dir]) != base_dir:
            return "Accesso negato: percorso non consentito.", 403
    except ValueError:
        # Se sono su due dischi diversi (es. C: vs D:)
        return "Accesso negato: percorsi incompatibili.", 403

    if os.path.exists(percorso):
        return send_file(percorso)
    return "Immagine non trovata o percorso non valido", 404


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print("="*70)
    print("[OK] SERVER FLASK AVVIATO CON SUCCESSO!")
    print(">>> In ascolto su http://127.0.0.1:5000 (API /api/v1/chat/stream e webhook WhatsApp)")
    print("="*70)
    app.run(debug=False, use_reloader=False)