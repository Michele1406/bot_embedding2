# Implementation Plan — Ricettario e Logica di Composizione (Nino / SO FOOD)

## Come usare questo file in Antigravity

1. Apri il progetto in Antigravity, seleziona **Claude Opus 4.6 (thinking)** come modello dell'agente principale.
2. Metti questo file e `Ricettario_SO_FOOD.xlsx` nella root del progetto (o linkali con `@`) prima di avviare il task.
3. Prompt iniziale suggerito per l'Agent Manager:

   > Leggi `implementazione_ricettario_antigravity.md` per intero prima di generare il tuo Task List. È già un piano di implementazione completo e in ordine di esecuzione: Task 1 → Task 6. Genera il tuo `implementation_plan.md` a partire da questo documento (non ripartire da zero), poi esegui i task in sequenza, uno per volta, fermandoti per la mia review dopo ogni task che tocca `system_prompt_v2.py` o `app.py`. Rispetta alla lettera i "Guardrail" indicati: sono vincoli di prodotto già discussi e decisi, non negoziabili in autonomia.

4. Politica di review consigliata: **non** impostare "Always Proceed" sui Task 4 e 5 (system prompt e flusso principale) — sono i punti dove un errore silenzioso è più costoso da individuare a runtime.

---

## Goal

Collegare `Ricettario_SO_FOOD.xlsx` (230 ricette normalizzate, già pronto) alla pipeline esistente di Nino, chiudendo le 5 macro-funzioni già concordate: decomposizione query, caricamento ricettario in una collezione Chroma dedicata, slot-filling ricetta→catalogo, guardrail di canale, fallback/sostituzioni deterministiche. Il codice target esiste già (`app.py`, `retrieval_utils.py`, `system_prompt_v2.py`) e va **esteso**, non riscritto.

## Contesto di prodotto (leggere prima di scrivere codice)

Nino è un assistente B2B per SO FOOD (Bari), Flask + ChromaDB + Gemini. Ha già: ricerca ibrida per codice/fornitore/vettore in `retrieval_utils.py::cerca_prodotti`, sessioni per utente in `app.py`, un system prompt in `system_prompt_v2.py`. In un round di refactor precedente sono stati rimossi un classificatore di intent e liste hardcoded di fornitori "prioritari" perché causavano il bug "Nino propone sempre gli stessi prodotti" — **non vanno reintrodotti in nessuna forma**, nemmeno per implementare i task sotto.

## Files to Modify

| File | Tipo di modifica |
|---|---|
| `caricaprodotti_v2.py` | Aggiunta di 2 campi metadato calcolati (quantità normalizzata) |
| `carica_ricettario.py` | Sostituzione completa (nuovo script di caricamento) |
| `retrieval_utils.py` | Aggiunta in coda: 6 nuove funzioni, nessuna esistente va toccata |
| `app.py` | Aggiunta di una funzione di decomposizione + punto di innesto nel flusso di `elabora_messaggio_nino` |
| `system_prompt_v2.py` | Due aggiunte testuali mirate a sezioni esistenti |

**Nota di provenienza:** il Task 0 chiude un punto rimasto aperto dalla primissima valutazione architetturale di questo progetto ("Soluzione A": più metadati strutturati nel catalogo, priorità più alta di tutto il resto) e dalla lezione già documentata nel resoconto — non affidarsi a parole come "gigante" nel nome prodotto per dedurre il formato, perché nel catalogo reale è fuorviante (es. `OLIVE B.D.CERIGNOLA GIGANTI 314ML` è un vasetto piccolo: "Giganti" è la cultivar dell'oliva, non il formato).

## Dependencies

Nessun nuovo pacchetto oltre a quelli già in uso (`pandas`, `chromadb`, `google-genai`, `pydantic` — verificare che `pydantic` sia già in `requirements.txt`, altrimenti aggiungerlo).

## Guardrail (non negoziabili — non derogare in autonomia)

- Il filtro di canale (`ristorante/pizzeria/bottega/pub/bar`) si applica **solo** alla selezione del template ricetta in `trova_template_ricetta`. Mai come pre-filtro sui prodotti del RAG generico altrove nel codice.
- Nessuna quantità/grammatura va aggiunta in nessun punto della UI o del prompt: è deliberatamente assente dal ricettario.
- Il fallback di sostituzione ingrediente è **deterministico nel codice** (soglia di distanza vettoriale), non delegato al giudizio libero del modello in fase di prompt.
- Non introdurre liste hardcoded di fornitori o prodotti "prioritari" per categoria in nessuna delle nuove funzioni.

---

## Task 0 — Quantità normalizzata nei metadati prodotto (`caricaprodotti_v2.py`)

**Goal:** aggiungere al caricamento del catalogo un campo di quantità/formato numerico normalizzato, parsato dal nome o dalla scheda prodotto, così che "proponi il formato grande di default" (Task 5a) possa diventare una scelta deterministica nel codice invece di un'istruzione di prompt che il modello deve dedurre da solo dal nome — lo stesso tipo di fragilità già vista con la regola "Marchio - Prodotto" nel round precedente del progetto.

**Modifiche proposte** (funzione di parsing + arricchimento dei metadati già scritti in fase di caricamento):

```python
import re

# Ordine di priorità dei pattern: dal più specifico al più generico.
# Cattura numero + unità, ignorando parole come "gigante/mignon/maxi" che
# nel catalogo reale descrivono la cultivar/varietà, non il formato.
PATTERN_QUANTITA = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(KG|GR|G|ML|CL|L|LT)\b", re.IGNORECASE
)

FATTORE_A_GRAMMI_O_ML = {
    "KG": 1000, "GR": 1, "G": 1,
    "L": 1000, "LT": 1000, "CL": 10, "ML": 1,
}


def estrai_quantita_normalizzata(nome_prodotto: str, descrizione: str = "") -> dict:
    """
    Ritorna {'valore_normalizzato': float|None, 'unita_base': 'peso'|'volume'|None}.
    valore_normalizzato è sempre in grammi (peso) o millilitri (volume),
    per poter confrontare formati diversi dello stesso prodotto senza
    fidarsi di parole nel nome (es. 'Giganti' = cultivar, non formato).
    """
    testo = f"{nome_prodotto} {descrizione}"
    match = PATTERN_QUANTITA.search(testo)
    if not match:
        return {"valore_normalizzato": None, "unita_base": None}

    valore = float(match.group(1).replace(",", "."))
    unita = match.group(2).upper()
    fattore = FATTORE_A_GRAMMI_O_ML.get(unita, 1)
    unita_base = "volume" if unita in ("ML", "CL", "L", "LT") else "peso"
    return {"valore_normalizzato": valore * fattore, "unita_base": unita_base}


# Nel punto in cui vengono già costruiti i metadati per ogni prodotto
# (dove oggi si popolano nome_fornitore, categoria_prodotto, ecc.), aggiungere:
quantita = estrai_quantita_normalizzata(nome_prodotto, descrizione_prodotto)
meta["quantita_valore_normalizzato"] = quantita["valore_normalizzato"]
meta["quantita_unita_base"] = quantita["unita_base"]
```

**Uso previsto (non implementarlo qui, solo tenerlo a mente per il Task 4):** quando la ricerca ibrida restituisce più varianti di formato dello stesso prodotto (stesso nome commerciale ripulito da `pulisci_nome_commerciale()`, `quantita_valore_normalizzato` diverso), il codice — non il prompt — sceglie quale mettere per primo nel contesto: il valore più alto se il cliente non si è qualificato come bottega, il più basso/medio altrimenti. Questo è un affinamento che si può aggiungere al Task 4 in un secondo momento; il Task 0 si limita a rendere disponibile il dato.

**Testing Strategy:**
- Verifica a campione sul prodotto citato nel resoconto originale (`OLIVE B.D.CERIGNOLA GIGANTI 314ML`): `estrai_quantita_normalizzata` deve restituire `314.0, "volume"`, non essere confuso da "GIGANTI".
- Verificare un prodotto in `KG` e uno in `G` dello stesso articolo: i due `valore_normalizzato` devono essere direttamente confrontabili (stessa scala, grammi).
- Verificare il caso di nessun match (prodotto senza quantità nel nome): deve restituire `None`, non sollevare eccezioni, e non deve bloccare il resto del caricamento del prodotto.

**Risks:** un regex non copre tutti i formati liberi presenti in un catalogo reale (es. "conf. da 6", "pz.", formati non numerici) — per quelli `valore_normalizzato` resta `None` e il codice a valle deve trattarlo come "formato non determinabile", non come formato piccolo per default.

---

## Task 1 — Nuovo `carica_ricettario.py`

**Goal:** caricare `Ricettario_SO_FOOD.xlsx` (fogli `Ricette` + `Ingredienti`) in una collezione Chroma dedicata (`ricette_sofood`), separata dal catalogo prodotti — un embedding per ricetta, non per file.

**Modifiche proposte:**

```python
import os
import time
import chromadb
import pandas as pd
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODELLO_EMBEDDING = "models/gemini-embedding-2"
PERCORSO_DB = "./database_vettoriale"
NOME_COLLEZIONE_RICETTE = "ricette_sofood"
FILE_RICETTARIO = "./Ricettario_SO_FOOD.xlsx"


class EmbedderGemini:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    def embed_documento(self, testo: str) -> list:
        response = self.client.models.embed_content(
            model=MODELLO_EMBEDDING,
            contents=[testo],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        vettore = response.embeddings[0]
        return vettore.values if hasattr(vettore, "values") else list(vettore)


def carica_ricettario():
    if not os.path.exists(FILE_RICETTARIO):
        print(f"[ERRORE] File '{FILE_RICETTARIO}' non trovato.")
        return

    ricette_df = pd.read_excel(FILE_RICETTARIO, sheet_name="Ricette")
    ingredienti_df = pd.read_excel(FILE_RICETTARIO, sheet_name="Ingredienti")

    client_db = chromadb.PersistentClient(path=PERCORSO_DB)
    collezione = client_db.get_or_create_collection(name=NOME_COLLEZIONE_RICETTE)
    embedder = EmbedderGemini(api_key=GEMINI_API_KEY)

    for _, riga in ricette_df.iterrows():
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

        print(f"Indicizzazione ricetta: {id_ricetta}...")
        try:
            embedding = embedder.embed_documento(testo_embedding)
            collezione.upsert(
                documents=[testo_embedding], metadatas=[meta],
                ids=[id_ricetta], embeddings=[embedding],
            )
            time.sleep(1)
        except Exception as e:
            print(f"Errore con {id_ricetta}: {e}")

    print(f"\n✅ Ricettario caricato: {len(ricette_df)} ricette in '{NOME_COLLEZIONE_RICETTE}'.")


if __name__ == "__main__":
    carica_ricettario()
```

**Testing Strategy:**
- Eseguire `python carica_ricettario.py` a terminale; verificare che il log riporti 230/230 indicizzazioni senza eccezioni.
- Verifica automatica: `collezione.count()` sulla collezione `ricette_sofood` deve restituire 230.
- Verifica a campione: `collezione.get(ids=["PIZZA_01"])` (o un altro ID reale del foglio Ricette) deve restituire metadati non vuoti con `ingredienti_json` deserializzabile.

**Risks:** rate limiting sull'API di embedding su 230 chiamate consecutive — il `time.sleep(1)` è già previsto: se falliscono singole ricette per timeout, lo script logga l'errore per quell'ID e continua (non interrompe il batch); rieseguire lo script è idempotente grazie a `upsert`.

---

## Task 2 — Estensione di `retrieval_utils.py`

**Goal:** aggiungere le funzioni di ricerca template, slot-filling, fallback e verifica di proponibilità, riusando `cerca_prodotti()` già esistente senza modificarlo.

**Modifiche proposte** (in coda al file, nessuna riga esistente va toccata):

```python
import json

NOME_COLLEZIONE_RICETTE = "ricette_sofood"
SOGLIA_DISTANZA_SOSTITUTO = 0.35  # oltre questa distanza, meglio omettere che forzare un match debole


def trova_template_ricetta(collezione_ricette, embedder, richiesta_cliente: str,
                            tipo_locale: str | None = None, n_candidati: int = 3):
    """
    Cerca il/i template di ricetta più vicini alla richiesta del cliente.
    Il filtro di canale si applica QUI (selezione del template), non sui
    singoli prodotti del RAG generico altrove nel codice — vedi Guardrail.
    """
    embedding = embedder.embed_documento(richiesta_cliente)
    risultati = collezione_ricette.query(query_embeddings=[embedding], n_results=n_candidati)

    candidati = []
    for i, id_ricetta in enumerate(risultati["ids"][0]):
        meta = risultati["metadatas"][0][i]
        if tipo_locale and tipo_locale in str(meta.get("canali_sconsigliati", "")):
            continue
        candidati.append({
            "id_ricetta": id_ricetta,
            "nome_piatto": meta["nome_piatto"],
            "categoria": meta["categoria"],
            "note_composizione": meta.get("note_composizione", ""),
            "ingredienti": json.loads(meta["ingredienti_json"]),
            "distanza": risultati["distances"][0][i],
        })
    return candidati


def riempi_slot_ricetta(template: dict, collezione_prodotti, indice_codici, embedder,
                         indice_fornitori=None):
    """Step 2: per ogni ingrediente generico del template, cerca il prodotto
    reale a catalogo con la ricerca ibrida esistente (cerca_prodotti)."""
    slot_riempiti = []
    for ingr in template["ingredienti"]:
        risultati = cerca_prodotti(
            collezione_prodotti, indice_codici, embedder,
            ingr["INGREDIENTE_GENERICO"], n_risultati=5,
            indice_fornitori=indice_fornitori,
        )
        categoria_attesa = str(ingr.get("CATEGORIA_ATTESA", "")).lower()
        candidati = [
            r for r in risultati
            if categoria_attesa in str(r["metadata"].get("categoria_prodotto", "")).lower()
        ] or risultati

        slot_riempiti.append({
            "ingrediente_richiesto": ingr["INGREDIENTE_GENERICO"],
            "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""),
            "ruolo": ingr.get("RUOLO", "opzionale"),
            "note_ingrediente": ingr.get("NOTE_INGREDIENTE", ""),
            "prodotto_trovato": candidati[0] if candidati else None,
        })
    return slot_riempiti


def gestisci_slot_mancante(slot: dict, collezione_prodotti, embedder,
                            soglia_distanza: float = SOGLIA_DISTANZA_SOSTITUTO):
    """Step 3: deterministico nel codice, non lasciato al giudizio del modello."""
    if slot["prodotto_trovato"] is not None:
        slot["esito"] = "TROVATO"
        return slot

    if slot["ruolo"] == "protagonista":
        slot["esito"] = "SCARTA_RICETTA"
        return slot

    embedding = embedder.embed_documento(slot["ingrediente_richiesto"])
    candidati = collezione_prodotti.query(
        query_embeddings=[embedding], n_results=3,
        where={"categoria_prodotto": slot["categoria_attesa"]} if slot["categoria_attesa"] else None,
    )
    if candidati["distances"] and candidati["distances"][0] and candidati["distances"][0][0] <= soglia_distanza:
        slot["esito"] = "SOSTITUITO"
        slot["sostituto_id"] = candidati["ids"][0][0]
        slot["sostituto_nome"] = candidati["metadatas"][0][0].get("nome_prodotto", "")
    else:
        slot["esito"] = "OMESSO"
    return slot


def proponibile(slot_riempiti: list) -> bool:
    """Regola documentata anche nella Legenda del ricettario: proponibile se
    TUTTI i 'protagonista' sono risolti E almeno metà dei 'secondario' lo è.
    Gli 'opzionale' non condizionano l'esito."""
    protagonisti = [s for s in slot_riempiti if s["ruolo"] == "protagonista"]
    if any(s["esito"] == "SCARTA_RICETTA" for s in protagonisti):
        return False

    secondari = [s for s in slot_riempiti if s["ruolo"] == "secondario"]
    if secondari:
        trovati = sum(1 for s in secondari if s["esito"] in ("TROVATO", "SOSTITUITO"))
        if trovati < len(secondari) / 2:
            return False
    return True


def componi_proposta_da_ricettario(richiesta_cliente: str, tipo_locale, collezione_ricette,
                                    collezione_prodotti, indice_codici, embedder,
                                    indice_fornitori=None):
    """Orchestratore end-to-end. Ritorna None se nessun template è
    utilizzabile: il chiamante ricade sul RAG prodotti generico esistente."""
    for template in trova_template_ricetta(collezione_ricette, embedder, richiesta_cliente,
                                            tipo_locale=tipo_locale):
        slot_riempiti = riempi_slot_ricetta(template, collezione_prodotti, indice_codici,
                                             embedder, indice_fornitori)
        slot_riempiti = [gestisci_slot_mancante(s, collezione_prodotti, embedder)
                          for s in slot_riempiti]
        if proponibile(slot_riempiti):
            return {"template": template, "slot": slot_riempiti}
    return None
```

**Testing Strategy:**
- Unit test su `proponibile()` con 3 casi sintetici: (a) protagonista mancante → `False`; (b) tutti protagonisti trovati, tutti secondari mancanti → `False` se ≥1 secondario esiste; (c) tutti protagonisti trovati, nessun secondario nel template → `True`.
- Test di integrazione: chiamare `componi_proposta_da_ricettario("un tagliere", tipo_locale=None, ...)` sul database reale e verificare che ritorni un dict con `template["categoria"] == "tagliere"`.

**Risks:** `collezione_ricette.query` con `where` su liste non è supportato nativamente da Chroma per substring match — il filtro su `canali_sconsigliati` è quindi post-query in Python (già così nel codice sopra), non spostarlo in un `where` di Chroma.

---

## Task 3 — Decomposizione strutturata in `app.py`

**Goal:** sostituire lo split su virgola della query riscritta con output JSON strutturato via `response_schema`, eliminando la fragilità su sotto-richieste che contengono virgole.

**Modifiche proposte:**

```python
from pydantic import BaseModel
from google.genai import types

class SottoRicerca(BaseModel):
    categoria: str
    query: str

class DecomposizioneQuery(BaseModel):
    sotto_ricerche: list[SottoRicerca]


def decomponi_query(client_genai, testo_per_ricerca: str) -> list[SottoRicerca]:
    prompt_decomposizione = f"""
Analizza questa richiesta di un cliente B2B alimentare e scomponila in sotto-ricerche,
una per ogni categoria di prodotto distinta menzionata. Se la richiesta riguarda
una sola categoria, restituisci una sola sotto-ricerca.

Richiesta: "{testo_per_ricerca}"

Rispondi solo con il JSON secondo lo schema fornito.
"""
    risposta = client_genai.models.generate_content(
        model=MODELLO_GEMINI,
        contents=prompt_decomposizione,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=DecomposizioneQuery,
        ),
    )
    return DecomposizioneQuery.model_validate_json(risposta.text).sotto_ricerche
```

**Nel ciclo di ricerca esistente**, sostituire `testi_da_cercare = testo_per_ricerca.split(",")` con:

```python
sotto_ricerche = decomponi_query(client_genai, testo_per_ricerca)
K_MINIMO_PER_CATEGORIA = 5
TETTO_CONTESTO = 30

risultati_totali = []
for sr in sotto_ricerche:
    risultati_totali.extend(
        ricerca_vettoriale(collezione, embedder, sr.query, n_risultati=K_MINIMO_PER_CATEGORIA)
    )
risultati_totali = dedup_risultati(risultati_totali)  # funzione già esistente
if len(risultati_totali) > TETTO_CONTESTO:
    risultati_totali = risultati_totali[:TETTO_CONTESTO]
```

**Testing Strategy:**
- Caso di regressione mirato: una query tipo `"prosciutto, ben stagionato e formaggi"` (virgola dentro una sotto-richiesta) deve produrre **2** sotto-ricerche coerenti, non 3 frammenti rotti — questo è esattamente il bug che la vecchia logica a `.split(",")` introduceva.
- Verificare che il numero di chiamate a `ricerca_vettoriale` per turno sia pari al numero di `sotto_ricerche` restituite da Gemini, non un numero fisso.

**Risks:** `response_schema` con Pydantic richiede una versione recente di `google-genai`; verificare la versione in `requirements.txt` prima di procedere, altrimenti il parsing JSON può fallire silenziosamente.

---

## Task 4 — Punto di innesto nel flusso principale (`app.py`)

⚠️ **Richiede review umana prima del merge** (vedi policy in cima al file).

**Goal:** far tentare la composizione da ricettario prima del RAG prodotti generico, solo quando la richiesta ha caratteristiche di composizione, senza toccare il comportamento per le query prodotto singolo.

**Modifiche proposte:**

```python
PAROLE_COMPOSIZIONE = ["tagliere", "menu", "menù", "abbinamento", "consigli", "consiglio", "idea per"]

usa_ricettario = any(p in messaggio_utente.lower() for p in PAROLE_COMPOSIZIONE)

contesto_ricetta = None
if usa_ricettario:
    contesto_ricetta = componi_proposta_da_ricettario(
        richiesta_cliente=testo_per_ricerca,
        tipo_locale=sessione.get("tipo_locale"),
        collezione_ricette=collezione_ricette,   # nuova collection, vedi Task 1
        collezione_prodotti=collezione,           # collection prodotti esistente, invariata
        indice_codici=indice_codici,
        embedder=embedder,
        indice_fornitori=indice_fornitori,
    )

if contesto_ricetta:
    blocco_contesto = costruisci_contesto_ricetta_testuale(contesto_ricetta)
else:
    blocco_contesto = costruisci_contesto_testuale(risultati_totali)  # flusso esistente, invariato
```

```python
def costruisci_contesto_ricetta_testuale(contesto_ricetta: dict) -> str:
    template = contesto_ricetta["template"]
    righe = [
        f"PROPOSTA COMPOSTA: {template['nome_piatto']} ({template['categoria']})",
        f"Note di composizione: {template['note_composizione']}" if template["note_composizione"] else "",
        "",
    ]
    for s in contesto_ricetta["slot"]:
        if s["esito"] == "TROVATO":
            righe.append(f"- {s['ingrediente_richiesto']}: {s['prodotto_trovato']['metadata']['nome_prodotto']} (MATCH REALE A CATALOGO)")
        elif s["esito"] == "SOSTITUITO":
            righe.append(f"- {s['ingrediente_richiesto']}: NON A CATALOGO, sostituito con {s['sostituto_nome']}")
        elif s["esito"] == "OMESSO":
            righe.append(f"- {s['ingrediente_richiesto']}: NON A CATALOGO, nessun sostituto valido — omettere dalla proposta")
        if s["note_ingrediente"]:
            righe.append(f"  (nota: {s['note_ingrediente']})")
    return "\n".join(r for r in righe if r)
```

**Testing Strategy:**
- Regressione: 5 query prodotto singolo già usate come test nel round precedente del progetto (senza parole di composizione) devono produrre esattamente lo stesso `blocco_contesto` di prima di questa modifica.
- Nuova funzionalità: `"vorrei un tagliere"` deve produrre un `blocco_contesto` che inizia con `PROPOSTA COMPOSTA:`.
- Caso di fallback: una richiesta di composizione per cui `componi_proposta_da_ricettario` ritorna `None` (nessun template proponibile) deve ricadere sul RAG generico senza eccezioni.

**Risks:** la lista `PAROLE_COMPOSIZIONE` è un rilevamento leggero, non un classificatore — falsi negativi (richieste di composizione non intercettate) sono accettabili e ricadono comunque sul RAG generico esistente; falsi positivi vanno monitorati nei log delle prime sessioni reali.

---

## Task 5 — Aggiunte a `system_prompt_v2.py`

⚠️ **Richiede review umana prima del merge.**

**Goal:** (a) rendere esplicita la regola "formato grande di default, salvo bottega"; (b) istruire il modello su come presentare un blocco `PROPOSTA COMPOSTA` in modo coerente con gli esiti di slot-filling/fallback del Task 2.

**Modifica 5a** — nella sezione esistente "FORMATI: RISTORAZIONE VS BOTTEGA", sostituire il contenuto con:

```
FORMATI: RISTORAZIONE VS BOTTEGA
So Food serve sia ristorazione/somministrazione sia botteghe/negozietti al dettaglio.
REGOLA DI DEFAULT: a parità di richiesta, proponi SEMPRE per primo il formato grande
(secchielli, conserve da 1,7-5kg, salumi e formaggi interi) — è il default per la
ristorazione, che è la maggioranza dei clienti. Passa ai formati piccoli/medi da
scaffale (vasetti da 200-500g) SOLO se il cliente si è qualificato esplicitamente
come bottega, negozio al dettaglio, o rivenditore. Se non sai ancora che tipo di
locale gestisce il cliente e la scelta del formato è rilevante per la risposta,
chiedilo prima di proporre — non dare per scontato il formato piccolo.
Non menzionare mai un peso/quantità precisi come se fossero una raccomandazione tua:
la scelta della quantità da ordinare spetta sempre al cliente.
```

**Modifica 5b** — nuova sezione da aggiungere:

```
GESTIONE DI UNA PROPOSTA COMPOSTA (da ricettario)
Se il contesto di questo turno contiene un blocco "PROPOSTA COMPOSTA", il piatto è
già stato composto automaticamente incrociando un template di composizione con il
catalogo reale. Regole:
- Presenta solo gli ingredienti marcati "MATCH REALE A CATALOGO" o "sostituito con
  X" come parte della proposta. Non inventare né aggiungere altro.
- Se un ingrediente è marcato "omettere dalla proposta", non nominarlo affatto:
  non spiegare al cliente che manca, semplicemente non è nella proposta finale.
- Se una nota indica che un ingrediente va "aggiunto a crudo dopo la cottura"
  (tipico di alcune pizze gourmet), descrivi la preparazione di conseguenza:
  non dire che è cotto insieme al resto se la nota specifica il contrario.
- Presenta la proposta con la stessa naturalezza di sempre (nomi in grassetto,
  fornitore citato con naturalezza nella frase), non come un elenco meccanico.
```

**Testing Strategy:** conversazione manuale end-to-end: chiedere a Nino "un tagliere per un pub" e verificare che (a) non proponga template con `pub` in `canali_sconsigliati`, (b) non menzioni ingredienti "omessi", (c) non citi mai un peso/grammo.

**Risks:** modifiche al system prompt hanno effetto immediato su tutte le conversazioni — testare in un ambiente non di produzione prima del deploy, coerente con la lezione già imparata sul progetto riguardo al "prompt overload" (troppe regole rigide fanno perdere aderenza anche alle altre).

---

## Task 6 — Verifica finale (contenuto atteso del Walkthrough)

Prima di chiudere il task, verificare e documentare nel walkthrough:

- [ ] `estrai_quantita_normalizzata` sul prodotto `OLIVE B.D.CERIGNOLA GIGANTI 314ML` restituisce `314.0, "volume"`, non un formato piccolo dedotto per errore da "GIGANTI".
- [ ] `carica_ricettario.py` gira senza errori, `ricette_sofood` contiene 230 documenti.
- [ ] Query di composizione (`"vorrei un tagliere"`) → template `categoria == "tagliere"`, tutti i `protagonista` risolti a un prodotto reale.
- [ ] Query con `tipo_locale = "pub"` → nessun template con `pub` in `canali_sconsigliati`.
- [ ] Ingrediente `protagonista` forzatamente assente dal catalogo (test con stringa inventata) → ricetta scartata correttamente, nessuna eccezione.
- [ ] Ingrediente `secondario` assente → sostituito se sotto soglia, altrimenti omesso senza bloccare la risposta.
- [ ] Il prompt non menziona mai un peso/quantità come raccomandazione propria (controllo manuale su 3 conversazioni di prova).
- [ ] A parità di richiesta, senza indicazioni sul cliente, Nino propone il formato grande per primo.
- [ ] 5 query prodotto singolo pre-esistenti (Task 4) restituiscono lo stesso `blocco_contesto` di prima della modifica — nessuna regressione sul flusso RAG generico.
