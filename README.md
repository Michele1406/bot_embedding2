# Nino - Consulente virtuale HORECA di So Food

Chatbot B2B per la distribuzione alimentare (ristoranti, bar, pub, botteghe). Nino consiglia prodotti **realmente a catalogo**, compone menu/taglieri/tris e prepara l'ordine. Si basa su RAG (ChromaDB + Gemini) con regole di dominio, filtri rigidi e controlli sull'output, perché nel B2B un prodotto inventato è un errore commerciale.

## Principi

| Requisito | Come è garantito |
|---|---|
| **Zero allucinazioni** | Il modello vede solo schede estratte dal catalogo; dopo la risposta il *guardrail* verifica che ogni prodotto in grassetto esista e rispetti la dieta; se la richiesta contiene termini che non esistono nel catalogo il modello riceve l'avviso (abstention). |
| **Consulenza esperta** | Ricettario + ontologia di dominio (tris da bar, olive, finger food, sottoli...), *second brain* con abbinamenti dedotti dalle ricette reali, upselling pertinente. |
| **Memoria e intenti** | Profilo cliente (locale, dieta, canale, esclusioni permanenti "niente tonno") salvato su SQLite: sopravvive al riavvio. |
| **Regole HORECA** | Dieta applicata *prima* del ranking (niente carne ai vegani), formati grandi a HORECA e piccoli a retail, olive = intere in salamoia, prodotti "solo ingrediente" mai proposti da soli. |

## Architettura

```
messaggio -> analisi LLM (intento, profilo, esclusioni)
          -> retrieval ibrido: vettoriale (Chroma, filtri nel where) + lessicale (IDF) + famiglie dell'ontologia
          -> ricettario (template + slot risolti dall'ontologia sul catalogo reale)
          -> second brain (abbinamenti verificati, scheda azienda)
          -> prompt modulare -> Gemini (streaming SSE)
          -> guardrail sull'output -> risposta
```

Dati nel database vettoriale (`database_vettoriale/`):

| Collezione | Contenuto |
|---|---|
| `catalogo_v2` (**default**) | 1338 prodotti con attributi strutturati (tipo, ruoli d'uso, modalità d'uso, formato in g/ml, canale, conservazione, dieta) |
| `ricette_v2` (**default**) | ricettario Excel + ricette del DB + template aggiunti (tris bar, finger food, antipasto sottoli) |
| `aziende_sofood` | schede storia/valori dei fornitori (da `sofood/ABSTRACT.xlsx`) |
| `catalogo_sofood`, `ricette_sofood` | versioni precedenti, tenute per confronto (`CATALOGO_COLLECTION=catalogo_sofood`) |

## Struttura del progetto

```
app.py                      server Flask, orchestrazione del turno di chat, endpoint API
regole_cliente.yaml         configurazione tenant: azienda, tono, diete, regole tagliere
core/
  retrieval_utils.py        facade: ri-esporta i moduli di ricerca qui sotto (gli import esistenti continuano a funzionare)
    vincoli_dieta.py        dieta, contesto del turno (ContextVar), salumi di terra/mare
    ricerca_base.py         indici in memoria, match lessicale (IDF)/esatto/fornitore, ricerca vettoriale
    ricerca_prodotti.py     cerca_prodotti: punto d'ingresso della ricerca ibrida
    contesto_prodotti.py    formattazione dei prodotti nel contesto del modello
    ricettario.py           template, slot, taglieri, tris, sostituti
  ontologia.py + .yaml      regole per famiglia di prodotto (olive, taralli, frutta secca, finger food, topping, sottoli...)
  second_brain.py           grafo di conoscenza: abbinamenti (classici + ricettario), alternative, schede aziende
  famiglie_tagliere.py + abbinamenti_horeca.yaml   varieta' del tagliere (mai due salami/pecorini), abbinamenti classici
  anagrafica_fornitori.py   riconosce i produttori nominati dal cliente (alias calcolati dal catalogo)
  allergeni.py              allergie del cliente (14 allergeni UE) filtrate su allergeni/tracce del catalogo
  ordini.py                 ordine in due passi: riepilogo -> CONFERMO -> data/ordini/*.json con tutte le info
  info_azienda.py           consegne, ordine minimo, costi, pagamenti (sofood/consegne.xlsx)
  embedder.py               embedding delle query con cache (memoria + data/cache_embedding.sqlite)
  manutenzione.py           conservazione dati: log 30 giorni, sessioni 7, ordini mai cancellati
  logistica.py + zone_consegna.yaml   fuori zona refrigerata (solo Puglia/Basilicata) niente freschi/surgelati; calendario ordini freschi
  guardrail_output.py       verifica di prodotti, produttori, formati e canale citati nella risposta
  copertura_richiesta.py    abstention: termini della richiesta senza riscontro a catalogo
  ordine_validazione.py     righe d'ordine verificate sul catalogo + checksum Partita IVA prima dell'inoltro
  riassunto.py              riassunto rolling della conversazione lunga (modello lite)
  sicurezza.py              pulizia input, rilevamento prompt injection, rate limit
  audit_log.py              una riga JSONL per risposta (log/audit/)
  whatsapp.py               adattatore WhatsApp Cloud API (spento di default)
  attributi_derivati.py     snack_tipo, conservazione, denocciolate, vegano da ingredienti, correzione dei flag dieta
  parse_formato.py          formato -> grammi/ml + canale horeca/retail
  schema_prodotto.py        schema Pydantic degli attributi estratti dall'LLM
  sessioni_store.py         persistenza sessioni (SQLite, TTL 7 giorni)
  system_prompt_v2.py       prompt modulare (identita', logistica, canale, composizione, dieta, sicurezza)
  cart_manager.py, order_extractor.py   checkout e ordine (senza ERP configurato si salva in data/ordini/)
  profilazione_locale.py, domain_rules.py, fornitori_config.py, config_manager.py, tassonomia_sofood.py
scripts/                    pipeline dati (vedi sotto)
data/                       attributi estratti (jsonl), audit ricettario, ricettario solo-DB
tests/                      ~230 test (unittest) + golden set di regressione (23 casi, tests/golden/casi.yaml)
```

I file di anagrafica stanno nella cartella dati `DATA_LAKE_PATH` (PRODOTTI SOFOOD, `core/percorsi.py`): nella radice
Tassonomia.xlsx, fornitori.csv, riassunto_prodotti*.xlsx, varianti_prodotto.csv; in `sofood/` ABSTRACT.xlsx, consegne.xlsx,
calendario_freschi.xlsx, SOFOOD.txt, regioni_produttori.csv. Le versioni precedenti sono in `old/`.

## Avvio

1. `.env` (vedi `.env.example`): `GEMINI_API_KEY`, `FLASK_SECRET_KEY`, modelli `LLM_*`.
2. `avvia_bot.bat` oppure `.venv\Scripts\python.exe app.py` -> `http://127.0.0.1:5000`.

Variabili utili: `CATALOGO_COLLECTION`, `RICETTE_COLLECTION` (default v2), `GUARDRAIL_OUTPUT=0` (disattiva il guardrail), `SESSIONI_DB`, `ERP_WEBHOOK_URL`.

### Endpoint
- `POST /api/v1/chat/stream` chat in streaming SSE (usare questo da WhatsApp)
- `POST /chat` chat sincrona, `POST /chat_audio` messaggio vocale

## Ordini

Quando il cliente vuole ordinare, Nino estrae prodotti, quantita' e unita' dalla chat, li verifica sul catalogo (codice +
P.IVA con checksum) e mostra un **riepilogo**. Solo dopo la parola **CONFERMO** l'ordine viene salvato in
`data/ordini/ordine_<id>.json` (cliente, contatti, righe con codice/formato/temperatura, avvisi, conversazione) oppure
inviato a `ERP_WEBHOOK_URL` se configurato. Lo stesso ordine non si registra due volte; "annulla" scarta la bozza.

## Composizione (taglieri e menu)

Due modalita': **completa** (proposta pronta + opzioni B per sostituire) e **guidata** (due scelte per componente, poi
il cliente decide). Varieta' e abbinamenti seguono `core/abbinamenti_horeca.yaml`; lo studio e' in `docs/STUDIO_ABBINAMENTI.md`.

## WhatsApp

`core/whatsapp.py` espone `GET/POST /webhook/whatsapp` (verifica Meta, firma `X-Hub-Signature-256`, deduplica, una sessione per numero,
risposte spezzate in messaggi da <= 3500 caratteri, rate limit 10 messaggi/minuto per numero). Si attiva con `WHATSAPP_ENABLED=1` e le
variabili `WHATSAPP_*` di `.env.example`. Limiti attuali: solo testo (vocali e foto ricevono un invito a scrivere), niente template per
messaggi iniziati da noi.

## Aggiornare i dati

| Operazione | Comando |
|---|---|
| Schede aziende (ABSTRACT) | modificare `sofood/ABSTRACT.xlsx`, poi `python scripts/carica_abstract_fornitori_v2.py` (incrementale) |
| Nuovi prodotti / attributi | `python scripts/aggiorna_catalogo.py --stima` poi senza `--stima` (estrazione LLM, formati, `catalogo_v2`) |
| Ricettario | `python scripts/aggiorna_ricettario.py` (rigenera `ricette_v2`, audit in `data/audit_ricettario.csv`) |
| Regole per famiglia di prodotto | `core/ontologia_horeca.yaml` e `core/ricettario_extra.yaml` (nessun codice) |
| Diete e tagliere | `regole_cliente.yaml` |
| Zone di consegna refrigerata | `core/zone_consegna.yaml` |
| Correzione flag dieta dopo cambi di regola | `python scripts/correggi_diete_catalogo.py` |
| Audit qualita' attributi / lacune di catalogo | `python scripts/audit_attributi.py`, `python scripts/report_lacune_catalogo.py` (CSV in `data/`) |

Nella cartella dati (`DATA_LAKE_PATH`) le schede prodotto sono **di sola lettura**; gli script aggiornano solo le anagrafiche (vedi sopra).

## Test

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t . -p "test_*.py"
```

Alcuni test d'integrazione usano ChromaDB e le API di embedding (con ripiego se la quota è esaurita).

## Decisioni di dominio

- **Tris da bar**: 3 ciotoline tra olive, taralli/grissini, frutta secca, patatine, snack di riso (non assoluto: se la richiesta indica altro, vince la richiesta).
- **Olive**: solo intere in salamoia con nocciolo; sott'olio, condite o denocciolate solo se richieste.
- **Modalità d'uso** per prodotto: `singolo`, `ingrediente`, `entrambi` (petali di tartufo = solo ingrediente; anacardi al tartufo = entrambi).
- **Vegano**: flag `SI` oppure `SI*` con ingredienti verificati tutti vegetali (`accetta_dedotto_se_ingredienti_vegetali`).
- **Lacune di catalogo note**: arachidi, snack di riso alla paprika, fior di latte, pane, insalate.

Le istruzioni per chi sviluppa con Claude Code sono in `CLAUDE.md`.
