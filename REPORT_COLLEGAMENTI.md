# Report: regole e collegamenti tra prodotti che mancano

Data: 2026-10-06. Indagine sul catalogo reale (`catalogo_v2`, 1338 prodotti, 76 produttori) e sul second brain.
Nessuna modifica fatta: in attesa di OK.

## 1. Cosa esiste oggi

| Collegamento | Dove | Copertura |
|---|---|---|
| Famiglie da tagliere (crudo, insaccato... / duro, erborinato...) | `abbinamenti_horeca.yaml` | salumi 109/112, formaggi 81/118 |
| Abbinamenti classici | `abbinamenti_horeca.yaml` | 12 regole, **solo** per famiglie di salumi e formaggi |
| Abbinamenti dalle ricette | second brain (378 ricette) | 648 coppie |
| Alternative (stessa tipologia) | second brain | 1163/1338 prodotti |
| Linea del produttore | second brain | tutti |
| Famiglie snack/aperitivo (olive, taralli, finger food...) | `ontologia_horeca.yaml` | 83 prodotti con `snack_tipo` |
| Territorio | `fornitori_config.py` | 44 voci produttore/regione, solo per alcuni ruoli |
| Logistica freschi (giorno ordine/arrivo) | `sofood/calendario_freschi.xlsx` | gia' usato |
| Incompatibilita' (terra/mare...) | `domain_rules.py` | gia' usato |

## 2. Cosa manca (con i numeri)

### A. Priorita' alta

**A1. Abbinamenti fuori dal tagliere: 466 prodotti (35%) non ne hanno nessuno.**
Le regole classiche coprono solo salumi e formaggi. Restano scoperti, per intero:
- MARE "altri prodotti" 96/96: bresaola di tonno, bottarga, acciughe, tonno sott'olio, salmone affumicato, caviale, baccala' mantecato, riccio di mare;
- olive 31/34, grissini 7/7, specialita' croccanti 6/6 (proprio gli snack da aperitivo);
- gelati e sorbetti 27/27, liquori 9/9, birre 6/6, frutta sotto spirito 6/6, pasticceria 6/6;
- salmone fresco 6/6, aceto 12/12, maionese 7/7.

Proposta: estendere `abbinamenti_classici` con regole per tipo (dato YAML, non prompt). Esempi:

| Prodotto | Si abbina a |
|---|---|
| tonno sott'olio / bresaola di tonno | cipolla di Tropea, sottoli, pane croccante |
| acciughe, alici | burro, pane croccante, pomodori secchi |
| salmone affumicato | burro, pane croccante, agrumi |
| bottarga | olio EVO, pasta (come finitura) |
| olive | taralli, frutta secca, sottoli (ciotoline da aperitivo) |
| taralli e grissini | olive, salumi |
| finger food fritto, patatine | birre |
| gelato | frutta sotto spirito, creme spalmabili dolci, biscotti |
| dolci e pasticceria | liquori |

Le regole devono passare da te: sono scelte gastronomiche, io preparo la bozza.

**A2. Alternative per gli ingredienti: 175 prodotti non ne hanno nessuna.**
Tutti i 50 oli EVO, 31 farine, 14 risi, 12 aceti, 9 passate, il sale. La causa e' una regola giusta applicata
troppo: i prodotti "solo ingrediente" non si propongono da soli, quindi non diventano mai alternative. Ma se il
cliente chiede "avete un altro olio?" o "un'alternativa a questa farina?" l'alternativa serve. Verificato: con la
regola allentata l'olio al tartufo trova 2 alternative.
Proposta: ammettere gli ingredienti come alternative quando il cliente chiede di quel prodotto (non nelle
composizioni da aperitivo/tagliere). Modifica piccola, rischio basso.

**A3. Stesso prodotto in piu' formati: 128 gruppi senza collegamento esplicito.**
Esempio: Mongetto marmellata di arance 40 g / 230 g / 1,2 kg, Finocchiona IGP / IGP Gigante. Oggi Nino li tratta
come prodotti diversi: rischia di proporli come alternative (gia' tamponato con un controllo sul nome) e non sa
dire "c'e' anche il formato da 1,2 kg per la ristorazione".
Proposta: collegamento "altri formati dello stesso prodotto" calcolato dal catalogo (produttore + nome senza peso,
piu' `varianti_prodotto.csv`). Serve a:
- scegliere il formato giusto per il canale (horeca grande, retail piccolo, "marmellatina" = piccolo);
- dirlo al cliente;
- escluderli per regola dalle alternative.

**A4. Regione d'origine dei produttori: manca per la maggior parte.**
Il territorio esiste solo nei 44 abbinamenti produttore-ruolo di `fornitori_config.py`. Per tutti gli altri
prodotti Nino non sa da dove vengono, quindi "prodotti lucani", "un menu calabrese" o "solo pugliesi" funzionano
solo per salumi, formaggi e pochi altri ruoli.
Proposta: una colonna `regione` per i 76 produttori in `fornitori.csv` . Bozza ricavata dalle
schede ABSTRACT ("Identita'", "Storia e Radici"), da far controllare a te. Poi ogni prodotto eredita la regione del
produttore e il filtro regionale vale per qualunque categoria.

### B. Priorita' media

**B1. Famiglie da tagliere incomplete.**
- Salumi senza famiglia: Pezzente dolce/piccante (insaccato lucano) e Coppiette di suino (carne essiccata). Basta
  aggiungere le parole al YAML.
- Formaggi senza famiglia: 37, ma quasi tutti non sono da tagliere (burro, yogurt). Errore di dato: il **Pesto
  Maremma 'Mpestata** risulta nel reparto FORMAGGI, da correggere alla fonte.
- Una sola referenza "molle": i taglieri con 5 formaggi ripetono per forza le famiglie.

**B2. Dal prodotto al piatto ("con questo puoi fare...").**
Oggi il collegamento va solo dal piatto al prodotto (ricetta -> ingredienti). Manca il contrario: se il cliente
chiede della bottarga, Nino potrebbe suggerire "spaghetti alla bottarga" o un antipasto, prendendolo dalle 378
ricette gia' presenti. E' l'upselling consulenziale piu' naturale. Si costruisce dai dati che ci sono, senza API.

**B3. Complementari d'uso (cosa serve per prepararlo, non cosa ci sta bene).**
Diverso dall'abbinamento di gusto: finger food da friggere -> olio di semi per frittura; farina per pizza ->
pelati/passata, mozzarella, olio; panini/hamburger -> salse e maionese. Utile per chiudere ordini piu' completi.
Nel catalogo c'e' un solo olio di semi: la regola sarebbe pronta per quando l'assortimento cresce.

**B4. Incompatibilita' in un tagliere/tris oltre terra/mare.**
Mancano: due prodotti piccanti nello stesso tris, dolce e salato mescolati in una ciotolina, due affumicati.
Regole semplici nel YAML, simili ai "doppioni" gia' esistenti.

### C. Priorita' bassa / dati

- **Stagionalita'**: nessun dato (gelati e sorbetti d'estate, prodotti di ricorrenza). Serve prima il dato.
- **Allergeni**: restano i 189 prodotti incoerenti gia' segnalati (`data/audit_attributi.csv`), da correggere alla
  fonte: influenzano filtri e alternative.
- **`snack_tipo`** e' su 83 prodotti: copre bene olive, taralli e finger food. Mancano frutta secca (5 su 7 in
  sottocategoria) e le specialita' croccanti: si completano insieme ad A1.

## 3. Cosa scarterei

- **Abbinamenti statistici "per ingrediente" sui piatti composti**: gia' tolti (pistacchio sugli spaghetti allo
  scoglio); non vanno reintrodotti.
- **Collegamenti calcolati con il modello LLM su tutto il catalogo**: costosi in quota e non verificabili; meglio
  regole per tipo che rivedi tu.
- **Prezzi/margini negli abbinamenti**: non ci sono dati e Nino non parla di prezzi.

## 4. Ordine consigliato

1. A2 alternative per gli ingredienti (piccola, subito utile).
2. A3 altri formati dello stesso prodotto.
3. B1 famiglie mancanti + correzione del pesto (dato).
4. A1 abbinamenti oltre il tagliere: bozza YAML che rivedi tu, poi la attivo.
5. A4 regione dei produttori: bozza da ABSTRACT, la controlli, poi la attivo.
6. B2 dal prodotto al piatto.
7. B3 e B4 quando servono.

Ogni punto: regola nei dati (YAML/CSV), un test per regola, verifica sul catalogo reale, nessuna chiamata API
tranne la prova finale della chat.

## 5. Stato (2026-10-06): fatto

| Punto | Cosa | Dove | Effetto sul catalogo reale |
|---|---|---|---|
| A1 | 20 regole di abbinamento per tipo (pesce conservato, acciughe, salmone, bottarga/baccala', affettati di mare, olive, croccanti, frutta secca, fritti, birre, gelato, sorbetto, baba', biscotti, liquori, aceto, mozzarella, confetture dolci e salate, nduja) | `core/abbinamenti_horeca.yaml` `abbinamenti_per_tipo` | prima 466 prodotti senza abbinamenti; ora 59 senza nessun collegamento (abbinamento, complementare o piatto): legumi secchi e spezie, che sono ingredienti |
| A2 | l'alternativa a un ingrediente e' un ingrediente | `second_brain.alternative` | prodotti senza alternative: da 175 a 38 |
| A3 | stesso prodotto in piu' formati (129 gruppi, 308 prodotti); mai proposti come alternativa l'uno dell'altro | `second_brain.altri_formati` | riga "disponibile anche nei formati..." |
| A4 | regione per produttore: bozza `sofood/regioni_produttori.csv` (77 produttori: 39 alta, 23 media, 14 bassa, 1 senza indizi) | `core/territorio.py`, `scripts/bozza_regioni_produttori.py` | "che salumi lucani avete?" trova i salumi di Sapori Mediterranei; anche i taglieri regionali usano tutti i produttori della regione |
| B1 | Pezzente (insaccato), coppiette (muscolo stagionato); pesto escluso dai formaggi da tagliere | YAML + `ricettario` | salumi senza famiglia: 0 |
| B2 | piatti del ricettario in cui il prodotto e' ingrediente (prima da protagonista) | `second_brain.piatti_con` | "bottarga" -> linguine alla bottarga, risotto con bottarga |
| B3 | 6 regole di complementari (da friggere, basi pizza/pinsa, farine da pizza, pasta, sughi, panini) | YAML `complementari` | "pinsa" -> pelati, mozzarella, olio |
| B4 | tratti da non ripetere nel tagliere (piccante, affumicato, tartufato): preferenza, non divieto; non vale se il cliente nomina il tratto | YAML `tratti_da_non_ripetere` | |

Il bot usa la regione solo per le righe con confidenza **alta** o con la colonna `verificata` = si.
**Da fare a mano**: controllare `sofood/regioni_produttori.csv` (soprattutto le righe media/bassa: Acquerello risulta
"toscana" ma e' piemontese; Casa Marrazzo e Italfish da confermare; La Nicchia senza indizi) e scrivere "si" in
`verificata`. Rilanciare lo script non tocca le righe gia' verificate.

Restano da correggere alla fonte (dati di sola lettura): il Pesto Maremma 'Mpestata nel reparto FORMAGGI, i 189
allergeni incoerenti, il refuso "Dissosato".
