# Studio: abbinamenti logici e composizione dei taglieri

Data: 2026-10-06. Richiesta: "Nino deve saper comporre in entrambi i modi (proposta completa e guidata) e gli abbinamenti devono essere logici, ad esempio non 2 salami".

## 1. Principi gastronomici usati

Un tagliere professionale è un **percorso di degustazione**, non un elenco di prodotti simili. Valgono quattro principi:

1. **Varietà di famiglia.** Ogni referenza rappresenta un modo diverso di lavorare la carne o il latte. Due salami (o un salame e una finocchiona) raccontano la stessa cosa.
2. **Contrasto di texture e stagionatura.** Si alternano morbido e compatto, giovane e stagionato, dolce e sapido.
3. **Latti diversi** per i formaggi: vaccino, ovino, caprino, bufalino.
4. **Intensità crescente.** Si va dal più delicato (crudo dolce, formaggio fresco o morbido) al più intenso (insaccato piccante, erborinato).

Gli accompagnamenti seguono abbinamenti classici:

| Prodotto | Accompagnamento classico |
|---|---|
| Formaggi duri e stagionati | Confetture, mostarde, miele |
| Erborinati | Miele, confetture |
| Formaggi freschi | Sottoli, pomodorini |
| Crudi | Taralli, grissini, pane croccante |
| Insaccati | Taralli, sottoli, olive |
| Cotti | Pane morbido, mostarde |

## 2. Famiglie adottate

Le regole sono **dati**, in `core/abbinamenti_horeca.yaml`, e il codice le applica in `core/famiglie_tagliere.py`.

| Gruppo | Famiglie, in ordine di preferenza | Esempi a catalogo |
|---|---|---|
| Salumi | crudo, insaccato, muscolo stagionato, cotto, manzo, grasso, spalmabile | Parma e San Daniele, salame e finocchiona, coppa e capocollo, mortadella e porchetta, bresaola e cecina, lardo e pancetta |
| Formaggi | duro, semiduro, erborinato, molle, fresco, aromatizzato | Parmigiano e pecorino riserva, caciocavallo, gorgonzola, taleggio, robiola, pecorino al pistacchio |

L'ordine dei controlli conta: "culatta **cotta**" è cotto, non crudo, e un erborinato ai mirtilli resta erborinato.

Il **tipo di latte** (vaccino, ovino, caprino, bufalino) si ricava da tipo e nome. Il **tipo base** ("pecorino", "parmigiano") vieta due formaggi dello stesso tipo, anche di famiglie diverse.

La classificazione è stata verificata su tutti i 155 prodotti da tagliere del catalogo.

## 3. Algoritmo di scelta

La funzione è `ricettario._scegli_vari`.

1. **Primo giro:** una famiglia per volta. Prima vengono le famiglie nominate dal cliente ("con la mortadella"), poi l'ordine preferito. Dentro una famiglia vince il prodotto più rilevante per la ricerca, preferendo un latte non ancora usato.
2. **Secondo giro:** serve solo se il cliente chiede più prodotti delle famiglie disponibili. Una famiglia si può ripetere, ma **mai lo stesso tipo di formaggio**.
3. Se il cliente forza una sottocategoria ("3 prosciutti di Parma"), i vincoli non si applicano.
4. Se la ricerca trova pochi candidati (per esempio senza embedding), il pool si completa con i prodotti da tagliere del catalogo che rispettano dieta, allergie, esclusioni, zona e fornitori regionali.

Il **controllo finale** (`domain_rules.check_board_violations`) segnala anche "due referenze dello stesso tipo di formaggio" e fa ripetere la scelta.

Il **second brain** (`abbinamenti`) propone prima gli abbinamenti classici della famiglia, poi quelli statistici del ricettario. Scarta sempre un abbinamento della stessa famiglia del prodotto (niente "salame con salame").

## 4. Le due modalità (D5)

La modalità si sceglie così, in ordine di priorità:

1. Parole esplicite nel messaggio: "fai tu" o "decidi tu" danno la modalità **completa**; "scegliamo insieme" o "dammi delle opzioni" danno la **guidata**.
2. Altrimenti, il campo `modalita_composizione` dell'analisi, memorizzato nel profilo.
3. In mancanza di entrambi, **completa**.

Per ogni prodotto scelto il contesto contiene un'**OPZIONE B** verificata: stessa famiglia o stessa tipologia, con gli stessi vincoli del cliente.

- **Completa:** proposta pronta componente per componente, con il perché degli abbinamenti, poi l'offerta di sostituire con le opzioni B o di costruirla insieme.
- **Guidata:** struttura del tagliere più due scelte per componente (A e B), poi la domanda; quando il cliente ha scelto, il riepilogo.

## 5. Misure

Le misure sono state fatte su 10 richieste di tagliere, in due modalità: solo lessicale ed embedding simulato. Lo script è `misura_taglieri.py`, nello scratchpad.

| | Prima | Dopo |
|---|---|---|
| Gruppi salumi/formaggi con lo stesso tipo (due pecorini...) | 0 | 0 |
| Gruppi con due prodotti della stessa famiglia | 0 rilevati, ma senza classificazione delle famiglie | 2: tagliere pugliese con caciocavallo e pallone di Gravina, perché la regione non ha altri formaggi |
| Taglieri regionali corretti (spagnolo, toscano, emiliano, trentino) | **0**: il riconoscimento della regione era rotto, "tagliere spagnolo" dava mortadella e Parma | tutti: spagnolo con prosciutto di Bellota, chorizo e lomo; trentino con speck e carne salada |
| "Degustazione di 5 formaggi" | 2 formaggi, più 3 salumi non richiesti | 5 formaggi di 5 famiglie, nessun salume |

**Golden set con embedding reali:** 23 casi su 23. Tra i casi nuovi ci sono `tagliere_spagnolo`, `tagliere_vario_3_3` e `degustazione_5_formaggi`.

**Prova end-to-end con il modello vero:** tagliere "fai tu" con 3 salumi e 3 formaggi:

- **Salumi:** Parma (crudo), capocollo di Martina Franca (muscolo stagionato), salamella piccante (insaccato).
- **Formaggi:** cremosa (semiduro), pecorino di fossa (duro), erborinato.

Il percorso di degustazione è spiegato, e ogni componente ha la sua opzione B.

## 6. Limiti noti

- Le famiglie si riconoscono da parole del tipo e del nome. Un prodotto con un nome anomalo può restare senza famiglia: in quel caso vale la sottocategoria. Due documenti del catalogo iniziano con "DESCRIZIONE:" invece che con il nome: vanno corretti nei dati.
- Alcune regioni hanno poche famiglie a catalogo (formaggi pugliesi e toscani): in quel caso la regione prevale sulla varietà.
- Gli abbinamenti classici puntano alle sottocategorie del catalogo. Se ne nascono di nuove, va aggiornato il YAML.
