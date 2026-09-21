# Report per Claude: Struttura ed Origine dei Metadati nel Vettoriale ChromaDB

In merito alla tua analisi architetturale e alla proposta di strutturare i metadati all'interno di ChromaDB, abbiamo estratto l'intero contenuto del database vettoriale persistente (`database_vettoriale`) in un file Excel di audit: **`export_metadati_chromadb.xlsx`**.

Il file è composto da due fogli:
1. **`Prodotti`** (1.227 record, 37 colonne): tutte le referenze commerciali a catalogo.
2. **`Altro`** (437 record, 19 colonne): dati aziendali, schede fornitore, logistica e ricette.

Di seguito il dettaglio puntuale di cosa contiene ciascun foglio, da dove proviene ogni dato e perché è strutturato in questo modo.

---

## FOGLIO 1: "Prodotti" (1.227 Record)

Questo foglio contiene esclusivamente i prodotti fisici a catalogo. I metadati presenti sono il risultato di due fasi di indicizzazione storiche distinte: l'importazione iniziale dei dati grezzi e il successivo arricchimento tassonomico.

### 1. Dati Identificativi e Anagrafica Fornitore
* **`id`**: identificatore univoco del record in ChromaDB (formato: `{codice_fornitore}_{codice_prodotto}`, es. `19010014_OBE4001`).
* **`codice_prodotto`**: codice articolo So Food / fornitore (es. `OBE4001`, `FARINO10`, `CP00001`).
* **`codice_fornitore`**: codice numerico anagrafico del fornitore (es. `19010014`, `19010829`).
* **`nome_fornitore`**: ragione sociale / brand commerciale (es. *Oberto*, *Pastificio Gentile*, *Boschi 1961*).
  * **Origine**: caricati da `caricaprodotti_v2.py` incrociando i codici cartella su disco con il file anagrafico `fornitori.csv`.

---

### 2. Tassonomia e Classificazione Merceologica (Spiegazione Gerarchia)
Nel foglio compaiono tre colonne apparentemente simili: `categoria_prodotto`, `categoria_tassonomia` e `reparto`. Questa convivenza deriva dal passaggio da un sistema legacy al sistema ufficiale:

* **`categoria_prodotto` (Campo Legacy)**:
  * **Origine**: proviene dal foglio Excel originale `riassunto_prodotti.xlsx` (`caricaprodotti_v2.py`, riga 224: `dettagli["categoria"] = nome_foglio`).
  * **Valori**: *Dispensa*, *Salumi*, *Formaggi*, *Mare*, *Carni*, *Gelo*. Rappresenta la suddivisione grezza iniziale usata per la prima ingestione.
* **`reparto` (Tassonomia Ufficiale - Livello 1)**:
  * **Origine**: definito in `tassonomia_sofood.py` e popolato da `arricchisci_catalogo.py`.
  * **Valori**: `DISPENSA`, `SALUMI`, `FORMAGGI`, `MARE`, `CARNI`, `GELO`.
  * **Perché è quasi sempre identico a `categoria_prodotto`**: perché la tassonomia ufficiale a 3 livelli ha formalizzato come macro-reparti merceologici gli stessi 6 raggruppamenti del catalogo iniziale. `categoria_prodotto` è rimasto come retaggio per retrocompatibilità.
* **`categoria_tassonomia` (Tassonomia Ufficiale - Livello 2)**:
  * **Origine**: definito in `tassonomia_sofood.py` e popolato da `arricchisci_catalogo.py`.
  * **Valori**: categorie merceologiche intermedie (es. dentro *DISPENSA*: *Conserve*, *Pasta e riso*, *Farine e lieviti*, *Sott'oli e sott'aceti*, *Dolci e prodotti da forno*; dentro *SALUMI*: *Salumi cotti*, *Salumi crudi*, *Insaccati*; dentro *FORMAGGI*: *Formaggi freschi*, *Formaggi stagionati*, *Latticini*).
* **`sottocategoria` (Tassonomia Ufficiale - Livello 3)**:
  * **Origine**: classificata via LLM (Gemini) tramite `arricchisci_catalogo.py` mappando ogni prodotto su una delle 73 sottocategorie fisse di `tassonomia_sofood.py`.
  * **Valori**: la classe specifica del prodotto (es. *Pasta secca di semola*, *Pasta fresca*, *Mortadella*, *Pomodorini e datterini*, *Basi per pizza e impasti*, *Pecorino*, *Caciocavallo*).

**Gerarchia effettiva in uso:**
$$\text{reparto (Liv. 1)} \longrightarrow \text{categoria\_tassonomia (Liv. 2)} \longrightarrow \text{sottocategoria (Liv. 3)}$$

---

### 3. Filtri Dietetici e Vincoli di Servizio
* **`vegano`**, **`vegetariano`**, **`senza_glutine`**, **`senza_lattosio`**, **`biologico`**, **`kosher`**, **`milk_free`**:
  * **Origine**: estratti dal file tabellare `riassunto_prodotti.xlsx` e integrati in ChromaDB durante l'ingestione (`caricaprodotti_v2.py`).
  * **Valori**: stringhe booleane normalizzate (`SI` / `NO`). Permettono al motore di retrieval filtri deterministici (es. esclusione categorica di carne/pesce/formaggi per clienti vegani in `retrieval_utils.py`).
* **`allergeni`**, **`tracce_di`**:
  * **Origine**: schede tecniche fornitore sintetizzate in `riassunto_prodotti.xlsx`.

---

### 4. Metadati Multimodali e File Fisici
* **`ha_immagine_primaria`** (booleano): `True` se esiste l'immagine JPG associata al prodotto.
* **`percorso_immagine`** (stringa): percorso assoluto del file JPG su disco locale (es. `c:\Users\...\immagini\OBE4001.jpg`).
  * **Origine**: verificato fisicamente da `caricaprodotti_v2.py` via `file_jpg.exists()`.
  * **Perché esiste**: serve a Nino per emettere il tag `[IMG: <percorso>]` nella chat solo se la foto è effettivamente disponibile su disco.

---

### 5. Metadati Gastronomici Specialistici (Spezie Boschi)
* **`buono_per_pesce`**, **`buono_per_carne`**, **`buono_per_patate`**, **`buono_per_primi`**, **`buono_per_pizza`**, **`buono_per_verdure`**, **`buono_per_bbq`**:
  * **Origine**: inseriti da script di arricchimento sui prodotti del fornitore *Boschi 1961* estraendo le destinazioni d'uso dai testi tecnici del produttore.
  * **Perché esistono**: usati da `cerca_prodotti()` in `retrieval_utils.py` per boostare le spezie corrette in base al contesto della ricetta (es. mix marinara se la query parla di pesce, rub BBQ se parla di hamburger).

---

### 6. Contenuto Testuale Indicizzato
* **`documento_anteprima`**: prima riga del testo del prodotto (titolo commerciale pulito).
* **`documento_completo`**: il testo integrale vettorializzato con `gemini-embedding-2` (descrizione organolettica, formati, pezzature, note di servizio e disclaimer So Food).

---

## FOGLIO 2: "Altro" (437 Record)

Questo foglio raccoglie tutti i documenti vettorializzati che **non sono singoli prodotti commerciali**, ma costituiscono la base di conoscenza operativa del consulente Nino. Sono classificati tramite la colonna **`tipologia_record`**:

### 1. `Ricetta / Piatto Composto` (342 record)
* **Collezione di origine**: `ricette_sofood`.
* **Origine dati**: caricati da `carica_ricettario.py` a partire dal file `Ricettario_SO_FOOD.xlsx` e da `filexlsx.py`.
* **Cosa contengono**: template di composizione gastronomica (primi, secondi, pizze gourmet, taglieri regionali, aperitivi). Ciascun record ha campi strutturati nei metadati:
  * `id_ricetta`, `nome_piatto`, `categoria` (primo, secondo, pizza, tagliere), `regione_stile` (*Puglia, Toscana, Spagna, ecc.*), `ingredienti_richiesti` (lista di slot generici da riempire con prodotti a catalogo).

### 2. `Scheda Aziendale Fornitore` (74 record)
* **Collezione di origine**: `catalogo_sofood` (con ID `FORNITORE_{codice}_{nome}`).
* **Origine dati**: caricati da `carica_abstract_fornitori_v2.py` elaborando le schede storiche dei produttori.
* **Cosa contengono**: storia dell'azienda, territorio di produzione, certificazioni (DOP, IGP, BIO), metodi di lavorazione artigianale e punti di forza commerciali.
* **Perché esistono**: permettono a Nino di raccontare il produttore con naturalezza quando consiglia una referenza, senza inventare la storia dell'azienda.

### 3. `Calendario Ordini Freschi` (11 record)
* **Collezione di origine**: `catalogo_sofood` (con ID `CALENDARIO_{fornitore}`).
* **Origine dati**: caricati da `carica_logistica_v2.py` dal foglio "Calendario Ordini" del file logistico.
* **Cosa contengono**: regole per le merci fresche deperibili (giorno limite entro cui il cliente ristoratore deve piazzare l'ordine e giorno di consegna previsto settimanale per ciascun fornitore fresco).

### 4. `Programma Consegne e Zone` (10 record)
* **Collezione di origine**: `catalogo_sofood` (con ID `CONSEGNE_{zona}`).
* **Origine dati**: caricati da `carica_logistica_v2.py` dal foglio "Programma Consegne".
* **Cosa contengono**: suddivisione territoriale delle consegne dirette So Food con mezzi refrigerati (Bari città, provincia di Bari, BAT, Foggia, Taranto, Brindisi, Lecce, Matera, Potenza) e regole per le spedizioni sul resto del territorio nazionale.
