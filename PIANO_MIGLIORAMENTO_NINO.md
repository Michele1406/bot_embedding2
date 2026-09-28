# PIANO DI INTERVENTO — Nino / So Food (`bot_embedding2`)

> Analisi eseguita sul commit `2c22c00` (branch principale) clonando il repo e **eseguendo** codice e database in locale
> (ChromaDB reale, senza chiamate LLM). Numeri di riga riferiti a quel commit.
>
> **Legenda evidenza** — `[V]` = verificato eseguendo codice/DB (riproducibile, vedi Appendice A) · `[L]` = dedotto leggendo il codice.
>
> **Vincolo del proprietario: NON ridurre la qualità attuale delle risposte.** Tutto il piano è costruito attorno a questo vincolo (sezione 1).

---

## 0. Come usare questo file in Antigravity

Prompt iniziale suggerito per l'Agent Manager:

> Leggi `PIANO_MIGLIORAMENTO_NINO.md` per intero. Esegui le fasi **in ordine (0 → 8)**, un task alla volta, un commit per task.
> **Non iniziare la Fase 1+ prima di aver completato e committato la Fase 0 (golden set).** Dopo ogni task esegui `python scripts/golden_check.py` e
> riporta le differenze: una differenza non spiegata nel task = regressione = revert. Fermati per la mia review dopo ogni task marcato 🛑.
> Non cambiare comportamenti non elencati nel task. Non "migliorare" per iniziativa: se trovi altro, aggiungilo a `TODO_EXTRA.md`.

Politica di review: **mai "Always Proceed"** sui task 🛑 (toccano prompt di sistema, ranking, filtri dieta/canale, pipeline principale).

---

## 1. Regole di ingaggio — come NON abbassare la qualità

### 1.1 Il fatto più importante emerso dall'analisi

**La qualità che vedi oggi viene dal ramo "RAG generico + system prompt", non dal ricettario.**
Il ramo ricettario (`componi_proposta_da_ricettario`) è **silenziosamente inattivo**: restituisce sempre `""` e `app.py` ricade sul RAG generico. `[V]`
(dettaglio in RC-00). Conseguenza pratica: *"aggiustare" il ricettario cambierebbe il comportamento di produzione*, non lo ripristinerebbe.
Quindi la strategia è: **prima rimuovere/isolare ciò che è morto senza cambiare output, poi eventualmente riattivare il ricettario dietro flag, in A/B.**

### 1.2 Regole (obbligatorie per l'agente)

| # | Regola |
|---|---|
| R1 | **Golden set prima di tutto** (Fase 0): niente refactor senza baseline salvata. |
| R2 | Ogni task dichiara "Rischio qualità" e "Verifica". Se la verifica non passa → revert, non "aggiustare a mano". |
| R3 | Cambi che alterano ranking/filtri/prompt vanno dietro **feature flag** (variabile d'ambiente) con default = comportamento attuale, e si attivano solo dopo A/B approvato. |
| R4 | **Nessuna rimozione di regole di business "alla cieca"** (es. liste anti-wurstel, alias fornitori, regole del prompt): ognuna è nata da un errore reale osservato. Si migra a dati/metadati o si documenta; si elimina solo se il golden set dimostra equivalenza. |
| R5 | **Non riscrivere `system_prompt_v2.py` in blocco.** Prima modifiche chirurgiche (SP-01…SP-06), poi versione modulare **affiancata** (`system_prompt_v3.py`) selezionabile con `PROMPT_VERSION`, mai sostitutiva finché l'A/B non è vinto. |
| R6 | Non reintrodurre classificatori di intent a keyword o liste hardcoded di fornitori "prioritari" (già rimossi in passato: causavano "propone sempre gli stessi prodotti"). |
| R7 | Nessuna nuova dipendenza senza motivo; pin delle versioni in `requirements.txt` solo dopo aver verificato che tutto gira. |
| R8 | Un commit per task, messaggio `[ID-task] descrizione`. |

---

## 2. Sintesi esecutiva

| Priorità | Problema | ID | Evidenza |
|---|---|---|---|
| 🔴 P0 | `/immagine?path=` legge **qualsiasi file** del server (`.env` con la GEMINI_API_KEY, `app.py`, `/etc/passwd`). Con `avvia_tunnel.bat` il server è pubblico. | S-01 | `[V]` |
| 🔴 P0 | Registrazioni vocali dei clienti salvate in `static/` (pubbliche) **e committate su git** (4 file). | S-02 | `[V]` |
| 🔴 P0 | `!impara` scrive su file senza autenticazione; il file diventa parte del **system prompt** (prompt poisoning da chiunque abbia il link). Nessun rate limit → costi LLM esposti. | S-03 | `[L]` |
| 🟠 P1 | Ricettario **completamente inattivo** (schema dati incompatibile) + 2° bug che lo farebbe crashare + `gestisci_slot_mancante`/`proponibile` mai chiamate. Il prompt però descrive ancora il meccanismo. ≈54% di `retrieval_utils.py` e ~200 righe di `app.py` sono codice morto. | RC-00, D-* | `[V]` |
| 🟠 P1 | XSS lato frontend (testo utente e risposta LLM inseriti con `innerHTML`; `apriZoom('…')` rompibile da apice). | S-04 | `[L]` |
| 🟠 P1 | Fallback modello inefficace: `MODELLO_PRINCIPALE = MODELLO_FALLBACK = MODELLO_GEMINI` (stesso modello) → in caso di 429/503 riprova lo stesso modello. L'analisi iniziale non ha retry e cade subito nell'euristico (qualità inferiore). Storia git: 5+ commit su 503/429. | B-08 | `[L]` |
| 🟠 P1 | Fallback euristico: quando non trova numeri ritorna `quantita=2` (app.py:315) → forza "ESATTAMENTE 2 prodotti" anche per "che salumi avete?" durante un'indisponibilità dell'LLM. | B-12 | `[L]` |
| 🟠 P1 | `rileva_canale_locale("bottega barese")` → `misto` (dovrebbe essere `retail`): substring `"bar"` in "barese"/"Barletta". Cliente tipo in Puglia. | B-04 | `[V]` |
| 🟠 P1 | `rileva_cluster_regionale`: `"sardine"`→Sardegna, `"pizza capriziosa"`→Trentino (prefix match). Attiva pre-fetch fornitori regionali non pertinenti. | B-05 | `[V]` |
| 🟠 P1 | System prompt: contraddizioni con il codice (immagini), affermazioni non vere ("prodotti già filtrati per territorialità"), 12 nomi di brand hardcoded, ~8.600 token statici, 385 parole in maiuscolo, tag di contesto non spiegati. | SP-* | `[V]/[L]` |
| 🟡 P2 | 95 documenti utili (74 schede fornitore + 21 logistica/consegne) sono nel DB ma **esclusi da ogni ricerca**: il modello non li vede mai. | DT-03 | `[V]` |
| 🟡 P2 | Percorsi immagine: 1033/1033 sono `C:\Users\baron\…` → su qualunque altra macchina **nessuna foto** (silenzioso). | DT-04 | `[V]` |
| 🟡 P2 | Latenza/costi: ~60 schede × ~1.490 char ≈ 89k char (~25k token) per turno + 8,6k di prompt; nessuna cache embedding; ricerche sequenziali (fino a 13 embedding solo nel safety net); reflection loop che può raddoppiare le chiamate. | PF-* | `[V]/[L]` |

---

## FASE 0 — Rete di sicurezza della qualità (obbligatoria, nessun cambio di comportamento)

### T0.1 🛑 Golden set di query
**Cosa**: creare `tests/golden/queries.json` (~40 query) coprendo: panoramica ("che salumi avete?"), quantità ("3 salumi e 2 formaggi"), "di cui" ("2 prosciutti di cui 1 parma"),
tagliere terra / mare / spagnolo / toscano / pugliese, tagliere con quantità, pub+burger, pizzeria, bar, bottega, vegano, senza glutine, finger food/fritti, birre, marmellate/mostarde,
codice prodotto esatto, fornitore nominato (es. "Farino"), follow-up ("dimmene altri"), richiesta foto, richiesta formati, logistica ("consegnate a Lecce?"), prezzi, prompt injection, "ciao", vocale trascritto con refusi ("amburgher").
Ogni voce: `{id, sessione: [messaggi in ordine], attese: {...}}`.

### T0.2 Snapshot deterministico del retrieval
**Cosa**: `scripts/snapshot_retrieval.py` che, per ogni query, salva in `tests/golden/baseline/<id>.json`:
`analisi` (output di `analizza_richiesta_unificata`, temperature 0), **lista ordinata degli ID prodotto** finiti in `record_prodotti`, `stato` di profilo, hash del `contesto_testuale`.
`scripts/golden_check.py` rilancia e stampa il diff (ID aggiunti/rimossi/riordinati per query).
**Criterio**: un task passa se il diff è vuoto **oppure** ogni differenza è elencata e approvata nel task.
> Nota: l'analisi LLM a temperature 0 è quasi deterministica; se serve, salvare `analisi` in cache e rigiocarla per isolare il retrieval.

### T0.3 Invarianti automatici sulle risposte finali
`scripts/check_invarianti.py` esegue le query e verifica (regex, nessun LLM-judge necessario):
- nessuna parola `turno|prompt|contesto` (regola 6 del prompt);
- nessun `€`/"euro"/"sconto" (regola 4);
- nessun codice articolo se non richiesto (regola in "OBIETTIVO E STILE");
- nessun `###`/`##`;
- se `quantita=N` nell'analisi → il numero di prodotti in grassetto è N (± tolleranza dichiarata);
- nessun `[IMG:` se la query non chiede foto (comportamento attuale di app.py:1113-1145);
- risposta in italiano.
Salvare anche le risposte complete in `tests/golden/risposte/` per confronto manuale (temperature 0.3 → non deterministiche: confrontare per invarianti + lettura umana su 10 query chiave).

**Verifica Fase 0**: `python scripts/golden_check.py` sul codice non modificato → diff vuoto due volte di fila.

---

## FASE 1 — Sicurezza (P0)

### S-01 🔴 Lettura arbitraria di file su `/immagine` `[V]`
**Dove**: `app.py:1282-1287`.
**Problema**: `send_file(percorso)` su `request.args["path"]` senza alcuna validazione. Testato con `test_client`: `?path=.env` → 200 con il contenuto; `/etc/passwd` → 200; `app.py` → 200.
**Azione (minima, non cambia il funzionamento delle foto)**:
1. All'avvio costruire `IMMAGINI_AMMESSE = {normpath(m["percorso_immagine"]) for m in metadatas if m.get("ha_immagine_primaria")}`.
2. `/immagine` serve il file **solo se** `normpath(path)` è in quell'insieme; altrimenti 404. Estensioni ammesse `.jpg/.jpeg/.png`.
3. (Opzionale, S-01b) sostituire nel contesto/tag `[IMG: <path>]` il percorso con l'ID prodotto (`[IMG: 19010014_OBE4001]`) e risolvere l'ID lato server, così il path locale non arriva né all'LLM né al browser. Richiede di aggiornare la regex in `formattaImmaginiNino` (oggi richiede `\` o `/` nel valore).
**Rischio qualità**: nullo se le foto restano identiche. **Verifica**: test con 3 path validi → 200; `.env`, `../app.py`, `C:\Windows\win.ini` → 404.

### S-02 🔴 Audio degli utenti pubblici e su git `[V]`
**Dove**: `app.py:78-79` (cartella in `static/`), `app.py:1245-1256`; file tracciati: `static/audio_uploads/*.webm|*.wav`.
**Azione**: (a) salvare gli audio fuori da `static/` (es. `./var/audio/`), non servirli; (b) cancellarli dopo la trascrizione o con TTL (es. 24 h); (c) `git rm --cached -r static/audio_uploads` + `.gitignore`; (d) valutare pulizia della history (BFG) se le registrazioni sono dati sensibili; (e) `app.config["MAX_CONTENT_LENGTH"]` (es. 10 MB); (f) lato frontend l'`<audio>` usa `URL.createObjectURL` locale → non serve l'URL server (`audio_url` nella risposta si può togliere).
**Rischio qualità**: nullo (la trascrizione usa i byte in memoria).

### S-03 🔴 `!impara` e prompt poisoning; nessun rate limit `[L]`
**Dove**: `app.py:1194-1223`, `app.py:114-119` (`carica_memoria_dinamica`), `scripts/addestratore.py`.
**Problema**: chiunque abbia l'URL del tunnel può scrivere in `correzioni_chat.jsonl`; l'addestratore lo trasforma in `memoria_dinamica.txt`, **appeso al system prompt con "DA RISPETTARE TASSATIVAMENTE"** → può sovrascrivere le regole di sicurezza. Nessun limite di lunghezza/frequenza su `/chat`, `/chat_audio` (costo API).
**Azione**: `!impara` abilitato solo se `request.headers["X-Admin-Token"] == os.getenv("ADMIN_TOKEN")` (o disattivato di default con `ENABLE_IMPARA=0`); rate limit per IP/sessione (es. `flask-limiter`, 20 req/min); tetto lunghezza messaggio (es. 1.500 char); tetto dimensione `memoria_dinamica.txt` con revisione umana prima dell'attivazione.
**Rischio qualità**: nullo per gli utenti finali.

### S-04 🟠 XSS e ricostruzione del DOM `[L]`
**Dove**: JS in `app.py` — `aggiungiMessaggio` (~L2113-2180), `chatbox.innerHTML +=` (L2011, L2080, L2179), `onclick="apriZoom('${urlSicuro}')"` (~L1985).
**Problema**: testo utente e risposta LLM passano da regex markdown→HTML **senza escape**; un prompt injection che faccia emettere `<img onerror=…>` viene eseguito. `encodeURIComponent` non escapa `'` → `apriZoom('…')` rompibile. `innerHTML +=` ri-parsa tutta la chat a ogni messaggio (perde lo stato dei player audio, O(n²)).
**Azione**: escape HTML (`&<>"'`) del testo **prima** delle trasformazioni markdown (le trasformazioni esistenti restano identiche); usare `insertAdjacentHTML`/`appendChild`; `addEventListener` al posto di `onclick` inline.
**Rischio qualità**: nullo se il rendering markdown resta uguale → verificare a occhio 5 risposte (grassetti, elenchi, immagini).

### S-05 🟡 Server di sviluppo esposto
`app.run()` (Flask dev server) dietro tunnel pubblico. Usare `waitress` (Windows) con `threads=8`; aggiornare `avvia_bot.bat`. Aggiungere `.env.example`. La history git **non** contiene chiavi API `[V]` (buono).

---

## FASE 2 — Bug con correzione a basso rischio

> Ogni task: fare lo snapshot (T0.2) prima/dopo. Dove atteso un diff, è indicato.

| ID | Problema e dove | Azione | Diff atteso nel golden |
|---|---|---|---|
| **B-01** | `from app import ElementoRichiesto` in `elabora_messaggio_nino` (`app.py:615`, `:865`). Lanciando `python app.py` il modulo è `__main__`: l'import **rieseguirebbe l'intero file** (secondo client Chroma, ricostruzione indici, 2ª app Flask) alla prima query ambigua/"dimmene altri". `[L]` | Usare direttamente `ElementoRichiesto` (è nello stesso modulo). | Nessuno |
| **B-02** | Import duplicati (`app.py:10-13` vs `:34-37`) e inutilizzati (`:14 estrai_conteggi_tagliere`, `:30 classifica_terra_mare`, `:31 check_board_violations`); `MODELLO_GEMINI` (`:78`) mai usato. `[V pyflakes]` | Pulire. | Nessuno |
| **B-03** | Caratteri corrotti in `_QUALITA_ESCLUSIONI_TAGLIERE` (`retrieval_utils.py:1255-1256`): `"wǬrstel"`, `"patǸ"` → non matchano "würstel"/"paté". `[V]` | Correggere in `würstel`/`paté` (tenere anche le varianti ASCII). Il blocco oggi è nel ramo morto ma verrà riusato se si riattiva il ricettario. | Nessuno |
| **B-04** | `rileva_canale_locale` (`profilazione_locale.py:30-52`): match a sottostringa. `"bar"` ⊂ "barese", "Barletta"; `"market"`, `"cucina"`, `"chef"` idem. `"bottega barese"`, `"salumeria di barletta"` → `misto`. `[V]` | Match a parola intera (`\b…\b`). Aggiungere test: bottega barese→retail, salumeria di Barletta→retail, "bar"→horeca, "pub"→horeca. | **Atteso** per profili "barese/Barletta": boost formato retail. 🛑 Approvare. |
| **B-05** | `rileva_cluster_regionale` (`retrieval_utils.py:1230`): regex `\bparola` (prefisso) → `"sardine"`→sardegna, `"capriziosa"`→trentino (`"capriz"`), `"montagna"`→trentino. `[V]` Effetto: pre-fetch (max 4 prodotti/fornitore) di fornitori regionali fuori tema (`app.py:830-846`). | Match a parola intera con eventuali radici esplicite (`sard(o|a|e|i|egna)`, ecc.); rimuovere/riformulare `"capriz"` (verificare cosa intendeva: `caprino`?). Non toccare il caso `prosciutto di parma → emilia` senza approvazione. | **Atteso** su query con quelle parole. 🛑 |
| **B-06** | Ranking: `combinati.sort(key=punteggio_canale, reverse=True)` (`retrieval_utils.py:788`) riordina **tutto** (anche match esatti/fornitore) con bonus +2/−1 su un elenco che non ha punteggi di rilevanza → un prodotto meno pertinente ma in "kg" supera il migliore semantico. Inoltre tautologia `canale_locale == canale_locale` (`profilazione_locale.py:149`). Il formato è dedotto dal testo libero, mentre `formato_variante_liv5` (1226/1227 record) è inutilizzato (`formato_metadata_o_testo` mai chiamata). | (1) Applicare il boost **dentro ciascun gruppo** (esatti / fornitori / lessicali / vettoriali) o solo come tie-break; (2) usare `formato_metadata_o_testo`; (3) togliere la tautologia. **Dietro flag `CANALE_BOOST_V2`, default off.** | **Atteso** ampio → A/B su query pub/bottega/pizzeria. 🛑 |
| **B-07** | Reflection loop (`app.py:1158-1181`): controlla `elem.dominio.lower() in risposta_lower` (substring). Domini come "sottoli", "pane", "mare", "dispensa" spesso non compaiono letteralmente → falso "mancante" → **chiamata LLM extra + rigenerazione completa** (latenza ×2). La chiamata `generate_content` non ha try/except: eccezione → HTTP 500 → il frontend mostra "errore di connessione". | (1) try/except (in caso d'errore: `break` e restituire la risposta già generata); (2) considerare mancante un dominio solo se ha ≥1 prodotto in `record_prodotti` con quel `dominio_assegnato` **e** nessun nome di quei prodotti compare nella risposta (controllo su dati, non su parola); (3) log di quante volte scatta. | Possibile: meno rigenerazioni. Qualità invariata o migliore. |
| **B-08** 🛑 | Fallback modello: `MODELLO_PRINCIPALE`, `MODELLO_GEMINI`, `MODELLO_FALLBACK`, `MODELLO_AUDIO_FALLBACK` = stesso `gemini-3.6-flash` (`app.py:77-81`); commento a `:1061` cita altri modelli (stantio). Se il modello è in 503, il "fallback" riprova lo stesso. `analizza_richiesta_unificata` (`:279-292`) non ha retry: al primo errore usa l'euristico. | Env `MODELLO_FALLBACK` **diverso** dal principale (⚠ verificare che i nomi esistano nel proprio account: non verificabile da qui); retry con backoff (2 tentativi) anche nell'analisi **prima** di cadere nell'euristico; rimuovere costanti morte; aggiornare commenti. | Nessuno in condizioni normali; migliora sotto carico. |
| **B-09** | Filtro dieta "appiccicoso": `app.py:968-970` imposta `filtro_dieta="vegano"` se una qualsiasi frase contiene la sottostringa `vegano/vegana/vegani/vegane` (anche "non serve vegano", "senza vegani") e non viene mai azzerato; idem quanto estratto dall'LLM. | Word-boundary + negazione (riusare `_termine_presente_non_negato`); comando/intent per azzerare ("togli filtro vegano"). Default invariato: persiste. | Solo su frasi negate. |
| **B-10** | Doppio filtro vegano: `cerca_prodotti` (`retrieval_utils.py:701-742`, strutturato + negation-aware — buono) **e** hard-filter in `app.py:968-996` con **lista fornitori hardcoded** (`FORNITORI_NON_VEG`, L971) e blacklist di sottostringhe sul testo intero (`latte`, `miele`, `uov`, `burro`, `formaggi`…) che scarta anche "latte di cocco", "burro di arachidi", "senza uova". Metadato `vegano` ha valori `SI`(280) / `SI*`(414) / `NO`(533) / mancante(95): il significato di `SI*` non è documentato. | **Solo dopo golden set vegano**: sostituire il blocco di app.py con la logica strutturata di `cerca_prodotti` (già applicata perché `filtro_dieta` è passato) + esclusione per reparto; chiedere al proprietario il significato di `SI*`. | **Atteso** (più recall). 🛑 |
| **B-11** | Euristiche keyword troppo larghe: `ha_parole_nuova_richiesta` (`app.py:658-664`) contiene `" e "`, `"pasta"`, `"senza "`, `"secondo"`, `"vino"`… → quasi sempre vera. `[V]` ("e i formati?" → True). | Oggi irrilevante (ramo morto, vedi RC-00). Da sistemare **solo** se si riattiva il ricettario (Opzione B). | — |
| **B-12** | Fallback euristico: `estrai_q(...)` chiude con `return 2` (`app.py:315`) → `quantita=2` per ogni dominio senza numero → `blocco_conteggi` impone "ESATTAMENTE 2" (`:1042-1049`) su "che salumi avete?". `[L]` | `return None` quando non c'è un numero esplicito. | Solo in modalità fallback. |
| **B-13** | `record_prodotti` non inizializzato prima dei rami; usato con `'record_prodotti' in locals()` (`app.py:1118`). | Inizializzare `record_prodotti = []` a inizio funzione. | Nessuno |
| **B-14** | Messaggi d'errore con eccezione grezza all'utente (`app.py:999`, `:1096`). | Messaggio cortese + log server. | Nessuno |
| **B-15** | `sessioni` dict globale senza lock né TTL (cresce all'infinito; persa al riavvio); `FLASK_SECRET_KEY` mancante → `os.urandom` a ogni avvio → tutte le sessioni invalidate. | TTL/pulizia (es. 24 h), `threading.Lock`, `FLASK_SECRET_KEY` obbligatoria in `.env`. | Nessuno |
| **B-16** | `componi_proposta_da_ricettario` riassegna `prodotti_esclusi` (`retrieval_utils.py:449`) ignorando il parametro `prodotti_esclusi` passato da app.py; parametri `filtro_dieta`, `query_completa`, `piatto_precedente_nome` inutilizzati; annotazione `-> str` ma ritorna `dict`. | Confluisce in RC-B (se si riattiva) o D-02 (se si rimuove). | — |
| **B-17** | Tre livelli di cap per fornitore sovrapposti: `cerca_prodotti` (2, o fino a 12 per nicchie; `:66`, `:764-777`), `vettoriali_fair` cap 5 (`:681` — di fatto superato dal cap 2), `max_brand` in app.py (`:945-957`, 4…10 con eccezioni per `messina`/`mongetto`/`di tria`). | Documentare l'intento e consolidare in un solo punto **dopo** il golden set; le tre eccezioni per nome fornitore vanno spostate in `fornitori_config` come attributo (es. `"cap_extra": True`) invece che hardcoded nel flusso (R6). | **Atteso** minimo. 🛑 |

---

## FASE 3 — Ricettario: decisione e piano

### RC-00 Stato reale `[V]`
1. `componi_proposta_da_ricettario` (`retrieval_utils.py:431-582`) legge `json.loads(meta["canali_sconsigliati"])`: nel DB **408/408** record hanno testo libero (`"bar, pub"`, `"nan"`, `"ristorante"`) → `JSONDecodeError` al primo record → catturato → `risultati_ricettario=[]` → **ritorna `""`**. Eseguito contro il DB reale: `RISULTATO = ''`.
2. Legge anche `meta["slot"]` (chiave inesistente: 0/408) e non produce `ingredienti` → anche se il punto 1 passasse, `riempi_slot_ricetta` fallirebbe (`template["ingredienti"]`).
3. Se il template arrivasse ai tagliere: `get_comp_piano` (`:1438-1440`) ritorna `[{}]` (lista, per la precedenza di `if/else`) → `comp_pane.get(...)` → `AttributeError` `[V]`; con `piano_ricerca=None` (app.py:690, sempre `None`) i cicli su salumi/formaggi non eseguono **nessuna** iterazione (i tagliere non conterrebbero salumi né formaggi).
4. `gestisci_slot_mancante` e `proponibile` **non sono mai chiamate** → gli slot non hanno la chiave `esito` → `costruisci_contesto_ricetta_testuale` (`app.py:365`) e il tracciamento (`:774-777`) non produrrebbero righe prodotto.
5. `trova_template_ricetta` (`:1002`, l'unica coerente con lo schema reale: `ingredienti_json`, `canali_sconsigliati` testo) **non è chiamata da nessuno**.
6. Tutta la "macchina a stati" (`ultimo_piatto_proposto`, `ricette_mostrate`, `FAMIGLIE_PORTATE`, `categoria_ereditata`, `chiede_dettagli_formati_correnti`, `app.py:654-817`) dipende da `contesto_ricetta` truthy → **ramo morto**: `ha_piatto_attivo` è sempre `False`.
7. Il system prompt (L118-124, L87, L50) descrive ancora "PROPOSTA COMPOSTA" e "il sistema compone già i taglieri regionali".

### RC-A ✅ Raccomandato ORA — isolare senza cambiare output (🛑)
- Introdurre `USE_RICETTARIO` (env, **default `0`**) e far sì che `usa_ricettario` sia `False` se spento. Non cancellare ancora nulla.
- Log `[RICETTARIO] disattivato` una volta all'avvio.
- Dopo 2 settimane di produzione senza differenze → eseguire le rimozioni D-01…D-06.
- **Verifica**: golden set diff **vuoto** (il comportamento attuale è già "ricettario spento").

### RC-B (facoltativo, dopo RC-A e Fase 0) — riattivare il ricettario dietro flag
Solo se il proprietario vuole i template ricetta. Piano minimo, tutto dietro `USE_RICETTARIO=1`, **A/B contro il RAG generico** su query di composizione:
1. Selezione template: usare `trova_template_ricetta` (schema corretto) al posto del blocco duplicato in `componi…`; parse `canali_sconsigliati` come lista CSV ignorando `nan`/`nessuno`; confronto con `canale_locale` normalizzato (non con `tipo_locale` libero, es. "ristorante di mare").
2. Dopo `riempi_slot_ricetta`: chiamare `gestisci_slot_mancante` per ogni slot e `proponibile()`; se `False` → template successivo (come da `implementazione_ricettario_antigravity.md`, Task 2).
3. Correggere `get_comp_piano` (`return comps[0] if comps else {}`) e, con `piano_ricerca=None`, sintetizzare un piano di default da `target_salumi/target_formaggi` (altrimenti i tagliere restano senza salumi/formaggi).
4. Fix B-11 (keyword troppo larghe) e B-16.
5. Potatura dati (DT-02): 112 ricette legacy senza `foglio_origine` (296 dal loader attuale + 112 = 408).
6. Guardrail invariati (dal documento originale): canale solo nella selezione template; nessuna quantità in UI/prompt; sostituzione deterministica; **nessuna lista hardcoded di fornitori**.
7. Rimuovere dal codice vivo i nomi fornitore hardcoded nel ramo standard (`Biobontà`, `La Nicchia`, `Adò`, `Medimer`, `Farino` — `retrieval_utils.py:1676-1680`, `:1778-1786`, `:1827`) e tradurli in metadati/`fornitori_config`.
**Criterio di accettazione**: su 15 query di composizione, valutazione cieca (proprietario) ≥ RAG generico su ≥ 80% dei casi, e invarianti T0.3 tutti verdi. Altrimenti resta spento.

---

## FASE 4 — Codice morto / ridondante

> Eseguire **dopo** RC-A e con golden diff vuoto ad ogni step. Stime di righe: approssimative.

| ID | Cosa | Dove | Nota |
|---|---|---|---|
| **D-01** | `main_chatbot_v2.py` (306 righe): importa `core.query_decomposer` che **non esiste** → `ImportError` all'avvio `[V]`. Duplica la pipeline. Citato da docstring di `retrieval_utils.py`. | file intero | Eliminare (o `archive/`); aggiornare docstring. |
| **D-02** | Ramo ricettario in `app.py`: `costruisci_contesto_ricetta_testuale` (`:365-400`), blocco intent/composizione/stato (`:654-787`), ramo `elif chiede_dettagli_formati_correnti` (`:790-817`), `piano_ricerca = None` (`:690`), `FAMIGLIE_PORTATE`. | app.py | ≈200 righe. Rimuovere dopo RC-A. |
| **D-03** | `retrieval_utils.py`: `formatta_proposta_ricetta` (425), `componi_proposta_da_ricettario` (431-582), `trova_template_ricetta` (1002-1148), `PAROLE_NUMERI/_parse_numero_italiano/estrai_conteggi_tagliere` (1151-1220, mai usata neppure da app.py), `PAROLE_PLURALI_SLOT/N_ESPANSIONE_PLURALE` (1223-1224), `_QUALITA_ESCLUSIONI_TAGLIERE/_fornitori_ammessi_per_slot/_seleziona_componente_tagliere` (1249-1395), `riempi_slot_ricetta` (1398-1838, 440 righe), `gestisci_slot_mancante` (1840), `proponibile` (1897), `NOME_COLLEZIONE_RICETTE`, `SOGLIA_*`. | retrieval_utils | ≈1.040 righe = **~54% del file**. Se si sceglie RC-B, **spostare** in `core/ricettario.py` invece di cancellare. |
| **D-04** | `core/domain_rules.py` (154 righe) + `scripts/test_domain_rules.py`: usati solo dal ramo morto (`check_board_violations`, `INCOMPATIBILITY_MATRIX`, `prodotto_appartiene_a_famiglia`). | core | Spostare con D-03 (o archiviare). Le regole sono valide: non perderle. |
| **D-05** | Campi di sessione/analisi raccolti e **mai usati**: `senza_affettatrice` (`:50`, `:207`, `:631`) e `citta` (`:634`) — non influenzano né retrieval né prompt (`blocco_profilo`, `:1030-1037`, non li include). | app.py | **Non rimuovere ancora**: vedi SP-13 (renderli utili). |
| **D-06** | Simboli inutilizzati (vulture/pyflakes `[V]`): `regione_del_fornitore`, `formato_metadata_o_testo`/`rileva_canale_da_formato_liv5` (vedi B-06, da *usare* non cancellare), `classifica_terra_mare`, `MAPPA_SOTTOCATEGORIA`, `MAPPA_CATEGORIA_REPARTO`, `REPARTI`, `REPARTI_NEUTRI`, `MODELLO_GEMINI`, `FREQUENZA_SALVATAGGIO_LOG`. Import inutili in `retrieval_utils.py:45,50`. | vari | Pulizia. Nota: l'`import` a riga 1 di `retrieval_utils.py` precede il docstring, che quindi non è più un docstring di modulo. |
| **D-07** | Registri di fornitori **duplicati** (contraddicono il principio dichiarato in `fornitori_config.py:1-40` e `retrieval_utils.py:18-36`): `FORNITORI` (fornitori_config) · `_ALIAS_FORNITORI` (`retrieval_utils.py:227-303`, con mappature *parola prodotto → fornitore*: colatura→delfino/gentile, uova→scudellaro, burro→montanari/casera, limoncello→convento/smeralda, cantucci→lunardi, spalmabile→crucolo, carnaroli→acquerello — proprio ciò che il commento a `:222-226` dice di aver escluso) · `FORNITORI_NON_VEG` (`app.py:971`) · eccezioni `messina/mongetto/di tria` (`app.py:951-953`) · nomi nel prompt (SP-04). | vari | **Non cancellare** (R4): unificare in `fornitori_config` (attributi: alias, `non_vegano`, `cap_extra`, alias di prodotto) e generare da lì. Verificare col golden che i prodotti "colatura/uova/burro/…" restino trovati. 🛑 |
| **D-08** | Script una-tantum e file generati: `scripts/aggiorna_san_salvatore.py`, `aggiorna_immagine.py`, `filexlsx.py`, `esporta_tassonomia_excel.py`, `export_metadati_chromadb.xlsx` (600 KB), `report.md`, `implementazione_ricettario_antigravity.md` (cita "230 ricette", DB ne ha 408; `docs/struttura_rag_ricettario.md` citato in `retrieval_utils.py:446` **non esiste**). | root/scripts | Spostare in `scripts/archive/` e `docs/`; aggiornare numeri. ⚠ Gli script non sono stati letti riga per riga (solo intestazioni). |

---

## FASE 5 — System prompt (`core/system_prompt_v2.py`)

**Metriche `[V]`**: 30.973 caratteri, 4.759 parole, 149 righe, ≈8.600 token **a ogni turno**; 32 blocchi "REGOLA"; 385 parole tutte-maiuscole; `MAI` ×44, `VIETATO` ×16, `TASSATIVAMENTE` ×12, `SEMPRE` ×23, `SEVERAMENTE` ×6, `ESCLUSIVAMENTE` ×9.
Effetto: quando tutto è "assoluto", niente lo è; il modello riceve ~60 vincoli assoluti in competizione (tra loro e col codice).

### Modifiche chirurgiche (5a — fare per prime, una alla volta, A/B su golden 🛑)

**SP-01 — Contraddizione sulle immagini** (prompt L12-13 vs `app.py:1113-1145`)
Il prompt impone "OBBLIGO ASSOLUTO" di inserire `[IMG: path]` per ogni prodotto con percorso; il codice **elimina tutti i tag** se l'utente non ha chiesto una foto e i risultati sono >1 (`else: re.sub(r'\s*\[IMG:…')`). Inoltre ogni scheda nel contesto porta una riga "Percorso File Immagine: C:\Users\…" (fino a 65 righe/turno, con path locali).
*Azione*: iniettare la regola immagini **solo quando `richiede_foto` è vero** (blocco condizionale nel prompt finale); altrimenti nel prompt: "non inserire tag immagine". In `costruisci_contesto_testuale` emettere la riga "Percorso File Immagine" solo se `richiede_foto`. Lasciare invariato il garante di app.py.
*Rischio*: query "fammi vedere la foto…" → verificare col golden (L1 + risposte). 

**SP-02 — Sezione "GESTIONE DI UNA PROPOSTA COMPOSTA"** (L118-124) descrive un blocco che oggi non viene mai generato (RC-00).
*Azione*: rimuoverla dal prompt base e iniettarla solo se `contesto_testuale` contiene `PROPOSTA COMPOSTA` (o `USE_RICETTARIO=1`). Il "Non indicare mai grammature" è già coperto da L129.

**SP-03 — Affermazioni sul sistema non garantite dal codice**
- L87: "i prodotti per una richiesta regionale sono **già filtrati per territorialità**" → falso: il codice fa solo un *pre-fetch* di fornitori del cluster (`app.py:830-846`) e li **aggiunge** ai risultati; gli altri restano. Rischio: il modello mescola regioni fidandosi di un filtro che non c'è.
- L50: "il sistema esclude in automatico mozzarelle e creme" → vero solo per alcune sottocategorie nelle query con "tagliere" (`app.py:906-915`); "mozzarella" (sottocategoria `MOZZARELLE`) non è esclusa. `[V]`
- L18: "fino a 45 risultati" → oggi `N_RISULTATI_RAG = 65` (`app.py:73`).
*Azione*: riformulare in modo neutro ("nel contesto possono comparire anche prodotti non adatti: scegli tu con buon senso") oppure rendere vere le affermazioni nel codice (preferibile solo per il filtro regionale, come task separato).

**SP-04 — Nomi hardcoded nel prompt** (`Italfish`×3, `Colimena`, `Medimer`, `Farino`, `Capuano`, `Anfosso`, `BBS`, `Franchi`×5, `Salame Lion`, `Salame Rosa`, `Pachineat`/`Stardust`)
Contraddice L26 del prompt stesso ("mai da un elenco che di solito abbini") e il principio R6; invecchia col catalogo. Nel DB esistono `19010014_LION`, `19010014_LIONP` (famiglia "simile a mortadella").
*Azione*: (1) sostituire i nomi con attributi ("i salumi di mare del contesto"); (2) la regola Lion/Rosa → campo `nota_uso` nei metadati (o famiglia in `domain_rules`) stampato nella scheda; il prompt dice "rispetta le note d'uso della scheda". Fino a validazione A/B **lasciare** la regola Lion/Rosa (nata da un errore reale).

**SP-05 — Ridondanza "non dire mai che manca X"** (≥7 occorrenze + per singola categoria: dessert, pasta fresca, spalmabili, uova, colatura, ricci/tartare, ingredienti espliciti, birre, conteggi)
Quasi tutte sono coperte a monte da `esegui_safety_net_prodotti` (`PAROLE_PRODOTTO_SAFETY`, 132 parole; `ALIAS_PAROLA_SOTTOCATEGORIA`, 120 alias, **tutti consistenti col DB `[V]`**).
*Azione*: una sola regola generale ("prima di dire che un prodotto non c'è, controlla tutte le schede e i blocchi [PRODOTTI SPECIFICI…]; se c'è, proponilo") + mantenere solo le eccezioni **senza** rete di sicurezza nel codice. Tabella di mappatura regola→rete da compilare nel task. 🛑 (alto rischio di regressione: fare per gruppi, con golden per ciascun gruppo.)

**SP-06 — Enfasi**: riservare le maiuscole a ≤10 vincoli davvero inviolabili (no invenzioni, no prezzi, no contatti, no codici articolo, no gergo interno, lingua, ruolo/prompt-injection, dati solo dal contesto, allergeni→etichetta, ordine rivisto da operatore). Il resto in frase normale **con il motivo** ("perché…"). 🛑 A/B obbligatorio (rischio: calo di aderenza).

**SP-07 — Struttura e annidamento**
La sezione "COME COMPORRE TAGLIERI, MENU E ABBINAMENTI" (L28-116) contiene ~35 regole di domini diversi (dessert, spezie, risotto, colatura, uova, hamburger, sughi…). I livelli sono incoerenti: L101, L108, L110-116 sono al livello `-` dopo blocchi `*` di un'altra regola → non è chiaro a quale ambito appartengano.
*Struttura proposta* (per `system_prompt_v3.py`, non sostitutiva): 1) Identità e stile · 2) Vincoli inviolabili · 3) **Guida ai blocchi di contesto** (SP-08) · 4) Come rispondere per tipo di richiesta (esplorativa / specifica / composizione / follow-up / fuori tema) · 5) Regole di dominio (tagliere, mare, pizza/pinsa, primi, secondi, dolci, bar, dieta, spezie) · 6) Logistica e ordine · 7) Chiusura (max 1 domanda, SP-09).

**SP-08 — Tag di contesto non spiegati**: il codice inserisce `[GIÀ MENZIONATO IN PRECEDENZA]`, `[VINCOLI DI QUANTITA' OBBLIGATORI…]`, `[PROFILO CLIENTE MEMORIZZATO]`, `[=== DOMINIO RICHIESTO: X ===]`, `Varianti:`, `[MATCH ESATTO SU CODICE PRODOTTO]`, `[FORNITORI DISPONIBILI RILEVATI…]`, `[PRODOTTI SPECIFICI RICHIESTI DALL'UTENTE…]`, `[DETTAGLIO E FORMATI…]`. Il prompt ne cita solo 3 (`GIÀ MENZIONATO` mai, `[V]`).
*Azione*: aggiungere un paragrafo "Come leggere il contesto" (una riga per tag). Basso rischio, alto beneficio (soprattutto la regola 7 "varietà" che dipende da `GIÀ MENZIONATO`).

**SP-09 — Conflitti di chiusura**: il prompt impone domande finali diverse in punti diversi: "DEVI SEMPRE FARE QUESTA DOMANDA" (aziende, L136), chiedere il formato (L128), la terra per i locali di mare (L34), taralli/olive (L44), sott'oli (L70), mostarda (L71), feedback sul piatto (L147), P.IVA (L149). Una risposta può doverne fare 3.
*Azione*: "una sola domanda di chiusura, scelta con questa priorità: …".

**SP-10 — Storytelling "se lo sai"** (L54) invita a usare la memoria del modello, contro L18/L26. Le 74 schede fornitore che servirebbero **sono escluse dal RAG** (DT-03).
*Azione*: "usa solo la scheda produttore se presente nel contesto; altrimenti non raccontare" + DT-03.

**SP-11 — Precedenza sui conteggi**: L100 ("presenta esattamente la selezione completa del contesto") vs `blocco_conteggi` in app.py (`:1042-1049`, "ESATTAMENTE N… Eccezione: proponi i più simili o affini") vs eterogeneità (L48) vs onestà (L18, L115). L'eccezione di app.py spinge a **riempire la quota con prodotti fuori tema**.
*Azione*: precedenza esplicita: onestà > numero richiesto dall'utente > eterogeneità > default 2-3+2-3; testo del vincolo: "se i prodotti pertinenti sono meno di N, presenta quelli che ci sono e dillo".

**SP-12 — Prezzi/contatti/passaggio a collega**: vietati, ma manca la procedura ("passare la palla a un collega umano" senza dire come). `ha_parole_tecniche_formati` include "quanto costa/prezzo" (`app.py:668-672`).
*Azione*: frase-modello ("I prezzi li definisce il nostro commerciale in base al tuo profilo: se vuoi, preparo la bozza d'ordine e la fa rivedere a un collega") + cosa raccogliere.

**SP-13 — Dati di profilo raccolti ma non mostrati al modello**: `citta` e `senza_affettatrice` (D-05). Il prompt dice "chiedi la città solo se…" ma il modello **non vede la città già data** → può richiederla.
*Azione*: aggiungere `- Città: …` e `- Attrezzatura: senza affettatrice` a `blocco_profilo` (`app.py:1030-1037`) + una riga nel prompt (con "senza affettatrice" preferire prodotti già affettati/in busta). Miglioramento additivo, basso rischio.

**SP-14 — Formati vs pesi**: L129 ("non menzionare mai un peso") e app.py `:812-816` ("descrivi il formato/peso esatto") e L90 (rispondi col packaging). Chiarire: *riportare il formato dalla scheda è consentito quando richiesto; è vietato consigliare quanti kg ordinare*.

**SP-15 — `memoria_dinamica.txt`** appesa in coda con "DA RISPETTARE TASSATIVAMENTE" senza limite di dimensione né validazione: può contraddire il prompt base (S-03). Aggiungere tetto, revisione umana, e collocarla **prima** dei vincoli inviolabili dichiarando che questi prevalgono.

**SP-16 — Post-processing che compensa il prompt** (`app.py:1101-1112`): `sottofondo→sottovuoto`, `SOFOUND→SOFOOD`, rimozione "Marchio - Prodotto" nel grassetto, conversione `#` in grassetto. Sono utili come rete deterministica (tenere), ma non c'è evidenza nel repo di quanto scattino → loggare i casi (contatore) per decidere se le regole di formato nel prompt (L8, L11) si possono accorciare.

### Versione modulare (5b — opzionale, solo dopo 5a e A/B)
`system_prompt_v3.py` = **nucleo** (identità, vincoli, guida contesto, chiusura ≈2.5-3k token) + **blocchi di dominio** iniettati dal codice in base a ciò che l'analisi già estrae (`elementi_richiesti[].dominio`, `tipo_locale`, `filtro_dieta`, `richiede_composizione`): tagliere, mare, spagnolo/regionale, pizza-pinsa, primi/risotto, secondi, dolci, bar/tris, hamburger, spezie, dieta, logistica. Selezione tramite `PROMPT_VERSION=v3` (default `v2`). Riduce token e conflitti, ma **cambia molto il comportamento**: accettare solo se, sul golden set, invarianti verdi e valutazione cieca ≥ v2. **Non usare un intent classifier a keyword (R6): usare i campi già prodotti dall'analisi LLM.**

---

## FASE 6 — Prestazioni e costi (senza toccare i contenuti)

| ID | Azione | Rischio qualità |
|---|---|---|
| **PF-01** | Cache LRU su `embed_query` (stesso testo ripetuto: `elem.dominio` nell'Active RAG, parole del safety net, follow-up). | Nullo |
| **PF-02** | Parallelizzare con `ThreadPoolExecutor` le ricerche per elemento (`app.py:867-925`) e gli embedding del safety net (fino a **13 chiamate sequenziali** in una sola query ricca `[V]`); **preservare l'ordine dei risultati**. | Nullo se l'ordine è identico (golden diff vuoto) |
| **PF-03** | Reflection loop gated (B-07): meno chiamate LLM aggiuntive. | Nullo/positivo |
| **PF-04** | Context caching Gemini per il prefisso statico (system prompt ~8,6k token). | Nullo |
| **PF-05** | Dimensione contesto: 60 schede × media 1.487 char (max 8.461) ≈ 89k char ≈ ~25k token/turno `[V char / stima token]`. Opzioni: (a) tetto per scheda su campi non essenziali (es. troncare descrizioni oltre p90=1.971 char); (b) `N_RISULTATI_RAG` adattivo (65 solo se `max_req_q ≥ 8`, altrimenti ~30). ⚠ `65` è stato **alzato di proposito** (commit 743ba9e) per richieste numeriche grandi. | **Medio** → solo con golden L1+risposte; flag `CONTEXT_TRIM=0` di default 🛑 |
| **PF-06** | Avvio: tre `collezione.get()` completi (indici codici/fornitori/testuale) → una sola lettura. (Con B-01 non raddoppia più.) | Nullo |
| **PF-07** | Streaming (SSE) della risposta per la latenza percepita. | Nullo |
| **PF-08** | Logging strutturato dei tempi per fase (analisi, retrieval, risposta, reflection) per misurare *prima/dopo*. | Nullo |

---

## FASE 7 — Dati (`database_vettoriale`, sorgenti Excel)

| ID | Problema `[V]` | Azione |
|---|---|---|
| **DT-01** | 6 prodotti Menodiciotto con `sottocategoria="> 1000 GR"` (valore scivolato dalla colonna formato): `19010003_RP CIOCCOLATO`, `RP COCCO`, `RP CREMA`, `RP FIORDIPANNA`, `RP GIANDUIA`, `RP LIMONE`. Un record ha un metadato spurio `test_key="valore_test"` (`19010003_RP CAFFE`). | Correggere con `scripts/rigenera_tassonomia_db.py`/patch mirata (sottocategoria coerente con `categoria_tassonomia` = GELATI VASCHETTE); rimuovere `test_key`. Impatta il filtro `sottocategoria` esatto. |
| **DT-02** | `ricette_sofood` = 408 record: 296 dal loader attuale (fogli Excel) + **112 legacy** senza `foglio_origine` (prefissi `SO_` ×102, ecc.). `carica_ricettario.py` fa solo upsert, non elimina gli ID orfani. | Aggiungere al loader `--prune` (elimina ID non presenti nell'Excel). Rilevante solo con RC-B. |
| **DT-03** | 95 documenti non-prodotto in `catalogo_sofood` (74 `SCHEDA AZIENDALE FORNITORE`, 11 calendari ordini, 10 consegne zonali): **esclusi da ogni ricerca** (`CATEGORIE_ESCLUSE_DA_RAG`, `REPARTI_ESCLUSI_DA_RAG`) → il modello non può raccontare il produttore né rispondere su giorni di consegna/cut-off freschi (il prompt hardcoda solo "Puglia/Basilicata"). | Feature **additiva**: (a) alla prima menzione di un fornitore, allegare al contesto la sua scheda (1 riga); (b) se l'analisi rileva domanda logistica (`tipo_richiesta`), allegare i documenti `CONSEGNE_*`/`CALENDARIO_*` pertinenti. Spostarli in collezione separata `info_sofood`. 🛑 |
| **DT-04** | `percorso_immagine` = path assoluti `C:\Users\baron\…` (1033/1033); esistono **0** su questa macchina. Su un altro PC/server: nessuna foto, senza errori. | Variabile `CARTELLA_IMMAGINI` + path relativi (script di migrazione metadati); log all'avvio: "immagini trovate X/Y". |
| **DT-05** | 1.093/1.238 documenti iniziano con BOM `\ufeff` (finiscono nel testo "Scheda:" inviato all'LLM). | Rimuoverlo **in visualizzazione** (`costruisci_contesto_testuale`); **non** rigenerare gli embedding. |
| **DT-06** | `vegano`/`vegetariano`/`senza_glutine`/`senza_lattosio` hanno valori `SI*` e `?`; il codice tratta solo `startswith("NO")`. Un record ha `reparto="CARNI"` (gli altri `CARNE`; è un documento logistico, già escluso). | Definire e documentare col proprietario il significato di `SI*` e `?`; decidere il trattamento nei filtri dieta. |
| **DT-07** | Numeri nei documenti obsoleti: report.md (1.227+437), doc ricettario (230), "73 sottocategorie" vs **130** distinte nel DB. | Aggiornare i documenti (D-08). |

---

## FASE 8 — Architettura e igiene

| ID | Azione |
|---|---|
| **AR-01** | Estrarre l'HTML/JS (~900 righe) da `app.py` in `templates/index.html` + `static/app.js` (nessun cambio funzionale; abilita S-04 e test frontend). |
| **AR-02** | Spezzare `elabora_messaggio_nino` (~600 righe) in funzioni pure: `prepara_analisi`, `recupera_prodotti`, `applica_filtri_business`, `costruisci_prompt`, `genera_risposta`, `postprocessa`. **Solo con golden diff vuoto.** |
| **AR-03** | `config.py` unico (modelli, soglie, flag, path) letto da env; niente costanti duplicate. |
| **AR-04** | Filtri di business dell'app (anti-gelato nei finger food `app.py:898-907`, anti-wurstel/fusi/julienne/mare nei taglieri `:909-921`, pizzeria/bar/risotto in `cerca_prodotti`, hardcode `FARINO10` `:750`) → tabella dichiarativa `regole_business.py` (condizione → filtro), con test. Contraddicono la nota di design di `retrieval_utils.py:18-36` ("blocklist nel codice non distinguono il contesto"): non rimuoverli (R4), ma renderli espliciti e testati. |
| **AR-05** | `requirements.txt` senza versioni fisse e senza `pydantic` (importato) → pin dopo test (`chromadb` 1.5.9 usato nell'analisi). Aggiungere `waitress`, `flask-limiter`. |
| **AR-06** | Test: `tests/` con pytest per `rileva_canale_locale`, `rileva_cluster_regionale`, `pulisci_nome_commerciale`, `_match_sottocategoria`, `_termine_presente_non_negato`, fallback euristico (`estrai_q`), `/immagine`. Oggi esiste solo `scripts/test_domain_rules.py`. |
| **AR-07** | `README.md`: allineare (ricettario inattivo, numeri reali, flag, come lanciare golden set). |

---

## 3. Cosa è stato verificato e funziona (NON toccare)

- Indice HNSW allineato: 1.322/1.322 prodotti e 408/408 ricette raggiungibili con la propria query `[V]`.
- `ALIAS_PAROLA_SOTTOCATEGORIA`: 120/120 sottocategorie esistono nel DB `[V]` (il safety net funziona sui dati reali).
- Nessuna chiave API nella history git `[V]`; `.env` ignorato; `debug=False`.
- Lo storico conversazionale salva solo il testo utente/modello (non l'intero contesto RAG) → niente crescita del prompt tra turni `[L]`.
- `_termine_presente_non_negato` (gestione negazioni), `pulisci_nome_commerciale`, filtro dieta strutturato in `cerca_prodotti`: buona logica, da preservare.
- La rete di sicurezza `esegui_safety_net_prodotti` è il vero meccanismo anti "non ce l'abbiamo" → non indebolirla nel taglio del prompt (SP-05).

## 4. Limiti dell'analisi (onestà)

- **Nessuna chiamata LLM eseguita** (niente API key): la qualità delle risposte finali non è stata misurata; per questo la Fase 0 è obbligatoria.
- Il retrieval semantico è stato testato con vettori già presenti nel DB come "query finte": ho verificato struttura, crash e filtri, **non** la qualità del ranking semantico.
- Il frontend non è stato eseguito in un browser (S-04, B-15 sono da lettura del codice).
- Gli script in `scripts/` sono stati solo scorsi (intestazioni); `caricaprodotti_v2.py`, `arricchisci_catalogo.py` e simili non sono stati verificati riga per riga (né i file Excel sorgente, tranne struttura di `Ricettario_SO_FOOD.xlsx`).
- I nomi modello (`gemini-3.6-flash`, `gemini-2.5-flash`, `gemini-embedding-2`) non sono verificabili da qui: controllarli nel proprio account.
- Le stime di token sono `caratteri/3,6`; i conteggi di righe morte sono approssimati.

## 5. Definition of Done

- [ ] Fase 0 completata; `golden_check` verde sul codice iniziale.
- [ ] S-01…S-04 chiusi e testati (path fuori whitelist → 404; audio non pubblici; `!impara` protetto; escape HTML).
- [ ] `USE_RICETTARIO` presente, default `0`; golden diff vuoto.
- [ ] Nessun import rotto (`python -c "import app"` e `pyflakes` puliti); `main_chatbot_v2.py` rimosso/archiviato.
- [ ] Prompt: SP-01, SP-02, SP-03, SP-08, SP-13 applicati (SP-04…SP-07, SP-09…SP-12 solo con A/B approvato).
- [ ] Ogni task con diff non vuoto ha l'approvazione scritta del proprietario nel commit.
- [ ] README e documenti allineati ai numeri reali.

---

## Appendice A — Come riprodurre le evidenze `[V]`

```bash
git clone https://github.com/Michele1406/bot_embedding2 && cd bot_embedding2
pip install chromadb google-genai pandas openpyxl flask python-dotenv pydantic vulture pyflakes
cp -r database_vettoriale /tmp/db_copy     # lavorare su una copia: Chroma può scrivere

# 1) Ricettario inattivo: schema e componi()
python - <<'EOF'
import chromadb, json
c = chromadb.PersistentClient(path="/tmp/db_copy")
r = c.get_collection("ricette_sofood").get(include=["metadatas"])
print(len(r["ids"]), "ricette;",
      sum(1 for m in r["metadatas"] if "slot" in m), "con chiave 'slot';",
      sum(1 for m in r["metadatas"] if _ok(m)) if False else "")
bad = 0
for m in r["metadatas"]:
    try: json.loads(m["canali_sconsigliati"])
    except Exception: bad += 1
print("canali_sconsigliati NON json:", bad)        # atteso 408
EOF

# 2) Vulnerabilità /immagine (serve un .env finto)
printf 'GEMINI_API_KEY=FAKE\nFLASK_SECRET_KEY=x\n' > .env
python - <<'EOF'
import app
c = app.app.test_client()
print(c.get("/immagine", query_string={"path": ".env"}).status_code)   # atteso oggi: 200
EOF

# 3) Canale locale / cluster regionale
python -c "from core.profilazione_locale import rileva_canale_locale as f; print(f('bottega barese'))"   # oggi: misto
python -c "from core.retrieval_utils import rileva_cluster_regionale as r; print(r('sardine sott olio'), r('pizza capriziosa'))"  # oggi: sardegna trentino

# 4) Codice morto / import
vulture app.py core scripts --min-confidence 60 ; pyflakes app.py core/*.py main_chatbot_v2.py
python -c "import main_chatbot_v2"    # ModuleNotFoundError: core.query_decomposer

# 5) Metriche prompt
python -c "from core.system_prompt_v2 import SYSTEM_PROMPT_NINO as P; print(len(P), len(P.split()))"
```

## Appendice B — Mappa rapida "dove sta cosa" (commit 2c22c00)

| Area | File:righe |
|---|---|
| Analisi unificata LLM + fallback euristico | `app.py:237-362` |
| Safety net prodotti (parole/alias) | `app.py:403-587` |
| Orchestrazione turno | `app.py:590-1187` |
| Filtri business tagliere/finger food | `app.py:898-921` |
| Filtro vegano hard | `app.py:968-996` |
| Generazione + retry + reflection | `app.py:1062-1181` |
| Garante immagini / post-processing testo | `app.py:1099-1157` |
| Rotte (`/chat`, `/chat_audio`, `/immagine`) | `app.py:1190-1287` |
| Frontend HTML/JS | `app.py:1294-2182` |
| Ricerca ibrida `cerca_prodotti` | `retrieval_utils.py:603-867` |
| Ricettario (morto) | `retrieval_utils.py:425-582`, `:1002-1912` |
| Cluster regionali (vivo) | `retrieval_utils.py:1230-1246`, `fornitori_config.py` |
| Canale HORECA/RETAIL | `profilazione_locale.py` |
| System prompt | `core/system_prompt_v2.py` (149 righe) |
