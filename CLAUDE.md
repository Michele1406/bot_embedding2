# CLAUDE.md - Nino (So Food)

Chatbot HORECA B2B (Flask + ChromaDB + Gemini). Leggi `README.md` per l'architettura.

## Regole non negoziabili
- **Zero allucinazioni**: mai far citare al modello prodotti non presenti nel contesto; ogni nuova regola va sul *dato* (metadati, YAML), non su istruzioni al prompt.
- **Cartella dati** `C:\Users\baron\LAVORO\PRODOTTI SOFOOD` (`DATA_LAKE_PATH`, `core/percorsi.py`): le schede prodotto (cartelle 19xxxxxx) sono di sola lettura; le anagrafiche (radice: Tassonomia, fornitori, riassunti, varianti; `sofood/`: ABSTRACT, consegne, calendario freschi, SOFOOD.txt, regioni_produttori.csv) sono la fonte unica e gli script le aggiornano li'. Prima di modificarle salva una copia in `old/`.
- **Segreti**: non stampare né committare `.env` (contiene `GEMINI_API_KEY`).
- Dieta, esclusioni del cliente e canale si applicano **prima** del ranking (nel `where` di Chroma o nei filtri), mai solo a valle.
- Costi API: usare `gemini-3.5-flash-lite` (15 chiamate/min, 500 totali) per estrazioni/test; `gemini-3.6-flash` solo se serve (5/min, 20 totali). Niente shard paralleli. Gli embedding consumano la stessa quota.

## Comandi
```powershell
.venv\Scripts\python.exe app.py                                        # server su :5000
.venv\Scripts\python.exe -m unittest discover -s tests -t . -p "test_*.py"   # tutti i test devono passare (quelli che richiedono l'embedding risultano SKIP se la quota e' esaurita)
.venv\Scripts\python.exe -m core.second_brain                          # controllo a campione del second brain
.venv\Scripts\python.exe scripts\aggiorna_catalogo.py --stima          # stima chiamate prima di rigenerare i dati
```
Usa sempre `.venv\Scripts\python.exe` (il Python di sistema non ha chromadb).

## Mappa del codice
- `app.py`: turno di chat (`stream_messaggio_nino`), analisi LLM, safety net, guardrail, sessioni.
- `core/retrieval_utils.py` e' una FACADE: la logica sta in `vincoli_dieta`, `ricerca_base`, `ricerca_prodotti`, `contesto_prodotti`, `ricettario` (nessun ciclo: vincoli <- base <- prodotti <- contesto <- ricettario). ContextVar per turno: `_DIETA_CORRENTE`, `_CANALE_CORRENTE`, `_QUERY_ORIGINALE`, `ontologia.ESCLUSIONI_CORRENTI`, `logistica.ZONA_CORRENTE`.
- `core/ontologia.py` + `ontologia_horeca.yaml`: famiglie di prodotto (olive, taralli, frutta secca, finger food, topping, sottoli), template preferiti, sinonimi. **Nuova regola di prodotto = modifica YAML.**
- `core/second_brain.py`: abbinamenti (regole per tipo e classici da `abbinamenti_horeca.yaml`, poi lift sul ricettario), alternative, altri formati, complementari, piatti con il prodotto, schede aziende da `aziende_sofood`.
- `core/famiglie_tagliere.py` + `core/abbinamenti_horeca.yaml`: famiglie da tagliere (crudo, insaccato... / duro, erborinato...), varieta', tratti da non ripetere, abbinamenti classici, **abbinamenti per tipo** e **complementari** (selettori parole/sottocategorie/snack_tipo/famiglie). Studio: `docs/STUDIO_ABBINAMENTI.md`, collegamenti: `REPORT_COLLEGAMENTI.md`.
- `core/guide_prodotto.py` + `core/guide_prodotto.yaml`: guida tecnica (tagli di carne, legumi/cereali, riso, farine, pomodoro, uova, formaggi da cucina, pesce, olio per fruttato, aceto): carattere, cotture, ammollo, piatti, porzione indicativa, tagli simili; dati della scheda (linea Oberto, tempi di cottura, tipo di fruttato, formato) in priorita'. `riga()` entra in `_righe_dettaglio`, `consiglio()` risponde a "che taglio per la griglia / che legumi per una zuppa" (app.py, dopo la panoramica). **Nuova guida = modifica YAML** (`modifica: true` per note tipo "gia' cotti"; `usi` per il consiglio).
- `core/territorio.py`: regione/nazione chiesta dal cliente (solo nomi e aggettivi: "a Bari" e' dove sta il locale) e regione dei produttori da `sofood/regioni_produttori.csv` (generato da `scripts/bozza_regioni_produttori.py`: prima l'indirizzo nelle schede prodotto, poi controllo con ABSTRACT; righe con nota "manuale" non vengono toccate). Nazione estera anche per singolo prodotto (riga PRODUTTORE).
- `core/costruzione_tagliere.py`: tagliere costruito INSIEME al cliente (non convinto -> numeri -> rose numerate -> scelte -> riepilogo). Liste e domande sono testi fissi (nessuna API); stato in `stato["costruzione"]`.
- `core/testo_prodotto.scheda_strutturata`: scheda nel contesto divisa in "Racconto del prodotto" (per argomentare) e "Dati tecnici" (per domande mirate). `sofood/SOFOOD.txt` = chi e' So Food, entra nel prompt (personalita' di Nino).
- `core/anagrafica_fornitori.py` (produttori nominati), `core/allergeni.py` (ContextVar `ALLERGIE_CORRENTI`), `core/ordini.py` (riepilogo -> CONFERMO -> JSON), `core/info_azienda.py` (consegne da `sofood/consegne.xlsx`), `core/embedder.py` (cache embedding), `core/manutenzione.py` (retention).
- `core/foto.py` (quando mostrare le foto: solo prodotto certo + file esistente; prodotti "in primo piano" per le domande anaforiche), `core/formato_testo.py` (pulizia markdown unica: nessuna regex deve attraversare un a-capo). In `app.py` la proposta attiva si riusa (`messaggio_su_proposta_attiva`, `ricostruisci_proposta`) invece di cercarne una nuova.
- `core/guardrail_output.py` (prodotti, produttori, formati/canale), `core/copertura_richiesta.py` (abstention), `core/ordine_validazione.py` (righe ordine + P.IVA), `core/sicurezza.py`, `core/logistica.py`, `core/audit_log.py`, `core/riassunto.py`, `core/whatsapp.py`.
- `core/system_prompt_v2.py`: prompt modulare; qualunque nuova riga di contesto che il modello deve capire va spiegata qui.
- `regole_cliente.yaml`: tenant, diete (`accetta_dedotto_se_ingredienti_vegetali: true`), tagliere.

## Test golden e quota API
- `tests/golden/casi.yaml` + `tests/test_golden.py`: regressione sul retrieval reale. Gli embedding delle query si salvano in `data/cache_embedding_test.json` (`tests/ambiente.py`): la quota si paga solo alla prima esecuzione. Con quota esaurita i casi risultano SKIP (mai "superati").
- Il piano gratuito dell'embedding ha 1000 chiamate/giorno: il bot in produzione ne consuma una per ricerca. Per WhatsApp serve il piano a pagamento.
- `python -m tests.test_golden` stampa il rapporto per caso.

## Convenzioni
- Italiano per commenti, messaggi e prompt; testo ASCII con apostrofo (`e'`) nei prompt/YAML per evitare problemi di encoding.
- File di test in `tests/test_*.py` (unittest, nessuna dipendenza nuova). Ogni bug corretto -> un test.
- Collezioni Chroma: `catalogo_v2` / `ricette_v2` di default; `catalogo_sofood` / `ricette_sofood` sono legacy. `aziende_sofood` = schede fornitori (non sono prodotti).
- Prodotti "non prodotto" (schede aziendali, logistica, calendari) restano fuori da `catalogo_v2`.
- Il formato è in g/ml (`parse_formato`), canale `horeca` >= 1000, `retail` <= 500.

## Cartelle speciali
- `data/`: attributi estratti (jsonl), audit, cache degli embedding e sessioni (gli ultimi due non vanno su git).
- `database_vettoriale/`: ChromaDB locale, fuori da git (chroma.sqlite3 supera i 100 MB di GitHub); si rigenera con gli script (costa embedding).

## Windows / strumenti
- Shell Git Bash: negli heredoc le sequenze con backslash (n, b, s) vengono alterate e possono corrompere il codice; per modifiche con backslash usa gli strumenti Edit/Write, non script Python in heredoc. Dopo ogni modifica a `app.py` controlla la sintassi con `python -c "import ast;ast.parse(open('app.py',encoding='utf-8').read())"`.
- Dopo modifiche fatte da script controlla che non ci siano caratteri di controllo: `tests/test_ontologia.py::TestNessunCarattereDiControllo` fallisce se un `` e' diventato backspace.
- I test non devono mai scrivere fuori dal progetto (usa `tempfile`): un percorso come `/dev/null/x` su Windows crea cartelle reali su C:.
- Il database tracciato da git (`database_vettoriale/`) cambia a ogni rigenerazione: non fare commit dei binari senza accordo.
