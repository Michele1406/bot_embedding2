import os
import re
import json
import time
import uuid
import datetime
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template_string, session
import chromadb
from pydantic import BaseModel
from google import genai
from google.genai import types
from core.system_prompt_v2 import SYSTEM_PROMPT_NINO
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
    estrai_conteggi_tagliere,
    pulisci_nome_commerciale,
    rileva_cluster_regionale,
)
from core.fornitori_config import FORNITORI, elenco_fornitori_per_ruolo_regione
from core.profilazione_locale import rileva_canale_locale
from core.tassonomia_sofood import classifica_terra_mare
from core.domain_rules import check_board_violations
from dotenv import load_dotenv

from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from core.system_prompt_v2 import SYSTEM_PROMPT_NINO

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

class AnalisiUnificata(BaseModel):
    tipo_richiesta: str = Field(description="'panoramica_catalogo', 'ricerca_specifica', 'composizione_piatto', 'conversazione_generica'")
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

# ====================================================================
# CONFIGURAZIONE GLOBALE
# ====================================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("ATTENZIONE: GEMINI_API_KEY non trovata nel file .env")

MODELLO_EMBEDDING = "models/gemini-embedding-2"
MODELLO_PRINCIPALE = os.getenv("MODELLO_RISPOSTA", "models/gemini-3.0-flash")
MODELLO_GEMINI = "models/gemini-3.5-flash-lite"
MODELLO_FALLBACK = "models/gemini-3.5-flash-lite"
MODELLO_AUDIO = "models/gemini-2.5-flash"
MODELLO_AUDIO_FALLBACK = "models/gemini-3.5-flash-lite"
PERCORSO_DATABASE_VETTORIALE = "./database_vettoriale"
NOME_COLLEZIONE = "catalogo_sofood"
N_RISULTATI_RAG = 65  # Aumentato per passare più prodotti all'IA e permettere taglieri grandi
MAX_SCAMBI_STORICO = 7
MAX_PRODOTTI_MOSTRATI_TRACCIATI = 60  # Aumentato per gestire i 45 prodotti

# Cartelle di salvataggio
CARTELLA_LOG_CHAT = Path("./log/chat")
CARTELLA_LOG_CHAT.mkdir(parents=True, exist_ok=True)
CARTELLA_AUDIO_UPLOADS = Path("./static/audio_uploads")
CARTELLA_AUDIO_UPLOADS.mkdir(parents=True, exist_ok=True)
FREQUENZA_SALVATAGGIO_LOG = 1  # salva ogni singolo messaggio utente in tempo reale

# ====================================================================
# CLASSE EMBEDDING
# ====================================================================
class EmbedderMultimodaleGemini:
    """Calcola vettori con gemini-embedding-2 per interrogare ChromaDB."""
    def __init__(self, api_key: str, model_name: str):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def embed_query(self, testo: str) -> list:
        response = self.client.models.embed_content(
            model=self.model_name,
            contents=[testo],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        vettore = response.embeddings[0]
        return vettore.values if hasattr(vettore, "values") else list(vettore)


def carica_memoria_dinamica():
    """Legge le regole apprese e le aggiunge al prompt di sistema"""
    if os.path.exists("memoria_dinamica.txt"):
        with open("memoria_dinamica.txt", "r", encoding="utf-8") as f:
            return "\n\nREGOLE APPRESE E LOGICHE DI ABBINAMENTO (DA RISPETTARE TASSATIVAMENTE):\n" + f.read()
    return ""


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

    for modello in [MODELLO_AUDIO, MODELLO_AUDIO_FALLBACK]:
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
NOME_COLLEZIONE_RICETTE = "ricette_sofood"
collezione_ricette = client_db.get_collection(name=NOME_COLLEZIONE_RICETTE)
embedder = EmbedderMultimodaleGemini(api_key=GEMINI_API_KEY, model_name=MODELLO_EMBEDDING)

# Indice codice_prodotto -> id, costruito una volta sola all'avvio (ricerca esatta)
indice_codici_prodotto = costruisci_indice_codici(collezione)
# Indice fornitore -> id, costruito una volta sola all'avvio (ricerca per brand/fornitore)
indice_fornitori = costruisci_indice_fornitori(collezione)
# Indice testuale per ricerca lessicale ibrida su titoli/nomi di tutti i prodotti
indice_testuale = costruisci_indice_testuale(collezione)

# ====================================================================
# STATO PER SESSIONE
# ====================================================================
sessioni = {}  # sid -> {"storico": [...], "prodotti_mostrati": set(), "prodotti_mostrati_ordinati": [], ...}


def ottieni_sessione():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    sid = session["sid"]
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
        }
    sessioni[sid].setdefault("ultimo_piatto_proposto", None)
    sessioni[sid].setdefault("ricette_mostrate", set())
    return sessioni[sid], sid


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
@app.route("/")
def home():
    """Carica l'interfaccia grafica HTML"""
    return render_template_string(HTML_TEMPLATE)


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

REGOLE PER IL RIFERIMENTO PRECEDENTE:
- Se il cliente dice "dimmene altri", "ancora", o "altri" senza specificare cosa, devi capire dallo STORICO a cosa si riferisce e impostare `riferimento_precedente`=true e `argomento_riferito` al dominio di cui parlavate.

Rispondi rigorosamente con il JSON dello schema AnalisiUnificata.
"""
    try:
        risposta = client_genai.models.generate_content(
            model=MODELLO_PRINCIPALE,  # Usiamo Flash 3.6 (modello principale) per maggiore accuratezza visto che fonde 3 chiamate in 1
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=AnalisiUnificata,
            ),
        )
        return AnalisiUnificata.model_validate_json(risposta.text)
    except Exception as e:
        print(f"[ATTENZIONE] Analisi unificata fallita ({e}), fallback euristico.")
        richiede_comp = any(p in user_query.lower() for p in ["tagliere", "tris", "aperitivo", "ciotoline", "menu", "menù", "burger", "hamburger"])
        
        import re
        def estrai_q(kws, txt):
            for k in kws:
                m = re.search(r'(\d+)\s+(?:\w+\s+){0,2}' + k, txt)
                if m: return int(m.group(1))
            for k in kws:
                m = re.search(k + r'\s+(?:\w+\s+){0,3}(\d+)', txt)
                if m: return int(m.group(1))
            m = re.findall(r'\b(\d+)\b', txt)
            nums = [int(x) for x in m if 1 < int(x) <= 15]
            if nums: return nums[0]
            return 2

        elementi_fb = []
        q_lower = user_query.lower()
        
        salumi_kws = ["salum", "prosciutt", "affettat", "coppa", "pancetta", "bresaola", "salam", "mortadella"]
        if any(w in q_lower for w in salumi_kws):
            q_salumi = "prosciutto crudo " if "prosciutt" in q_lower else ""
            q_salumi += "salumi affettati prosciutti"
            elementi_fb.append(ElementoRichiesto(dominio="salumi", tipo_prodotto="salumi", query_ricerca=q_salumi, quantita=estrai_q(salumi_kws, q_lower)))
            
        formaggi_kws = ["formagg", "pecorino", "caciocavallo", "parmigiano", "mozzarella", "burrata"]
        if any(w in q_lower for w in formaggi_kws):
            elementi_fb.append(ElementoRichiesto(dominio="formaggi", tipo_prodotto="formaggi", query_ricerca="formaggi stagionati", quantita=estrai_q(formaggi_kws, q_lower)))
            
        mare_kws = ["mare", "pesce", "ittico", "salmone", "tonno", "gamber", "polpo"]
        if any(w in q_lower for w in mare_kws):
            elementi_fb.append(ElementoRichiesto(dominio="mare", tipo_prodotto="prodotti di mare", query_ricerca="mare pesce ittico", quantita=estrai_q(mare_kws, q_lower)))
            
        dispensa_kws = ["marmellat", "confettur", "miele", "crem", "sottoli", "pasta", "riso"]
        if any(w in q_lower for w in dispensa_kws):
            elementi_fb.append(ElementoRichiesto(dominio="dispensa", tipo_prodotto="dispensa", query_ricerca="marmellata conserve", quantita=1))
            
        finger_kws = ["finger", "fritt", "cald", "rigenerare", "arancin", "crocchett", "snack", "tria"]
        if any(w in q_lower for w in finger_kws):
            elementi_fb.append(ElementoRichiesto(dominio="gelo", tipo_prodotto="fritti", query_ricerca="pastella frittelline pettole stick verdorate", quantita=estrai_q(finger_kws, q_lower)))
        
        if not elementi_fb:
            elementi_fb = [ElementoRichiesto(dominio="generale", quantita=None, query_ricerca=user_query)]
        return AnalisiUnificata(
            tipo_richiesta="conversazione_generica" if not richiede_comp else "composizione_piatto",
            richiede_composizione=richiede_comp,
            riferimento_precedente=False,
            argomento_riferito=None,
            elementi_richiesti=elementi_fb,
            profilo=ProfiloClienteAggiornato()
        )


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
            prima_linea = doc.splitlines()[0] if doc else ""
            meta = s["prodotto_trovato"].get("metadata", {})
            fornitore = meta.get("nome_fornitore", "")
            nome_prodotto = pulisci_nome_commerciale(prima_linea, fornitore) if prima_linea else meta.get("nome_prodotto", s["prodotto_trovato"]["id"])
            str_prod = f" (Produttore: {fornitore})" if fornitore else ""
            righe.append(f"- {s['ingrediente_richiesto']}: {nome_prodotto}{str_prod} (MATCH REALE A CATALOGO)")
            percorso_img = str(meta.get("percorso_immagine", "")).strip()
            ha_img = (
                meta.get("ha_immagine_primaria")
                and percorso_img
                and percorso_img.lower() not in ("", "nan", "none", "false")
                and os.path.exists(percorso_img)
            )
            if ha_img:
                righe.append(f"  Percorso File Immagine: {percorso_img}")
            else:
                righe.append(f"  Immagine: NESSUNA FOTO A CATALOGO (NON inserire alcun tag [IMG] per questo prodotto)")
        elif s.get("esito") == "SOSTITUITO":
            righe.append(f"- {s['ingrediente_richiesto']}: NON A CATALOGO, sostituito con {s.get('sostituto_nome', 'prodotto alternativo')}")
            righe.append(f"  Immagine: NESSUNA FOTO A CATALOGO (NON inserire alcun tag [IMG] per questo prodotto)")
        elif s.get("esito") == "OMESSO":
            righe.append(f"- {s['ingrediente_richiesto']}: NON A CATALOGO, nessun sostituto valido — omettere dalla proposta")
        if s.get("note_ingrediente"):
            righe.append(f"  (nota: {s['note_ingrediente']})")
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
        for forn_chiave, id_list in indice_fornitori.items():
            if len(forn_chiave) < 4:
                continue
            if re.search(r"\b" + re.escape(forn_chiave) + r"\b", query_lower) and forn_chiave not in contesto_lower:
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
                    doc_p = r["document"].splitlines()[0].lower() if r.get("document") else ""
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

    if not prodotti_aggiuntivi:
        return contesto_testuale

    blocco_extra = "\n\n[PRODOTTI SPECIFICI RICHIESTI DALL'UTENTE — PRESENTI A CATALOGO SO FOOD (NON NEGARE LA DISPONIBILITÀ!)]\n"
    for r in prodotti_aggiuntivi:
        meta = r["metadata"]
        prima_linea = r["document"].splitlines()[0] if r.get("document") else ""
        nome_pulito = pulisci_nome_commerciale(prima_linea, meta.get("nome_fornitore", ""))
        blocco_extra += f"- Prodotto: {nome_pulito} (Produttore: {meta.get('nome_fornitore')}, Codice: {meta.get('codice_prodotto')}, Categoria: {meta.get('categoria_prodotto')})\n"
        percorso_img = str(meta.get("percorso_immagine", "")).strip()
        ha_img = (
            meta.get("ha_immagine_primaria")
            and percorso_img
            and percorso_img.lower() not in ("", "nan", "none", "false")
            and os.path.exists(percorso_img)
        )
        if ha_img:
            blocco_extra += f"  Percorso File Immagine: {percorso_img}\n"
        else:
            blocco_extra += "  Immagine: NESSUNA FOTO A CATALOGO (NON inserire alcun tag [IMG] per questo prodotto)\n"

    return contesto_testuale + blocco_extra


def elabora_messaggio_nino(user_query: str, stato: dict, sid: str) -> dict:
    """Esegue l'elaborazione RAG e la generazione della risposta di Nino."""
    user_query_clean = re.sub(r'\b(hamburge|hamburgher|amburgher)\b', 'hamburger', user_query, flags=re.IGNORECASE)
    user_query_clean = re.sub(r'\b(kechup|chechup)\b', 'ketchup', user_query_clean, flags=re.IGNORECASE)
    testo_per_ricerca = user_query_clean

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
    analisi = analizza_richiesta_unificata(client_genai, user_query_clean, contesto_conversazione, stato)

    # Continuità conversazionale: se l'utente chiede "dimmene altri", recuperiamo l'argomento precedente
    if analisi.riferimento_precedente and analisi.argomento_riferito:
        ha_nuovi = any(e.dominio.lower() != "generale" for e in analisi.elementi_richiesti)
        if not ha_nuovi:
            from app import ElementoRichiesto
            analisi.elementi_richiesti = [ElementoRichiesto(
                dominio=analisi.argomento_riferito,
                quantita=None,
                query_ricerca=analisi.argomento_riferito
            )]
            
    # Estrai la query di ricerca combinata per il fallback / checks testuali storici
    queries = [e.query_ricerca for e in analisi.elementi_richiesti if e.query_ricerca]
    testo_per_ricerca = " ".join(queries) if queries else user_query_clean
    if analisi.profilo.tipo_locale:
        stato["tipo_locale"] = analisi.profilo.tipo_locale.lower()
    if analisi.profilo.stile_cucina:
        stato["stile_cucina"] = analisi.profilo.stile_cucina.lower()
    if analisi.profilo.dieta_filtro:
        stato["filtro_dieta"] = analisi.profilo.dieta_filtro.lower()
    if analisi.profilo.senza_affettatrice:
        stato["senza_affettatrice"] = True
    if analisi.profilo.citta:
        stato["citta"] = analisi.profilo.citta

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

    print(f"[DEBUG PROFILO] Tipo Locale: {stato.get('tipo_locale')} | Canale: {stato.get('canale_locale')} | Stile: {stato.get('stile_cucina')} | Dieta: {stato.get('filtro_dieta')} | No Affettatrice: {stato.get('senza_affettatrice')} | Città: {stato.get('citta')}")
    print(f"[DEBUG INTENT] Richiede Composizione: {analisi.richiede_composizione} ({analisi.tipo_richiesta})")

    stato.setdefault("ultimo_piatto_proposto", None)
    stato.setdefault("ricette_mostrate", set())

    # Riconoscimento se la query contiene parole di nuova richiesta o congiunzioni che introducono una variante/nuovo piatto
    ha_parole_nuova_richiesta = any(k in query_bassa_combinata for k in [
        " e ", ", e ", " ma ", ", ma ", "però", "pero ", " invece", "vorrei", "fammi", "fammene", "proponimi",
        "dimmi", "passiamo", "più semplice", "piu semplice", "meno elaborato", "un altro", "un'altra",
        "altra proposta", "un panino", "un burger", "un primo", "un secondo", "un piatto", "uno con", "una con",
        "fammene una", "fammene uno", "e uno", "e una", "pasta", "secondo", "tagliere", "tapas", "pizza", "dessert", "dolce", "birra", "vino", "senza "
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

    contesto_ricetta = None
    ids_da_tracciare = []

    if usa_ricettario:
        try:
            ha_gia_prodotti = len(stato.get("prodotti_mostrati", set())) > 0
            target_salumi, target_formaggi = None, None

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
                    "antipasto": {"antipasto", "antipasti"}
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
        contesto_testuale = costruisci_contesto_ricetta_testuale(contesto_ricetta)
        record_prodotti = []
        for s in contesto_ricetta.get("slot", []):
            if s.get("esito") == "TROVATO" and s.get("prodotto_trovato"):
                ids_da_tracciare.append(s["prodotto_trovato"]["id"])
                record_prodotti.append(s["prodotto_trovato"])
            elif s.get("esito") == "SOSTITUITO" and s.get("sostituto_id"):
                ids_da_tracciare.append(s["sostituto_id"])
                # se abbiamo il dizionario sostituto_trovato potremmo appenderlo, ma id_da_tracciare basta

        # MACCHINA A STATI: Registra il piatto proposto ed esce dal ricettario per le domande successive
        stato["ultimo_piatto_proposto"] = {
            "id_ricetta": tmpl.get("id_ricetta"),
            "nome_piatto": tmpl.get("nome_piatto"),
            "categoria": tmpl.get("categoria"),
            "prodotti_ids": list(ids_da_tracciare),
        }
        if tmpl.get("id_ricetta"):
            stato["ricette_mostrate"].add(tmpl.get("id_ricetta"))
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
        if not any(k in query_bassa_combinata for k in ["piatto", "primo", "secondo", "tagliere", "pasta", "questi"]):
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
                    return 25
                elif getattr(elemento, "quantita", None) is not None:
                    return max(elemento.quantita * 6, 18)
                else:
                    return 18

            elementi_da_cercare = analisi.elementi_richiesti if analisi.elementi_richiesti else []
            if not elementi_da_cercare:
                from app import ElementoRichiesto
                elementi_da_cercare = [ElementoRichiesto(dominio="generale", query_ricerca=testo_per_ricerca)]

            for elem in elementi_da_cercare:
                filtro_rep = elem.reparto.strip().upper() if elem.reparto else None
                filtro_sotto = elem.sottocategoria.strip().upper() if elem.sottocategoria else None

                n_risultati = calcola_n_risultati(elem, analisi.tipo_richiesta)
                
                risultati_parziali = cerca_prodotti(
                    collezione, indice_codici_prodotto, embedder, elem.query_ricerca,
                    n_risultati, indice_fornitori=indice_fornitori,
                    indice_testuale=indice_testuale,
                    filtro_categoria=None,
                    filtro_reparto=filtro_rep,
                    filtro_sottocategoria=filtro_sotto,
                    tipo_locale=stato.get("tipo_locale"),
                    filtro_dieta=stato.get("filtro_dieta"),
                    canale_locale=stato.get("canale_locale"),
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
                        filtro_sottocategoria=filtro_sotto,
                        tipo_locale=stato.get("tipo_locale"),
                        filtro_dieta=stato.get("filtro_dieta"),
                        canale_locale=stato.get("canale_locale"),
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

            # HARD-FILTER DIETETICO DETERMINISTICO (VEGANO)
            if stato.get("filtro_dieta") == "vegano" or any(v in user_query_clean.lower() for v in ["vegano", "vegana", "vegani", "vegane", "100% vegetale", "plant based"]):
                stato["filtro_dieta"] = "vegano"
                FORNITORI_NON_VEG = {
                    "italfish", "medimer", "delfino", "smeralda", "oberto", "adò", "ado'",
                    "la bottega di ado'", "branchi", "franchi", "franchi salumi", "martina franca",
                    "salumi martina franca", "scudellaro", "crucolo", "la casera",
                    "formaggeria toscana", "montanari & gruzza", "coradazzi", "recco",
                    "latte nobile", "san salvatore", "azienda agricola san salvatore 1988",
                    "menodiciotto", "pisani dossi", "pellizziari", "cecinas nieto", "solera"
                }
                PAROLE_NON_VEG = [
                    "carne", "manzo", "maiale", "vitello", "salume", "salumi", "prosciutto", "salame",
                    "pancetta", "guanciale", "lardo", "culatello", "capocollo", "bresaola", "cecina", "jamon",
                    "pesce", "tonno", "salmone", "baccalà", "baccala", "spada", "alici", "acciug", "polpo",
                    "ricci", "riccio", "bottarga", "latte", "formaggio", "formaggi", "burro", "mozzarella",
                    "burrata", "stracciatella", "pecorino", "parmigiano", "grana", "ricotta", "uov", "miele", "strutto"
                ]
                filtrati_veg = []
                for r in record_prodotti:
                    forn_r = str(r["metadata"].get("nome_fornitore", "")).lower()
                    rep_r = str(r["metadata"].get("reparto", "")).upper()
                    if any(fv in forn_r for fv in FORNITORI_NON_VEG) or rep_r in ["CARNE", "SALUMI", "FORMAGGI", "MARE"]:
                        continue
                    doc_r = r.get("document", "").lower()
                    if any(pv in doc_r for pv in PAROLE_NON_VEG):
                        continue
                    filtrati_veg.append(r)
                record_prodotti = filtrati_veg

        except Exception as e:
            return {"reply": f"Errore nella ricerca prodotti: {e}"}

        prodotti_mostrati_per_contesto = stato["prodotti_mostrati"].copy()
        if is_warm_request:
            prodotti_mostrati_per_contesto = {
                pid for pid in prodotti_mostrati_per_contesto
                if not any(r["id"] == pid and ("gelo" in str(r["metadata"].get("categoria_prodotto","")).lower() or "di tria" in str(r["metadata"].get("nome_fornitore","")).lower()) for r in record_prodotti)
            }
        contesto_testuale = costruisci_contesto_testuale(record_prodotti, prodotti_mostrati_per_contesto)

        for r in record_prodotti:
            ids_da_tracciare.append(r["id"])

    # Safety net per richieste specifiche (solo se non siamo in follow-up formati sul piatto corrente)
    if not chiede_dettagli_formati_correnti:
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

    # Creazione prompt finale
    prompt_di_sistema_completo = SYSTEM_PROMPT_NINO + carica_memoria_dinamica()

    info_profilo = []
    if stato.get("tipo_locale"):
        info_profilo.append(f"- Tipo locale: {stato['tipo_locale']}")
    if stato.get("stile_cucina"):
        info_profilo.append(f"- Stile cucina: {stato['stile_cucina']}")
    if stato.get("filtro_dieta"):
        info_profilo.append(f"- Dieta / Vincolo alimentare: {stato['filtro_dieta']}")
    blocco_profilo = "\n    [PROFILO CLIENTE MEMORIZZATO]\n    " + "\n    ".join(info_profilo) + "\n" if info_profilo else ""

    istruzioni_conteggio = []
    if getattr(analisi, "elementi_richiesti", None):
        for elem in analisi.elementi_richiesti:
            if getattr(elem, "quantita", None) is not None:
                istruzioni_conteggio.append(f"- Categoria '{elem.dominio}': devi presentare ESATTAMENTE {elem.quantita} prodotti. Nè uno di più, nè uno di meno.\n  (Eccezione: se non ci sono abbastanza risultati perfetti, per raggiungere la quota {elem.quantita} proponi i prodotti più simili o affini presenti nei DATI RAG piuttosto che dire che non ne abbiamo).")
    
    blocco_conteggi = ""
    if istruzioni_conteggio:
        blocco_conteggi = "\n[VINCOLI DI QUANTITA' OBBLIGATORI (Da rispettare rigorosamente, salvo eccezioni indicate)]\n" + "\n".join(istruzioni_conteggio) + "\n"

    prompt_finale = f"""{blocco_profilo}{blocco_conteggi}
    [DATI RAG ESTRATTI DAL CATALOGO - USA QUESTE INFO PER RISPONDERE]
    {contesto_testuale}

    [DOMANDA DELL'UTENTE]
    {user_query}
    """

    max_messaggi = MAX_SCAMBI_STORICO * 2
    if len(stato["storico"]) > max_messaggi:
        stato["storico"] = stato["storico"][-max_messaggi:]

    # Modello da usare per le risposte utente: Gemini 3.5 Flash-Lite con fallback su Gemini 3.7 Flash
    modello_da_usare = os.getenv("MODELLO_RISPOSTA", MODELLO_PRINCIPALE)

    # Generazione risposta con ciclo di Auto-Correzione (Reflection)
    for tentativo_riflessione in range(2):
        chat_session = client_genai.chats.create(
            model=modello_da_usare,
            config=types.GenerateContentConfig(system_instruction=prompt_di_sistema_completo, temperature=0.3),
            history=stato["storico"]
        )
    
        response = None
        for tentat in range(3):
            try:
                response = chat_session.send_message(prompt_finale)
                break
            except Exception as e:
                err_msg = str(e)
                if any(k in err_msg for k in ["429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"]):
                    if modello_da_usare != MODELLO_FALLBACK:
                        print(f"[ATTENZIONE] Fallback da {modello_da_usare} a {MODELLO_FALLBACK} per errore: {err_msg[:80]}")
                        modello_da_usare = MODELLO_FALLBACK
                        try:
                            chat_session = client_genai.chats.create(
                                model=MODELLO_FALLBACK,
                                config=types.GenerateContentConfig(system_instruction=prompt_di_sistema_completo, temperature=0.3),
                                history=stato["storico"]
                            )
                            response = chat_session.send_message(prompt_finale)
                            break
                        except Exception as fb_err:
                            err_msg = str(fb_err)
                    if tentat < 2:
                        time.sleep(3 * (tentat + 1))
                        continue
                return {"reply": f"Errore del modello: {e}"}
    
        testo_pulito = response.text or ""
        testo_pulito = re.sub(r'\b[Ss]ottofondo\b', 'sottovuoto', testo_pulito)
        testo_pulito = re.sub(r'PRODOTTI\s+SOFOUND', 'PRODOTTI SOFOOD', testo_pulito, flags=re.IGNORECASE)
        testo_pulito = re.sub(r'^\s*#{1,6}\s*(.+)$', r'**\1**', testo_pulito, flags=re.MULTILINE)
        testo_pulito = re.sub(r'(\s*[\*\-]\s*)\*{3,}', r'\1**', testo_pulito)
        testo_pulito = re.sub(r'\*{4,}', '**', testo_pulito)
        testo_pulito = re.sub(r'\*\*\s*\*\*', '', testo_pulito)
        testo_pulito = re.sub(r'\*\*([^\n*]{1,40}?)\s+-\s+([^\n*]+?)\*\*', r'**\2**', testo_pulito)
        testo_pulito = re.sub(r'(\*\*[^*]*?)\s*\b(?:[Ss]ottovuoto|[Ss]/[Vv]|[Aa][Tt][Mm]|[Ss]/[Oo])\b\s*([^*]*?\*\*)', r'\1\2', testo_pulito)
        testo_pulito = re.sub(r'\*\*([^*]+?)\*\*', lambda m: f'**{m.group(1).strip()}**', testo_pulito)
        testo_pulito = re.sub(r'\bnel\s+catalogo\s+di\s+questo\s+turno\b', 'a catalogo', testo_pulito, flags=re.IGNORECASE)
        testo_pulito = re.sub(r'\bin\s+questo\s+turno\b', 'al momento', testo_pulito, flags=re.IGNORECASE)
        testo_pulito = re.sub(r'\bnel\s+contesto\s+(?:fornito|a\s+disposizione)\b', 'a catalogo', testo_pulito, flags=re.IGNORECASE)
        testo_pulito = re.sub(r'\bdi\s+questo\s+turno\b', 'del catalogo', testo_pulito, flags=re.IGNORECASE)
    
        # 6. Garante Immagini (Ridotto per evitare spam)
        # L'immagine viene iniettata SOLO SE:
        # 1. L'utente ha chiesto esplicitamente una foto/immagine
        # 2. Oppure se stiamo parlando di UN SINGOLO prodotto (es. ricerca molto specifica)
        richiede_foto = any(w in user_query.lower() for w in ["foto", "immagine", "immagini", "vederlo", "fotografia", "fammelo vedere"])
        tutti_record = list(record_prodotti) if 'record_prodotti' in locals() else []
        mostra_immagini = richiede_foto or len(tutti_record) == 1
        
        if mostra_immagini:
            for r in tutti_record:
                meta_r = r.get("metadata", {})
                p_img = str(meta_r.get("percorso_immagine", "")).strip()
                ha_img = meta_r.get("ha_immagine_primaria") and p_img and p_img.lower() not in ("", "nan", "none", "false") and os.path.exists(p_img)
                if ha_img and p_img not in testo_pulito:
                    prima_l = r.get("document", "").splitlines()[0] if r.get("document") else ""
                    nome_p = pulisci_nome_commerciale(prima_l, meta_r.get("nome_fornitore", "")).strip().lower()
                    forn_p = str(meta_r.get("nome_fornitore", "")).strip().lower()
                    righe = testo_pulito.splitlines()
                    nuove_righe = []
                    inserito = False
                    for riga in righe:
                        nuove_righe.append(riga)
                        if not inserito and riga.strip().startswith(("-", "*", "•")):
                            riga_low = riga.lower()
                            parole_chiave_nome = [w for w in re.findall(r'\w+', nome_p) if len(w) > 3]
                            
                            match_count = sum(1 for w in parole_chiave_nome if w in riga_low)
                            soglia = min(2, len(parole_chiave_nome)) if parole_chiave_nome else 0
                            match_forn = forn_p in riga_low if len(forn_p) > 3 else False
                            
                            if match_count >= soglia or (match_count >= 1 and match_forn):
                                nuove_righe.append(f"[IMG: {p_img}]")
                                inserito = True
                    if inserito:
                        testo_pulito = "\n".join(nuove_righe)
            testo_pulito = re.sub(r'(\[IMG:\s*[^\]]+\])(?:\s*\1)+', r'\1', testo_pulito)
        else:
            # Strip any [IMG] tags that the LLM might have generated on its own
            testo_pulito = re.sub(r'\s*\[IMG:\s*[^\]]+\]\s*', ' ', testo_pulito)
            
        # Clean up any excessive newlines left behind by stripping
        testo_pulito = re.sub(r'\n{3,}', '\n\n', testo_pulito)
        testo_pulito = re.sub(r' \n', '\n', testo_pulito)
        testo_pulito = re.sub(r'\n ,', ',', testo_pulito)
        
        # --- REFLECTION LOOP (Auto-Correzione Multi-Dominio) ---
        domini_mancanti = []
        risposta_lower = testo_pulito.lower()
        if getattr(analisi, "elementi_richiesti", None):
            for elem in analisi.elementi_richiesti:
                if elem.dominio.lower() != "generale" and elem.dominio.lower() not in risposta_lower:
                    domini_mancanti.append(elem.dominio)

        if domini_mancanti and tentativo_riflessione == 0:
            prompt_riflessione = f"L'utente aveva richiesto esplicitamente di parlare anche di: {', '.join(domini_mancanti)}.\nLa tua risposta copre queste categorie o le ha dimenticate?\nRispondi solo 'MANCA' se le hai dimenticate, altrimenti 'OK'.\n\nRISPOSTA:\n{testo_pulito}"
            res_rif = client_genai.models.generate_content(model=MODELLO_FALLBACK, contents=prompt_riflessione)
            if "MANCA" in (res_rif.text or "").upper():
                print(f"[REFLECTION] Domini mancanti: {domini_mancanti}. Rigenero.")
                prompt_finale += f"\n\nATTENZIONE: Nella risposta precedente ti sei scordato di trattare queste categorie richieste dall'utente: {', '.join(domini_mancanti)}. Riprova bilanciando la risposta in modo che copra TUTTE le richieste in egual misura, senza bloccarti su una sola categoria."
                continue
                
        break

    stato["storico"].append(types.Content(role="user", parts=[types.Part.from_text(text=user_query)]))
    stato["storico"].append(types.Content(role="model", parts=[types.Part.from_text(text=testo_pulito)]))

    timestamp_ora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stato["log_chat"].append({"ruolo": "utente", "testo": user_query, "timestamp": timestamp_ora})
    stato["log_chat"].append({"ruolo": "nino", "testo": testo_pulito, "timestamp": timestamp_ora})
    stato["contatore_messaggi"] += 1

    if stato["contatore_messaggi"] % FREQUENZA_SALVATAGGIO_LOG == 0:
        salva_log_chat(sid, stato)

    return {"reply": testo_pulito}


@app.route("/chat", methods=["POST"])
def chat():
    user_query = request.json.get("message", "") if request.json else ""

    if not user_query:
        return jsonify({"reply": "Messaggio vuoto."})

    stato, sid = ottieni_sessione()

    # Intercettazione della correzione di addestramento
    comando_normalizzato = user_query.strip().lower()
    if comando_normalizzato.startswith("!impara"):
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


@app.route("/logo.png")
def servi_logo():
    """Restituisce il logo aziendale ufficiale So Food per l'avatar WhatsApp"""
    p = os.path.join(os.path.dirname(__file__), "static", "logo_sofood.png")
    if os.path.exists(p):
        return send_file(p, mimetype="image/png")
    return "Logo non trovato", 404


@app.route("/immagine")
def servi_immagine():
    """Questa rotta riceve il percorso C:\\... dal browser e gli invia il file JPEG reale"""
    percorso = request.args.get("path")
    if percorso and os.path.exists(percorso):
        return send_file(percorso)
    return "Immagine non trovata o percorso non valido", 404


# ====================================================================
# FRONTEND HTML & JS (REPLICA FEDELE WHATSAPP CHAT)
# ====================================================================
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="it">
<head>
    <title>WhatsApp • So Food</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <style>
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        html, body {
            margin: 0;
            padding: 0;
            height: 100%;
            width: 100%;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #d1d7db;
            color: #111b21;
        }
        
        .wa-app {
            display: flex;
            flex-direction: column;
            height: 100dvh;
            max-width: 820px;
            margin: 0 auto;
            background: #ffffff;
            box-shadow: 0 4px 24px rgba(0,0,0,0.15);
            position: relative;
        }

        /* HEADER WHATSAPP */
        .wa-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 14px;
            background-color: #008069;
            color: #ffffff;
            z-index: 10;
            box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        }
        .wa-header-left {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .wa-back-btn {
            background: none;
            border: none;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            padding: 4px;
            margin-right: -4px;
        }
        .wa-avatar {
            width: 42px;
            height: 42px;
            border-radius: 50%;
            background: #ffffff; /* Sfondo bianco per il logo */
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            flex-shrink: 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.2);
            cursor: pointer;
            border: 1px solid rgba(255,255,255,0.4);
        }
        .wa-avatar img {
            width: 90%;
            height: 90%;
            object-fit: contain;
        }
        .wa-contact-info {
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
        .wa-name {
            font-size: 16.5px;
            font-weight: 600;
            line-height: 1.2;
            letter-spacing: -0.1px;
        }
        .wa-status {
            font-size: 12.5px;
            color: #d1fae5;
            margin-top: 1px;
            display: flex;
            align-items: center;
            gap: 4px;
            transition: color 0.2s;
        }
        .wa-header-right {
            display: flex;
            align-items: center;
            gap: 16px;
            color: #ffffff;
        }
        .wa-icon-btn {
            background: none;
            border: none;
            color: #ffffff;
            cursor: pointer;
            padding: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0.9;
        }
        .wa-icon-btn:hover { opacity: 1; }

        /* CHAT AREA WHATSAPP */
        #chatbox {
            flex: 1;
            overflow-y: auto;
            padding: 12px 16px 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            background-color: #efeae2;
            background-image: 
                radial-gradient(#d5ceb9 0.85px, transparent 0.85px), 
                radial-gradient(#d5ceb9 0.85px, #efeae2 0.85px);
            background-size: 26px 26px;
            background-position: 0 0, 13px 13px;
        }

        /* SECURITY PILL & DATE PILL */
        .wa-security-pill {
            align-self: center;
            background-color: #ffeecd;
            color: #54656f;
            font-size: 11.5px;
            line-height: 1.4;
            padding: 6px 14px;
            border-radius: 8px;
            text-align: center;
            max-width: 90%;
            margin: 4px auto 8px auto;
            box-shadow: 0 1px 1px rgba(11,20,26,0.08);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        .wa-date-pill {
            align-self: center;
            background-color: #ffffff;
            color: #54656f;
            font-size: 11.5px;
            font-weight: 500;
            padding: 4px 12px;
            border-radius: 7.5px;
            box-shadow: 0 1px 1px rgba(11,20,26,0.1);
            margin: 2px auto 10px auto;
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }

        /* BUBBLES */
        .msg-row {
            display: flex;
            width: 100%;
            margin: 2px 0;
        }
        .msg-row.nino { justify-content: flex-start; }
        .msg-row.tu { justify-content: flex-end; }

        .wa-bubble {
            max-width: 82%;
            padding: 6px 9px 6px 9px;
            font-size: 14.2px;
            line-height: 1.42;
            word-wrap: break-word;
            position: relative;
            box-shadow: 0 1px 0.5px rgba(11,20,26,0.13);
        }
        .wa-bubble.nino {
            background-color: #ffffff;
            color: #111b21;
            border-radius: 7.5px;
            border-top-left-radius: 0;
        }
        .wa-bubble.tu {
            background-color: #d9fdd3;
            color: #111b21;
            border-radius: 7.5px;
            border-top-right-radius: 0;
        }

        .bubble-content {
            margin-bottom: 2px;
        }
        .wa-heading {
            font-weight: 700;
            font-size: 15px;
            margin-top: 8px;
            margin-bottom: 4px;
            color: #075e54;
            display: block;
        }
        .wa-heading:first-child { margin-top: 0; }
        .wa-bullet {
            display: flex;
            align-items: flex-start;
            margin: 3px 0;
            line-height: 1.4;
        }
        .wa-dot-bullet {
            margin-right: 6px;
            font-weight: bold;
            color: #128c7e;
            flex-shrink: 0;
        }
        .wa-num-bullet {
            margin-right: 6px;
            font-weight: bold;
            color: #128c7e;
            flex-shrink: 0;
        }
        .wa-divider {
            border: 0;
            border-top: 1px solid rgba(0,0,0,0.12);
            margin: 8px 0;
        }
        .bubble-meta {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 3px;
            float: right;
            margin-left: 10px;
            margin-top: 4px;
            font-size: 11px;
            color: #667781;
            user-select: none;
        }
        .ticks {
            color: #53bdeb; /* Doppia spunta blu WhatsApp */
            font-size: 13px;
            line-height: 1;
            font-weight: bold;
        }

        /* IMMAGINI NELLA CHAT */
        .product-img {
            width: 100%;
            max-width: 300px;
            aspect-ratio: 1 / 1;
            object-fit: contain;
            border-radius: 6px;
            margin: 6px auto;
            background: #ffffff;
            border: 1px solid #e9edef;
            display: block;
            cursor: pointer;
            transition: opacity 0.15s ease;
        }
        .product-img:hover { opacity: 0.94; }

        /* AUDIO PLAYER IN WHATSAPP STYLE */
        .wa-audio-container {
            display: flex;
            flex-direction: column;
            gap: 6px;
            min-width: 250px;
            max-width: 320px;
            padding: 2px 0;
        }
        .wa-audio-player {
            width: 100%;
            height: 38px;
            border-radius: 20px;
            outline: none;
        }
        .wa-transcription-box {
            background: rgba(0, 128, 105, 0.08);
            border-left: 3px solid #008069;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 13px;
            color: #111b21;
            font-style: italic;
            line-height: 1.35;
            margin-top: 4px;
        }
        .wa-transcription-label {
            font-size: 11px;
            font-weight: 600;
            color: #008069;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            display: flex;
            align-items: center;
            gap: 4px;
            margin-bottom: 2px;
            font-style: normal;
        }

        /* INPUT AREA WHATSAPP */
        .wa-input-bar {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 10px;
            background-color: #f0f2f5;
            border-top: 1px solid #e9edef;
            z-index: 10;
        }
        .wa-input-icons {
            display: flex;
            align-items: center;
            gap: 6px;
            color: #54656f;
        }
        .wa-action-icon {
            background: none;
            border: none;
            color: #54656f;
            cursor: pointer;
            padding: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            transition: background 0.15s;
        }
        .wa-action-icon:hover { background: #e2e5e9; }

        .wa-input-wrapper {
            flex: 1;
            display: flex;
            align-items: center;
            background: #ffffff;
            border-radius: 22px;
            padding: 2px 14px;
            box-shadow: 0 1px 1px rgba(0,0,0,0.06);
        }
        input#userInput {
            width: 100%;
            padding: 9px 0;
            border: none;
            outline: none;
            font-size: 15px;
            color: #111b21;
            background: transparent;
        }
        input#userInput::placeholder { color: #8696a0; }

        /* BARRA DI REGISTRAZIONE VOCALE WHATSAPP */
        .wa-recording-bar {
            display: none;
            flex: 1;
            align-items: center;
            justify-content: space-between;
            background: #ffffff;
            border-radius: 22px;
            padding: 6px 14px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.08);
        }
        .wa-recording-info {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .wa-rec-indicator {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background-color: #ea4335;
            animation: waPulseRec 0.8s infinite alternate ease-in-out;
        }
        @keyframes waPulseRec {
            from { opacity: 1; transform: scale(1.1); }
            to { opacity: 0.25; transform: scale(0.8); }
        }
        .wa-rec-timer {
            font-size: 15px;
            font-weight: 600;
            color: #111b21;
            font-variant-numeric: tabular-nums;
        }
        .wa-rec-label {
            font-size: 13px;
            color: #667781;
        }
        .wa-rec-actions {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .wa-rec-btn {
            background: none;
            border: none;
            cursor: pointer;
            padding: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            transition: background 0.15s;
        }
        .wa-rec-btn:hover { background: #f0f2f5; }
        .wa-rec-btn.send {
            background-color: #008069;
            color: #ffffff;
        }
        .wa-rec-btn.send:hover { background-color: #006a57; }

        .wa-btn-group {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        button#micBtn, button#sendBtn {
            width: 42px;
            height: 42px;
            border-radius: 50%;
            background-color: #008069;
            color: white;
            border: none;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            flex-shrink: 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.18);
            transition: background-color 0.15s, transform 0.1s;
        }
        button#micBtn:hover, button#sendBtn:hover { background-color: #006a57; }
        button#micBtn:active, button#sendBtn:active { transform: scale(0.95); }

        /* LOADING ANIMATION WHATSAPP */
        .wa-typing {
            display: flex;
            align-items: center;
            gap: 4px;
            padding: 4px 2px;
        }
        .wa-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #8696a0;
            animation: waBounce 1.3s infinite ease-in-out both;
        }
        .wa-dot:nth-child(1) { animation-delay: -0.32s; }
        .wa-dot:nth-child(2) { animation-delay: -0.16s; }
        @keyframes waBounce {
            0%, 80%, 100% { transform: scale(0); }
            40% { transform: scale(1); }
        }

        /* MODALE LIGHTBOX WHATSAPP */
        #imgModal {
            display: none;
            position: fixed;
            z-index: 9999;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(11, 20, 26, 0.94);
            justify-content: center;
            align-items: center;
            flex-direction: column;
            cursor: pointer;
        }
        #modalImg {
            max-width: 90%;
            max-height: 85vh;
            border-radius: 8px;
            object-fit: contain;
            box-shadow: 0 8px 30px rgba(0,0,0,0.5);
            background: #ffffff;
            padding: 4px;
        }
        .zoom-hint {
            color: #8696a0;
            margin-top: 14px;
            font-size: 13px;
        }
    </style>
</head>
<body>
    <div class="wa-app">
        <!-- HEADER WHATSAPP -->
        <header class="wa-header">
            <div class="wa-header-left">
                <button class="wa-back-btn" aria-label="Indietro">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
                </button>
                <div class="wa-avatar" onclick="apriZoom('/logo.png')" title="Visualizza logo So Food">
                    <img src="/logo.png" alt="So Food">
                </div>
                <div class="wa-contact-info">
                    <div class="wa-name">So Food</div>
                    <div class="wa-status" id="waStatus">online</div>
                </div>
            </div>
            <div class="wa-header-right">
                <button class="wa-icon-btn" title="Videochiamata">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>
                </button>
                <button class="wa-icon-btn" title="Chiamata">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg>
                </button>
                <button class="wa-icon-btn" title="Cerca">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
                </button>
                <button class="wa-icon-btn" title="Altre opzioni">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/></svg>
                </button>
            </div>
        </header>

        <!-- CHAT AREA -->
        <div id="chatbox">
            <div class="wa-security-pill">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="#54656f"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></svg>
                <span>I messaggi con questo account aziendale sono protetti con crittografia end-to-end.</span>
            </div>
            <div class="wa-date-pill">OGGI</div>
        </div>

        <!-- INPUT BAR WHATSAPP -->
        <div class="wa-input-bar">
            <div class="wa-input-icons">
                <button class="wa-action-icon" title="Emoji">
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm3.5-9c.83 0 1.5-.67 1.5-1.5S16.33 8 15.5 8 14 8.67 14 9.5s.67 1.5 1.5 1.5zm-7 0c.83 0 1.5-.67 1.5-1.5S9.33 8 8.5 8 7 8.67 7 9.5 7.67 11 8.5 11zm3.5 6.5c2.33 0 4.31-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5z"/></svg>
                </button>
                <input type="file" id="audioFileInput" accept="audio/*,.mp3,.wav,.m4a,.ogg,.webm,.aac" style="display:none" onchange="caricaFileAudio(this)">
                <button class="wa-action-icon" title="Allega audio o file" onclick="document.getElementById('audioFileInput').click()">
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/></svg>
                </button>
            </div>
            
            <!-- CAMPO TESTO STANDARD -->
            <div class="wa-input-wrapper" id="inputWrapper">
                <input type="text" id="userInput" placeholder="Scrivi un messaggio" onkeypress="if(event.key === 'Enter') invia()" autocomplete="off">
            </div>

            <!-- BARRA REGISTRAZIONE LIVE (stile WhatsApp) -->
            <div class="wa-recording-bar" id="recordingBar">
                <div class="wa-recording-info">
                    <div class="wa-rec-indicator"></div>
                    <span class="wa-rec-timer" id="recTimer">0:00</span>
                    <span class="wa-rec-label">Registrazione...</span>
                </div>
                <div class="wa-rec-actions">
                    <button class="wa-rec-btn" onclick="annullaRegistrazione()" title="Annulla registrazione">
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="#ea4335"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                    </button>
                    <button class="wa-rec-btn send" onclick="fermaEInviaRegistrazione()" title="Invia messaggio vocale">
                        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                    </button>
                </div>
            </div>

            <!-- DOPPIO PULSANTE: MICROFONO E INVIA -->
            <div class="wa-btn-group" id="btnGroup">
                <button id="micBtn" onclick="avviaRegistrazione()" aria-label="Registra messaggio vocale" title="Registra messaggio vocale">
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5-3c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
                </button>
                <button id="sendBtn" onclick="invia()" aria-label="Invia messaggio" title="Invia messaggio">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                </button>
            </div>
        </div>
    </div>

    <!-- MODALE LIGHTBOX PER FOTO AD ALTA RISOLUZIONE -->
    <div id="imgModal" onclick="this.style.display='none'">
        <img id="modalImg" src="">
        <div class="zoom-hint">Tocca ovunque per chiudere</div>
    </div>

    <script>
        let mediaRecorder = null;
        let audioChunks = [];
        let recInterval = null;
        let recSeconds = 0;

        function getOrario() {
            let d = new Date();
            let hh = String(d.getHours()).padStart(2, '0');
            let mm = String(d.getMinutes()).padStart(2, '0');
            return `${hh}:${mm}`;
        }

        window.onload = () => {
            aggiungiMessaggio("Ciao! Sono Nino, il tuo consulente commerciale So Food.<br>Come posso aiutarti oggi per il tuo locale?", "nino");
        };

        function apriZoom(src) {
            document.getElementById("modalImg").src = src;
            document.getElementById("imgModal").style.display = "flex";
        }

        async function avviaRegistrazione() {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                alert("Il tuo browser non supporta la registrazione diretta del microfono. Puoi allegare un file audio usando l'icona con la graffetta!");
                return;
            }

            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                audioChunks = [];

                let options = {};
                if (window.MediaRecorder && MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
                    options = { mimeType: "audio/webm;codecs=opus" };
                } else if (window.MediaRecorder && MediaRecorder.isTypeSupported("audio/webm")) {
                    options = { mimeType: "audio/webm" };
                } else if (window.MediaRecorder && MediaRecorder.isTypeSupported("audio/mp4")) {
                    options = { mimeType: "audio/mp4" };
                }

                mediaRecorder = new MediaRecorder(stream, options);
                mediaRecorder.ondataavailable = (e) => {
                    if (e.data && e.data.size > 0) audioChunks.push(e.data);
                };

                mediaRecorder.onstop = () => {
                    stream.getTracks().forEach(track => track.stop());
                    if (mediaRecorder.wasCancelled || audioChunks.length === 0) return;
                    const mime = mediaRecorder.mimeType || "audio/webm";
                    const blob = new Blob(audioChunks, { type: mime });
                    inviaBlobAudio(blob, mime.includes("mp4") ? "vocale.m4a" : "vocale.webm");
                };

                mediaRecorder.wasCancelled = false;
                mediaRecorder.start();

                document.getElementById("inputWrapper").style.display = "none";
                document.getElementById("btnGroup").style.display = "none";
                document.getElementById("recordingBar").style.display = "flex";

                recSeconds = 0;
                document.getElementById("recTimer").textContent = "0:00";
                recInterval = setInterval(() => {
                    recSeconds++;
                    let m = Math.floor(recSeconds / 60);
                    let s = String(recSeconds % 60).padStart(2, '0');
                    document.getElementById("recTimer").textContent = `${m}:${s}`;
                }, 1000);

            } catch (err) {
                console.error("Accesso microfono negato o non disponibile:", err);
                alert("Impossibile accedere al microfono. Verifica che i permessi del browser siano attivi.");
            }
        }

        function fermaEInviaRegistrazione() {
            if (recInterval) clearInterval(recInterval);
            if (mediaRecorder && mediaRecorder.state === "recording") {
                mediaRecorder.stop();
            }
            ripristinaBarraInput();
        }

        function annullaRegistrazione() {
            if (recInterval) clearInterval(recInterval);
            if (mediaRecorder && mediaRecorder.state === "recording") {
                mediaRecorder.wasCancelled = true;
                mediaRecorder.stop();
            }
            ripristinaBarraInput();
        }

        function ripristinaBarraInput() {
            document.getElementById("recordingBar").style.display = "none";
            document.getElementById("inputWrapper").style.display = "flex";
            document.getElementById("btnGroup").style.display = "flex";
        }

        function caricaFileAudio(input) {
            if (input.files && input.files[0]) {
                let file = input.files[0];
                inviaBlobAudio(file, file.name);
                input.value = "";
            }
        }

        function formattaImmaginiNino(rispostaNino) {
            return rispostaNino.replace(/\[IMG:\s*(.*?)\]/g, function(match, percorso) {
                let p = (percorso || "").trim();
                if (!p || p.toUpperCase().includes("DISPONIBILE") || p.toUpperCase().includes("NESSUNA") || (!p.includes("\\") && !p.includes("/"))) {
                    return "";
                }
                let urlSicuro = "/immagine?path=" + encodeURIComponent(p);
                return `<img src="${urlSicuro}" class="product-img" onclick="apriZoom('${urlSicuro}')" title="Tocca per ingrandire" loading="lazy">`;
            });
        }

        async function inviaBlobAudio(blob, nomeFile = "vocale.webm") {
            let audioUrlLocale = URL.createObjectURL(blob);
            let msgId = "vocale-" + Date.now();

            aggiungiMessaggio("", "tu", {
                id: msgId,
                audioUrl: audioUrlLocale,
                transcription: "Trascrizione in corso..."
            });

            let statusEl = document.getElementById("waStatus");
            if (statusEl) {
                statusEl.textContent = "sta ascoltando il vocale...";
                statusEl.style.color = "#a7f3d0";
            }

            let chatbox = document.getElementById("chatbox");
            let loadingId = "loading-" + Date.now();
            chatbox.innerHTML += `
                <div id="${loadingId}" class="msg-row nino">
                    <div class="wa-bubble nino">
                        <div class="wa-typing">
                            <span class="wa-dot"></span><span class="wa-dot"></span><span class="wa-dot"></span>
                        </div>
                    </div>
                </div>`;
            chatbox.scrollTop = chatbox.scrollHeight;

            let formData = new FormData();
            formData.append("audio", blob, nomeFile);

            try {
                let response = await fetch('/chat_audio', {
                    method: 'POST',
                    body: formData
                });

                let data = await response.json();
                
                let elTrascrizione = document.getElementById("trascrizione-" + msgId);
                if (elTrascrizione) {
                    if (data.transcription && data.transcription.trim()) {
                        elTrascrizione.textContent = `"${data.transcription.trim()}"`;
                    } else {
                        elTrascrizione.innerHTML = `<i>Nessun parlato rilevato</i>`;
                    }
                }

                let rispostaNino = formattaImmaginiNino(data.reply || "");

                let loadingEl = document.getElementById(loadingId);
                if (loadingEl) loadingEl.remove();

                if (statusEl) {
                    statusEl.textContent = "online";
                    statusEl.style.color = "#d1fae5";
                }

                aggiungiMessaggio(rispostaNino, "nino");
            } catch (err) {
                console.error("Errore invio vocale:", err);
                let loadingEl = document.getElementById(loadingId);
                if (loadingEl) loadingEl.remove();
                if (statusEl) {
                    statusEl.textContent = "online";
                    statusEl.style.color = "#d1fae5";
                }
                aggiungiMessaggio("<i>C'è stato un problema durante l'invio o la trascrizione del vocale. Riprova!</i>", "nino");
            }
        }

        async function invia() {
            let input = document.getElementById("userInput");
            let testo = input.value;
            if (!testo.trim()) return;

            aggiungiMessaggio(testo, "tu");
            input.value = "";

            let statusEl = document.getElementById("waStatus");
            if (statusEl) {
                statusEl.textContent = "sta scrivendo...";
                statusEl.style.color = "#a7f3d0";
            }

            let chatbox = document.getElementById("chatbox");
            let loadingId = "loading-" + Date.now();
            chatbox.innerHTML += `
                <div id="${loadingId}" class="msg-row nino">
                    <div class="wa-bubble nino">
                        <div class="wa-typing">
                            <span class="wa-dot"></span><span class="wa-dot"></span><span class="wa-dot"></span>
                        </div>
                    </div>
                </div>`;
            chatbox.scrollTop = chatbox.scrollHeight;

            try {
                let response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: testo })
                });

                let data = await response.json();
                let rispostaNino = formattaImmaginiNino(data.reply || "");

                let loadingEl = document.getElementById(loadingId);
                if (loadingEl) loadingEl.remove();

                if (statusEl) {
                    statusEl.textContent = "online";
                    statusEl.style.color = "#d1fae5";
                }

                aggiungiMessaggio(rispostaNino, "nino");
            } catch (error) {
                let loadingEl = document.getElementById(loadingId);
                if (loadingEl) loadingEl.remove();
                if (statusEl) {
                    statusEl.textContent = "online";
                    statusEl.style.color = "#d1fae5";
                }
                aggiungiMessaggio("<i>Scusa, c'è stato un piccolo errore di connessione. Riprova tra poco!</i>", "nino");
            }
        }

        function aggiungiMessaggio(testo, classe, opzioniAudio = null) {
            let chatbox = document.getElementById("chatbox");
            let orario = getOrario();
            let ticks = (classe === "tu") ? `<span class="ticks">✓✓</span>` : "";

            let contenutoHtml = "";
            if (opzioniAudio) {
                contenutoHtml += `
                    <div class="wa-audio-container">
                        <audio controls class="wa-audio-player" src="${opzioniAudio.audioUrl}"></audio>
                        <div class="wa-transcription-box">
                            <div class="wa-transcription-label">
                                <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5-3c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
                                Vocale Trascritto
                            </div>
                            <span id="trascrizione-${opzioniAudio.id || ''}">${opzioniAudio.transcription || ''}</span>
                        </div>
                    </div>`;
            }

            if (testo) {
                let tf = testo;
                // 1. Titoli markdown (###, ##, #) -> wa-heading
                tf = tf.replace(/^###\s*(.*?)$/gm, '<div class="wa-heading">$1</div>');
                tf = tf.replace(/^##\s*(.*?)$/gm, '<div class="wa-heading">$1</div>');
                tf = tf.replace(/^#\s*(.*?)$/gm, '<div class="wa-heading">$1</div>');
                // 2. Linee divisorie orizzontali (---)
                tf = tf.replace(/^---$/gm, '<hr class="wa-divider">');
                // 3. Elenchi puntati (* o - a inizio riga)
                tf = tf.replace(/^[\*\-]\s+(.*?)$/gm, '<div class="wa-bullet"><span class="wa-dot-bullet">•</span><span>$1</span></div>');
                // 4. Elenchi numerati (1., 2., ecc.)
                tf = tf.replace(/^(\d+)[\.\)]\s+(.*?)$/gm, '<div class="wa-bullet"><span class="wa-num-bullet">$1.</span><span>$2</span></div>');
                // 5. Bold & Italic combinato: ***testo***
                tf = tf.replace(/\*\*\*(.*?)\*\*\*/g, "<strong><em>$1</em></strong>");
                // 6. Bold: **testo**
                tf = tf.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
                // 7. Italic: *testo* o _testo_ (senza toccare tag già convertiti)
                tf = tf.replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, "<em>$1</em>");
                tf = tf.replace(/\b_([^_\n]+?)_\b/g, "<em>$1</em>");
                // 8. A capo
                tf = tf.replace(/\n\n+/g, "<br><br>");
                tf = tf.replace(/\n/g, "<br>");
                // 9. Pulizia spazi extra tra blocchi div/hr e br
                tf = tf.replace(/<\/div><br>/g, "</div>");
                tf = tf.replace(/<hr class="wa-divider"><br>/g, '<hr class="wa-divider">');

                contenutoHtml += `<div class="bubble-content">${tf}</div>`;
            }

            let bubbleHtml = `
                <div class="msg-row ${classe}">
                    <div class="wa-bubble ${classe}">
                        ${contenutoHtml}
                        <div class="bubble-meta">
                            <span>${orario}</span>${ticks}
                        </div>
                    </div>
                </div>`;

            chatbox.innerHTML += bubbleHtml;
            chatbox.scrollTop = chatbox.scrollHeight;
        }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print("="*70)
    print("[OK] SERVER FLASK AVVIATO CON SUCCESSO!")
    print(">>> Apri il tuo browser e vai all'indirizzo: http://127.0.0.1:5000")
    print("="*70)
    app.run(debug=False, use_reloader=False)