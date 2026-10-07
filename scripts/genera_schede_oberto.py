# -*- coding: utf-8 -*-
"""
Schede prodotto Oberto (fornitore 19010883) dal listino ufficiale (oberto.pdf: codici e nomi di sistema) e dal
catalogo carni So Food (sofood-catalogo-carni.pdf, pagine Oberto 56-92: descrizioni, conservazione, scadenze).

Uscita: <progetto>/19010883/<CODICE>/<CODICE>.TXT, stessa struttura delle schede gia' in PRODOTTI SOFOOD/19010883
(UTF-8 con BOM, campi PRODOTTO / FORMATO / DESCRIZIONE / CARATTERISTICHE / TEMPERATURA DI CONSERVAZIONE / SCADENZA /
PRODUTTORE). Le 8 schede gia' esistenti vengono copiate identiche. Nessun dato inventato: dove il catalogo non descrive
un codice si usano solo il nome ufficiale, la shelf life del listino e la presentazione Oberto del catalogo; questi codici
finiscono in 19010883/_DA_VERIFICARE.csv. Nessuna chiamata API; la cartella PRODOTTI SOFOOD non viene toccata.

    .venv\\Scripts\\python.exe scripts\\genera_schede_oberto.py <listino.json>
"""
import csv
import json
import os
import re
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USCITA = os.path.join(RADICE, "19010883")
ESISTENTI = os.path.join(os.getenv("DATA_LAKE_PATH", r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD"), "19010883")
PRODUTTORE = "Selezione Oberto S.r.l. - Via C. Cavallotto 30/36, 12060 Roddi (CN) - Italia"
# Presentazione Oberto (catalogo, pag. 56-57): vale per tutta la carne di Fassona Oberto
FASSONA = ("selezionata da Oberto, che nelle Langhe sceglie da oltre 50 anni femmine adulte di Razza Piemontese (Fassona) "
           "con eta' superiore ai 36 mesi")
SENZA_GLUTINE = "Naturalmente senza glutine (unico ingrediente: carne di Fassona)"

# ---------------------------------------------------------------- consigli d'uso del catalogo (testo del catalogo)
GRIGLIA = ("Alla griglia, in padella o in forno. Lasciare riposare brevemente le carni dopo la cottura per ottenere un "
           "risultato ottimale.")
GRIGLIA_ALTA = ("Alla griglia, su fry-top o in forni speciali per le cotture delle carni, a fuoco molto alto da ambo i lati "
                "e per pochissimi minuti. Lasciare riposare per breve tempo prima di consumare per ottenere un risultato "
                "ottimale.")
LENTA = "Cucinare lentamente e a lungo. Il tempo di cottura dipende dalla quantita' e dalla tecnica impiegata."
LESSARE = "Lessare lentamente e a lungo. Il tempo di cottura dipende dalla quantita' e dalla tecnica impiegata."
BRASATI = ("Cucinare lentamente e a lungo. Ideale per lessi, arrosti, brasati, stufati e bocconcini al Barolo. Il tempo "
           "di cottura dipende dalla quantita' e dalla tecnica impiegata.")
ROSA = "Adatto per arrosti, Roast Beef, involtini e scaloppine."
MONOPORZIONE_GRIGLIA = ("Taglio monoporzione pronto all'uso. Alla griglia, in padella o in forno per pochi minuti. "
                        "Lasciare riposare brevemente le carni dopo la cottura per ottenere un risultato ottimale.")
HAMBURGER = ("Monoporzione pronto all'uso. Cucinare alla griglia, alla piastra, al forno, in padella o al vapore a fuoco "
             "moderato.")
WURST = "Alla griglia o al vapore, nel panino o per arricchire pizze gourmet: il suo impiego e' poliedrico."

# ---------------------------------------------------------------- tagli del catalogo
# chiave -> (nome per PRODOTTO, origine del taglio, altra denominazione, consigli, temperatura, linea, senza glutine)
T = {
    "carpaccio": ("Carpaccio", "Ricavato da tagli pregiati di coscia", "Coscia fagottino",
                  "Servire crudo. Tagliare finemente e condire con olio extra vergine, sale e pepe. Ottimo con salsa al "
                  "gorgonzola o dressing a piacere.", "0 C / +4 C", "Selezione Crudo", True),
    "coscia_trita": ("Coscia Trita", "Ricavata da tagli pregiati di coscia", None,
                     "Servire cruda. Condire con olio extra vergine, sale e pepe a piacere. Ottima anche in preparazione "
                     "tartare, con spuma di caprino o in panino \"steak tartare\".", "0 C / +2 C", "Selezione Crudo", True),
    "coscia_battere": ("Coscia da Battere", "Ricavata da tagli pregiati di coscia", None,
                       "Battere al coltello e servire cruda. Condire con olio extra vergine, sale e pepe a piacere.",
                       "0 C / +4 C", "Selezione Crudo", True),
    "cruda_anteriore": ("Cruda di Anteriore da Battere", "Ricavata da tagli pregiati dell'anteriore", None,
                        "Battere al coltello e servire cruda. Condire con olio extra vergine, sale e pepe a piacere.",
                        "0 C / +4 C", "Selezione Crudo", True),
    "fesa": ("Fesa senza Copertina", "Taglio pregiato di coscia", None,
             "Adatto per cotture in rosa, Roast Beef, scaloppine, pizzaiola, tagliate e battuta al coltello.",
             "0 C / +4 C", "Selezione Cotture in Rosa", True),
    "girello": ("Girello (Magatello)", "Taglio magro pregiato di coscia", "Rotonda",
                "Ideale per la preparazione del vitello tonnato e del carpaccio.", "0 C / +4 C", "Selezione Cotture in Rosa", True),
    "noce_spalla": ("Noce di Spalla", "Taglio pregiato ricavato dal quarto anteriore", None, ROSA, "0 C / +4 C",
                    "Selezione Cotture in Rosa", True),
    "fesone": ("Fesone per Roast Beef", "Taglio pregiato e parato, ricavato dall'anteriore", None, ROSA, "0 C / +4 C",
               "Selezione Cotture in Rosa", True),
    "sottofesa": ("Sottofesa Squadrata", "Taglio pregiato di coscia", None,
                  "Adatto per Roast Beef, carpaccio e battuta al coltello.", "0 C / +4 C", "Selezione Cotture in Rosa", True),
    "coda": ("Coda Tagliata a Mano", "Tagliata a mano in pezzi selezionati", None, LENTA, "0 C / +3 C",
             "Selezione Brasati - Cotture al Forno - Lessi", True),
    "guancia": ("Guancia", "Ricavata dalla parte centrale della testa", None, LENTA, "0 C / +3 C",
                "Selezione Brasati - Cotture al Forno - Lessi", True),
    "reale": ("Reale", "Situato nella fascia muscolare del collo", "Tenerone", BRASATI, "0 C / +4 C",
              "Selezione Brasati - Cotture al Forno - Lessi", True),
    "sottopaletta": ("Sottopaletta", "Taglio pregiato del quarto anteriore",
                     "Arrosto della vena, cappello del prete, copertina di spalla, aletta", BRASATI, "0 C / +4 C",
                     "Selezione Brasati - Cotture al Forno - Lessi", True),
    "stinco": ("Stinco con Osso (Geretto)", "Ricavato dall'arto posteriore", None, LENTA, "0 C / +3 C",
               "Selezione Brasati - Cotture al Forno - Lessi", True),
    "spinacino": ("Spinacino", "Taglio ricavato dal posteriore", "Fiocco",
                  "Taglio magro ideale per Roast Beef, vitello tonnato e battuta al coltello.", "0 C / +4 C",
                  "Selezione Brasati - Cotture al Forno - Lessi", True),
    "brutto_buono": ("Brutto e Buono", "Ricavato dalla parte centrale del collo", None,
                     "Ideale per lessi, stracotti e bocconcini al Barolo.", "0 C / +4 C",
                     "Selezione Brasati - Cotture al Forno - Lessi", True),
    "ribs": ("Ribs di Bovino Adulto", "Porzione centrale di pancia con osso", "Scaramella, fracosta",
             "Cuocere sottovuoto a bassa temperatura. Per ottenere un risultato ottimale grigliare brevemente prima di "
             "servire.", "0 C / +4 C", "Selezione Brasati - Cotture al Forno - Lessi", True),
    "gallinella": ("Gallinella", "Taglio proveniente dalla coscia", None,
                   "Cucinare lentamente e a lungo. Ideale per lessi, arrosti, brasati e stufati. Il tempo di cottura "
                   "dipende dalla quantita' e dalla tecnica impiegata.", "0 C / +4 C",
                   "Selezione Brasati - Cotture al Forno - Lessi", True),
    "spezzatino": ("Spezzatino Cubettato", "Ricavato da tagli nobili dell'anteriore", None, "Ideale per cotture in umido.",
                   "0 C / +4 C", "Selezione Brasati - Cotture al Forno - Lessi", True),
    "pancia": ("Pancia senza Osso", "Taglio di pancia", None, LESSARE, "0 C / +4 C",
               "Selezione Brasati - Cotture al Forno - Lessi", True),
    "punta": ("Punta senza Osso", "Taglio ricavato dalla punta di petto", None, LESSARE, "0 C / +4 C",
              "Selezione Brasati - Cotture al Forno - Lessi", True),
    "muscolo": ("Muscolo senza Osso", "Ricavato dall'arto posteriore", None, LESSARE, "0 C / +4 C",
                "Selezione Brasati - Cotture al Forno - Lessi", True),
    "lingua": ("Lingua", "Lingua di Fassona", None, LESSARE, "0 C / +3 C", "Selezione Brasati - Cotture al Forno - Lessi", True),
    "lombata8": ("Lombata Intera 8 Coste con Osso e Filetto", "Taglio pregiato ricavato dal lombo, a partire dal filetto "
                 "sino all'ottava costa", None, "Da questo taglio si ricavano costate e fiorentine. " + GRIGLIA,
                 "0 C / +4 C", "Griglia Selezione", True),
    "lombata3": ("Lombata Intera 3 Coste con Osso e Filetto", "Taglio pregiato ricavato dal lombo, a partire dal filetto "
                 "sino alla terza costa", None, "Da questo taglio si ricavano circa 3 kg di costate e la parte rimanente "
                 "in fiorentine. " + GRIGLIA, "0 C / +4 C", "Griglia Selezione", True),
    "costata": ("Costata", "Taglio pregiato ricavato dal lombo, nella sezione delle sole otto coste", None,
                "Da questo taglio si ricavano le costate. " + GRIGLIA, "0 C / +4 C", "Griglia Selezione", True),
    "fiorentina": ("Fiorentina", "Taglio pregiato ricavato dal lombo, nella sezione con filetto", None,
                   "Da questo taglio si ricavano le fiorentine. " + GRIGLIA, "0 C / +4 C", "Griglia Selezione", True),
    "tomahawk": ("Tomahawk", "Taglio con osso scalzato, ricavato dalle tre coste che precedono la lombata", None, GRIGLIA,
                 "0 C / +4 C", "Griglia Selezione", True),
    "cowboy": ("Cowboy Steak", "Taglio con osso scalzato, ricavato dalla costata", None, GRIGLIA, "0 C / +4 C",
               "Griglia Selezione", True),
    "cube_roll": ("Cube Roll", "Taglio molto pregiato ricavato dalla lombata senza osso (parte alta)", None, GRIGLIA,
                  "0 C / +4 C", "Griglia Selezione", True),
    "rib_eye": ("Rib Eye", "Taglio molto pregiato ricavato dalla lombata senza osso (parte alta) senza la copertina", None,
                GRIGLIA, "0 C / +4 C", "Griglia Selezione", True),
    "controfiletto": ("Controfiletto", "Taglio molto pregiato ricavato dalla lombata senza osso (parte bassa)", None,
                      GRIGLIA, "0 C / +4 C", "Griglia Selezione", True),
    "filetto": ("Filetto con Cordone", "Il taglio piu' pregiato e tenero del bovino", None, GRIGLIA, "0 C / +4 C",
                "Griglia Selezione", True),
    "scamone": ("Scamone", "Taglio molto pregiato ricavato dal posteriore", None,
                "Taglio molto versatile: adatto per crudi, cotture alla griglia, in padella o al forno. Lasciare riposare "
                "brevemente le carni dopo la cottura per ottenere un risultato ottimale.", "0 C / +4 C", "Griglia Selezione", True),
    "noce": ("Noce", "Taglio pregiato di coscia", None,
             "Taglio molto versatile: adatto per cotture in rosa, crudi e alla griglia. Ottimo anche per paillard e "
             "bistecche impanate.", "0 C / +4 C", "Griglia Selezione", True),
    "filetto_noce": ("Filetto di Noce", "Taglio ricavato dal posteriore", None, GRIGLIA_ALTA, "0 C / +4 C",
                     "Griglia Selezione", True),
    "picanha": ("Picanha", "Taglio ricavato dalla coscia", None, GRIGLIA, "0 C / +4 C", "Griglia Selezione", True),
    "codone": ("Codone", "Taglio molto pregiato ricavato dalla coscia", None, GRIGLIA, "0 C / +4 C", "Griglia Selezione", True),
    "tagliata": ("Tagliata di Coscia", "Ricavata da tagli pregiati di coscia", None, MONOPORZIONE_GRIGLIA, "0 C / +4 C",
                 "Griglia Selezione", True),
    "filetto_anteriore": ("Filetto di Anteriore", "Taglio pregiato dell'anteriore, ricavato dalla parte centrale della "
                          "spalla", None, GRIGLIA_ALTA, "0 C / +4 C", "Tagli Speciali per Griglia", True),
    "chuck": ("Chuck Steak (Cuore di Reale)", "Taglio dell'anteriore ricavato dalla parte centrale del reale",
              "Cuore di reale, punta di reale", GRIGLIA_ALTA, "0 C / +4 C", "Tagli Speciali per Griglia", True),
    "flap": ("Flap Steak (Bavetta)", "Taglio ricavato dal posteriore", "Bavetta", GRIGLIA_ALTA, "0 C / +4 C",
             "Tagli Speciali per Griglia", True),
    "hanger": ("Hanger Steak (Lombetto)", "Taglio ricavato dal posteriore (lombetto)", None, GRIGLIA_ALTA, "0 C / +4 C",
               "Tagli Speciali per Griglia", True),
    "skirt": ("Skirt Steak (Diaframma)", "Taglio ricavato dal diaframma", None, GRIGLIA_ALTA, "0 C / +4 C",
              "Tagli Speciali per Griglia", True),
    "salsiccia": ("Salsiccia di Fassona", "Realizzata con tagli pregiati di Fassona e spezie", None,
                  "Alla griglia o per arricchire ripieni, condimenti di risotti e primi piatti.", "0 C / +2 C",
                  "Tagli Speciali per Griglia", False),
    "hamburger": ("Hamburger Tradizionale", "Ricavato da tagli accuratamente selezionati", None, HAMBURGER, "0 C / +2 C",
                  "Griglia Selezione", True),
    "macinato": ("Macinato per Hamburger e Ragu'", "Carne macinata di Fassona", None,
                 "Adatto per la preparazione di hamburger, sughi e ripieni.", "0 C / +2 C", "Griglia Selezione", True),
    "agnello": ("Mezzena di Agnello della Valle Stura", "Agnello piemontese proveniente dalle valli piemontesi, luoghi "
                "particolarmente piovosi e ricchi di pascoli dove gli agnelli possono liberamente approvvigionarsi: il "
                "pascolo garantisce il giusto equilibrio dei grassi nelle carni, eleganti e dal sapore importante ma mai "
                "invadente", None, "Cucinare lentamente al forno oppure in umido, arricchito con erbe aromatiche e in base "
                "alla ricetta che piu' vi piace.", "0 C / +4 C", "L'Agnello Piemontese", False),
    "costata_pronta": ("Costata Pronta per la Griglia", "Taglio monoporzione con osso, ricavato dal lombo nella sezione "
                       "delle sole 8 coste", None, GRIGLIA, "0 C / +4 C", "Pronti per la Griglia", True),
    "fiorentina_pronta": ("Fiorentina Pronta per la Griglia", "Taglio monoporzione con osso, ricavato dal lombo nella "
                          "sezione con filetto", None, GRIGLIA, "0 C / +4 C", "Pronti per la Griglia", True),
    "tomahawk_pronto": ("Tomahawk Pronto per la Griglia", "Taglio monoporzione con osso, ricavato dalle 3 coste che "
                        "precedono la lombata", None, GRIGLIA, "0 C / +4 C", "Pronti per la Griglia", True),
    "wurstel": ("Wurstel di Fassona", "Realizzato con tagli freschi e cotti al vapore. Il formato piu' piccolo e con "
                "ricetta delicata, si adatta ai gusti di grandi e piccini", None, WURST, "0 C / +4 C", "Wurstel di Fassona", True),
    "kasewurst": ("Kasewurst di Fassona", "Realizzato con tagli freschi e cotti al vapore, insaccati in budello naturale "
                  "croccante. Arricchito da spezie e formaggio nazionale", None, WURST, "0 C / +4 C", "Wurstel di Fassona", True),
    "gran_cruda": ("Gran Cruda - Trionfo di Tartare di Fassona", "Pregiati tagli magri di coscia di Fassona piemontese",
                   None, "Servire il prodotto crudo dopo averlo lasciato a temperatura ambiente per alcuni minuti e condire "
                   "a piacere. Suggeriamo di condire con olio extravergine di oliva, sale e pepe.", "0 C / +4 C",
                   "Salumi di Fassona", True),
    "polpa_imperiale": ("Polpa Imperiale - Hamburger di Fassona", "Hamburger gourmet di Fassona piemontese, da tagli "
                        "selezionati con il giusto equilibrio di magro e grasso", None, "Cucinare alla griglia, alla "
                        "piastra, in padella o al vapore a fuoco moderato. Arricchire con formaggi fondenti o salse a piacere.",
                        "0 C / +4 C", "Salumi di Fassona", True),
    "polpa_imperiale_bra": ("Polpa Imperiale - Hamburger di Fassona al Bra Duro DOP", "Hamburger gourmet di Fassona "
                            "piemontese, da tagli selezionati con il giusto equilibrio di magro e grasso, con Bra Duro DOP",
                            None, "Cucinare alla griglia, alla piastra, in padella o al vapore a fuoco moderato. Arricchire "
                            "con formaggi fondenti o salse a piacere.", "0 C / +4 C", "Salumi di Fassona", False),
}

# codice (numero del catalogo) -> (taglio, formato dal catalogo, scadenza dal catalogo)
C = {
    "1056": ("carpaccio", "1,5 kg", "30 giorni"), "1054": ("carpaccio", "3 kg", "30 giorni"),
    "1317": ("coscia_trita", "Monoporzione da 120 g - Confezione da 10 pezzi", "15 giorni"),
    "1319": ("coscia_trita", "Monoporzione da 160 g - Confezione da 10 pezzi", "15 giorni"),
    "1092": ("coscia_trita", "1 kg", "15 giorni"),
    "1324": ("coscia_battere", "Monoporzione da 120 g - Confezione da 10 pezzi", "20 giorni"),
    "1078": ("coscia_battere", "1 / 1,3 kg", "30 giorni"),
    "1204": ("cruda_anteriore", "1 / 1,3 kg", "30 giorni"),
    "1095": ("fesa", "8 / 10 kg", "30 giorni"), "1126": ("girello", "3 / 5 kg", "30 giorni"),
    "1183": ("noce_spalla", "4 / 5,5 kg", "30 giorni"), "1185": ("fesone", "2,5 / 4,5 kg", "30 giorni"),
    "1229": ("sottofesa", "2,5 / 4,5 kg", "30 giorni"), "1061": ("coda", "2 / 2,5 kg", "15 giorni"),
    "1139": ("guancia", "2 / 2,5 kg (2 pezzi)", "15 giorni"), "1214": ("reale", "6 / 8 kg", "30 giorni"),
    "1224": ("sottopaletta", "1,5 / 2,5 kg", "30 giorni"), "1236": ("stinco", "3 / 5 kg", "30 giorni"),
    "1190": ("stinco", "Stinco intero porzionato a ossibuchi da circa 300 / 400 g (10 pezzi)", "30 giorni"),
    "1234": ("spinacino", "1 / 2 kg", "30 giorni"), "1012": ("brutto_buono", "2 / 4 kg", "30 giorni"),
    "1218": ("ribs", "3 / 4 kg", "30 giorni"), "1122": ("gallinella", "3 / 4 kg", "30 giorni"),
    "1232": ("spezzatino", "1 kg", "20 giorni"), "1195": ("pancia", "4 / 6 kg", "30 giorni"),
    "1212": ("punta", "4 / 6 kg", "30 giorni"), "1177": ("muscolo", "1,5 / 3,5 kg", "30 giorni"),
    "1144": ("lingua", "1,5 / 2,5 kg", "15 giorni"),
    "1150": ("lombata8", "22 / 27 kg (diviso in due pezzi)", "30 giorni"),
    "1154": ("lombata8", "22 / 27 kg - porzionata: peso di costate e fiorentine a richiesta", "20 giorni"),
    "1148": ("lombata3", "16 / 19 kg", "30 giorni"),
    "1159": ("lombata3", "16 / 19 kg - porzionata: peso di costate e fiorentine a richiesta", "20 giorni"),
    "1072": ("costata", "Costata intera 11 / 14 kg - Confezione da 1 pezzo", "30 giorni"),
    "1125": ("costata", "Costata singola 500-600 g - Confezione da 15 pezzi", "20 giorni"),
    "1163": ("costata", "Costata singola 1 kg - Confezione da 10 pezzi", "20 giorni"),
    "1084": ("costata", "Costata singola, peso a richiesta", "20 giorni"),
    "1008": ("fiorentina", "Fiorentina intera 11 / 15 kg - Confezione da 1 pezzo", "30 giorni"),
    "1081": ("fiorentina", "Fiorentina singola 1,2 kg - Confezione da 8 pezzi", "20 giorni"),
    "1083": ("fiorentina", "Fiorentina singola 1,5 kg - Confezione da 5 pezzi", "20 giorni"),
    "1010": ("fiorentina", "Fiorentina singola, peso a richiesta", "20 giorni"),
    "1275": ("tomahawk", "Tomahawk 3 coste, 3 / 4 kg", "20 giorni"),
    "1090": ("tomahawk", "Tomahawk singolo, 900 g / 1,1 kg", "20 giorni"),
    "1372": ("cowboy", "Cowboy steak intera, 6,5 kg", "30 giorni"),
    "1373": ("cowboy", "Cowboy steak singola, 1 kg", "20 giorni"),
    "1165": ("cube_roll", "3 / 4 kg", "30 giorni"), "1346": ("rib_eye", "2,8 / 3,5 kg", "30 giorni"),
    "1064": ("controfiletto", "8 / 10 kg", "30 giorni"), "1099": ("filetto", "2,8 / 4 kg", "30 giorni"),
    "1220": ("scamone", "4 / 5 kg", "30 giorni"), "1180": ("noce", "4,5 / 6 kg", "30 giorni"),
    "1107": ("filetto_noce", "1,7 kg (2 pezzi da 700 / 900 g)", "30 giorni"),
    "1200": ("picanha", "2,5 / 3 kg", "30 giorni"), "1202": ("codone", "1 / 1,5 kg", "30 giorni"),
    "1315": ("tagliata", "Monoporzione da 200 g - Confezione da 10 pezzi", "20 giorni"),
    "1246": ("tagliata", "Monoporzione da 300 g - Confezione da 10 pezzi", "20 giorni"),
    "1155": ("tagliata", "Monoporzione da 500 g", "20 giorni"), "1192": ("tagliata", "1 kg", "20 giorni"),
    "1252": ("tagliata", "2 kg", "20 giorni"),
    "1104": ("filetto_anteriore", "1,4 kg (2 pezzi da circa 600-800 g)", "30 giorni"),
    "1215": ("chuck", "3 / 4 kg", "30 giorni"), "1241": ("flap", "1 / 1,5 kg", "20 giorni"),
    "1162": ("hanger", "4-500 g (3 pezzi da 160 g)", "20 giorni"),
    "1287": ("skirt", "1,1 kg (2 pezzi da 550 g)", "20 giorni"),
    "6007": ("salsiccia", "1 kg", "15 giorni"),
    "6003": ("hamburger", "2 pezzi da 150 g - Confezione da 12 pezzi (24 hamburger)", "15 giorni"),
    "6004": ("hamburger", "2 pezzi da 200 g - Confezione da 12 pezzi (24 hamburger)", "15 giorni"),
    "6008": ("macinato", "2 kg", "15 giorni"), "2002": ("agnello", "5 - 6 kg", "30 giorni"),
    "1117": ("costata_pronta", "Monoporzione da 500 g", "20 giorni"),
    "1118": ("fiorentina_pronta", "Monoporzione da 1 kg", "20 giorni"),
    "1489": ("tomahawk_pronto", "Monoporzione da 1 kg", "20 giorni"),
    "4046": ("wurstel", "3 pezzi x 80 g", "60 giorni"), "4049": ("kasewurst", "3 pezzi x 100 g", "60 giorni"),
    "6035": ("gran_cruda", "150 g", "15 giorni"), "6026": ("polpa_imperiale", "180 g", "20 giorni"),
    "6032": ("polpa_imperiale_bra", "180 g", "20 giorni"),
}
# stesso taglio con un altro codice (porzionato, pezzi, altra pezzatura): descrizione del taglio, formato dal listino
STESSO_TAGLIO = {
    "1026": "girello", "1074": "costata", "1325": "costata", "1326": "costata", "1111": "fiorentina", "1327": "fiorentina",
    "1070": "coscia_trita", "1087": "coscia_trita", "1115": "coscia_trita", "1088": "coscia_battere",
    "1119": "tagliata", "1245": "tagliata", "1339": "tagliata", "1165-3PEZZI": "cube_roll", "1180-3PEZZI": "noce",
    "1214-3PEZZI": "reale", "1215-3PEZZI": "chuck", "1224-3PEZZI": "sottopaletta", "6013": "hamburger",
}

ABBREVIAZIONI = [(r"\bMONOP\.?(?![A-Z])", "Monoporzione"), (r"\bMON\.", "Monoporzione"), (r"\bC/O\b", "con Osso"),
                 (r"\bS/OSSO\b", "senza Osso"), (r"\bS/V\b", "Sottovuoto"), (r"\bINT\.?(?=\s)", "Intera"),
                 (r"\bPORZ\.?(?=\s|$)", "Porzionata"), (r"\bFIL\.", "Filetto "), (r"\bANT\.", "Anteriore"),
                 (r"\bTRADIZ\.", "Tradizionale"), (r"\bVASCHET\.", "in Vaschetta"), (r"\bFASS\.", "Fassona"),
                 (r"\bBATT\b", "da Battere"), (r"\bCT\b", "Coste"), (r"\bC/C\b", "con Cordone"), (r"\bHAMB\.", "Hamburger")]
_MINUSCOLE = {"di", "da", "del", "della", "con", "senza", "per", "e", "in", "al", "alla", "a"}


# refusi del listino ufficiale (segnalati anche in _DA_VERIFICARE.csv)
REFUSI = {"AFFUMICATOFASSONA": "AFFUMICATO FASSONA", "SFILACCII": "SFILACCI"}


def pulisci_nome(descr: str) -> str:
    for a, b in REFUSI.items():
        descr = descr.replace(a, b)
    d = re.sub(r"^(FASSONA|OBERTO)\s*-\s*", "", descr.strip())
    d = re.sub(r"^FASSONA-\s*", "", d)
    gelo = bool(re.match(r"GELO\s", d, re.IGNORECASE))
    d = re.sub(r"^GELO\s+", "", d, flags=re.IGNORECASE)
    d = re.sub(r"(\d+)\s*CT\b", r"\1§Coste", d, flags=re.IGNORECASE)  # "8CT" -> "8 Coste" (si tiene nel nome)
    for a, b in ABBREVIAZIONI:
        d = re.sub(a, b, d, flags=re.IGNORECASE)
    # pesi e confezioni vanno nel FORMATO, non nel nome; i numeri senza unita' restano ("Salame di Manza 36 Riserva")
    tok = re.sub(r"[()]", " ", d).split()
    tenuti, salta = [], False
    for i, t in enumerate(tok):
        if salta:
            salta = False
            continue
        succ = tok[i + 1].upper() if i + 1 < len(tok) else ""
        if "§" in t:
            tenuti.append(t.replace("§", " "))
        elif re.fullmatch(r"[\d.,/+]+", t) and re.match(r"(KG|GR|G|PZ|PEZZI)", succ):
            salta = True
        elif re.search(r"\d", t) and re.search(r"(KG|GR|G|PZ|X)", t.upper()) or re.fullmatch(r"[\d.,/+-]+", t) and "/" in t:
            continue
        elif t.upper() in ("PEZZI", "PZ", "KG", "-") or (i == len(tok) - 1 and re.fullmatch(r"[0-9]+[.,][0-9]+", t)):
            continue
        else:
            tenuti.append(t)
    d = " ".join(tenuti)
    # "Tagliata Fassona" -> il prefisso "Fassona" lo mette chi compone il nome: niente doppioni
    d = re.sub(r"\b(Fassona|FASSONA)\b(?=.*\bFassona\b)", "", d, flags=re.IGNORECASE)
    d = re.sub(r"\s*[-/]\s*$", "", re.sub(r"\s+", " ", d)).strip(" -")
    if gelo:
        d += " (surgelato)"
    parole = []
    for i, w in enumerate(d.split()):
        lw = w.lower()
        parole.append(lw if i and lw in _MINUSCOLE else (w if w in ("DOP", "ATM") else w.capitalize()))
    nome = re.sub(r"\s*-\s*", " - ", " ".join(parole))
    nome = re.sub(r"(?<=\s)D'([a-z])", lambda m: "d'" + m.group(1).upper(), nome)  # "Crudo d'Alba", "Lardo d'Arnad"
    nome = re.sub(r" - ([a-z])", lambda m: " - " + m.group(1).upper(), nome)
    return re.sub(r"/([a-z])", lambda m: "/" + m.group(1).upper(), nome)  # "Fegato/Tartufo"


def formato_da_listino(descr: str, um: str) -> str:
    d = descr.upper()
    m = re.search(r"(\d+)\s*(?:GR|G)\s*\((\d+)\s*PZ\s*-\s*([\d.,]+)\s*KG\)", d)  # "160GR (20PZ-3.2KG)"
    if m:
        return f"{m.group(1)} g - Confezione da {m.group(2)} pezzi ({m.group(3).replace('.', ',')} kg)"
    m = re.search(r"(\d+)/(\d+)\s*(G|GR)\b", d)
    if m:
        return f"{m.group(1)}-{m.group(2)} g"
    m = re.search(r"(\d+)\s*(?:GR|G)X(\d+)-(\d+)PZ", d)
    if m:
        return f"{m.group(2)} pezzi da {m.group(1)} g - Confezione da {m.group(3)} pezzi"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(G|GR)\s*[- ]\s*(\d+)\s*PZ", d)
    if m:
        return f"{m.group(1)} g - Confezione da {m.group(3)} pezzi"
    m = re.search(r"(\d+)\s*X\s*(\d+)\s*(G|GR)\b", d) or re.search(r"(\d+)\s*(?:GR|G)\s*X\s*(\d+)", d)
    if m and "X" in m.group(0) and re.match(r"\d+\s*X", m.group(0)):
        return f"{m.group(1)} pezzi da {m.group(2)} g"
    m = re.search(r"(\d+)\s*(?:GR|G)X(\d+)-(\d+)PZ", d)
    if m:
        return f"{m.group(2)} pezzi da {m.group(1)} g - Confezione da {m.group(3)} pezzi"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*KG\s*X\s*(\d+)", d)
    if m:
        return f"{m.group(2)} pezzi da {m.group(1)} kg"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*KG\s*-\s*(\d+)\s*PZ", d)
    if m:
        return f"{m.group(1)} kg - Confezione da {m.group(2)} pezzi"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*KG\+", d)
    if m:
        return f"Oltre {m.group(1).replace('.', ',')} kg, peso variabile"
    m = re.search(r"(\d+)\s*PEZZI|\((\d+)\s*PZ\)", d)
    pezzi = f" ({m.group(1) or m.group(2)} pezzi)" if m else ""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*KG\b", d)
    if m:
        return f"{m.group(1).replace('.', ',')} kg{pezzi}" + (", peso variabile" if um == "KG" else "")
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:GR|G)\b", d)
    if m and not re.fullmatch(r"\d+[.,]\d+", m.group(1)):  # "FLATIRON 1.3G": refuso del listino, non si interpreta
        return f"{m.group(1)} g{pezzi}"
    m = re.search(r"\b(\d+[.,]\d+)\s*$", d)  # "BRESAOLA FASSONA META' 1,5" (unita' di misura KG)
    if m and um == "KG":
        return f"{m.group(1).replace('.', ',')} kg{pezzi}"
    return ("Peso variabile" if um == "KG" else "A pezzo") + pezzi


def stato_da_nome(descr: str) -> list:
    """Fatti scritti nel nome ufficiale (non dedotti): surgelato, sottovuoto, ATM, affumicato..."""
    d = descr.upper()
    out = []
    if d.startswith("GELO "):
        out.append("prodotto surgelato")
    if "S/V" in d:
        out.append("confezionato sottovuoto")
    if "ATM" in d:
        out.append("in vaschetta in atmosfera protettiva")
    if "AFFUMICAT" in d:
        out.append("affumicato")
    if "SKIN" in d:
        out.append("confezionato in skin pack")
    if "MARINAT" in d:
        out.append("marinato")
    return out


def scheda(codice: str, item: dict, esistente: "str | None") -> tuple:
    """(testo, fonte) per un codice OBE."""
    num = codice[3:].lstrip("0") if codice.startswith("OBE") else codice
    num = num if num in C or num in STESSO_TAGLIO else num.split("-")[0]
    descr, um = item["descr"], item.get("um")
    nome_listino = pulisci_nome(descr)
    gelo = descr.upper().startswith("GELO ")
    if num in C or num in STESSO_TAGLIO:
        chiave = C[num][0] if num in C else STESSO_TAGLIO[num]
        nome_t, origine, altra, consigli, temp, linea, sg = T[chiave]
        formato = C[num][1] if num in C else formato_da_listino(descr, um)
        scadenza = C[num][2] if num in C else (f"{item['shelf']} giorni" if item.get("shelf") else None)
        stato = stato_da_nome(descr)
        prima = origine if chiave == "agnello" else (
            f"{origine}. Carne di Fassona piemontese, {FASSONA}" if "assona" not in origine
            else f"{origine}. Carne {FASSONA}")
        testo_d = (prima.rstrip(".") + "." + (f" Altra denominazione: {altra}." if altra else "")
                   + (f" Linea \"{linea}\"." if linea else "") + (" " + ", ".join(stato).capitalize() + "." if stato else "")
                   + " " + consigli)
        if gelo:
            temp = None  # la temperatura del catalogo e' quella del fresco
        nome = nome_listino if num not in C else nome_listino or nome_t
        fonte = "catalogo" if num in C else "catalogo (stesso taglio, altro codice)"
        car = SENZA_GLUTINE if sg and chiave not in ("salsiccia", "wurstel", "kasewurst", "polpa_imperiale", "gran_cruda",
                                                     "polpa_imperiale_bra", "hamburger") else (
            "Senza glutine" if chiave in ("wurstel", "kasewurst", "gran_cruda", "polpa_imperiale", "polpa_imperiale_bra") else None)
    else:
        stato = stato_da_nome(descr)
        nome = nome_listino
        testo_d = (f"{nome}: prodotto a base di carne di Fassona piemontese, {FASSONA}" if "FASS" in descr.upper()
                   else nome).rstrip(".") + "." + (""
                   ) + (" " + ", ".join(stato).capitalize() + "." if stato else "")
        formato = formato_da_listino(descr, um)
        scadenza = f"{item['shelf']} giorni" if item.get("shelf") else None
        temp, car = None, None
        fonte = "solo listino"
    righe = [f"PRODOTTO: Oberto - {'Fassona ' if 'FASS' in descr.upper() and 'fassona' not in nome.lower() else ''}{nome}",
             f"FORMATO: {formato}", f"DESCRIZIONE: {testo_d}"]
    if car:
        righe.append(f"CARATTERISTICHE: {car}")
    if temp:
        righe.append(f"TEMPERATURA DI CONSERVAZIONE: {temp}")
    if scadenza:
        righe.append(f"SCADENZA: {scadenza}")
    righe.append(f"PRODUTTORE: {PRODUTTORE}")
    return "\n".join(righe) + "\n", fonte


def main():
    items = json.load(open(sys.argv[1], encoding="utf-8"))
    os.makedirs(USCITA, exist_ok=True)
    report = []
    for it in items:
        cod = it["codice"]
        cartella = os.path.join(USCITA, cod)
        os.makedirs(cartella, exist_ok=True)
        orig = os.path.join(ESISTENTI, cod, f"{cod}.TXT")
        if os.path.exists(orig):
            testo = re.sub(r"^PRODUTTORE:.*$", f"PRODUTTORE: {PRODUTTORE}", open(orig, encoding="utf-8-sig").read(), flags=re.M)
            with open(os.path.join(cartella, f"{cod}.TXT"), "w", encoding="utf-8-sig", newline="\n") as f:
                f.write(testo)
            report.append({"codice": cod, "nome_listino": it["descr"], "fonte": "scheda gia' esistente, copiata identica"})
            continue
        # stesso prodotto gia' schedato con un codice parente (OBE04047 = OBE4047, OBE4014-10 = OBE4014 da 10 pezzi):
        # si riusa la scheda e si aggiorna solo il formato
        base = "OBE" + cod[3:].split("-")[0].lstrip("0")
        orig = os.path.join(ESISTENTI, base, f"{base}.TXT")
        if os.path.exists(orig):
            testo = open(orig, encoding="utf-8-sig").read()
            testo = re.sub(r"^FORMATO:.*$", "FORMATO: " + formato_da_listino(it["descr"], it.get("um")), testo, count=1, flags=re.M)
            with open(os.path.join(cartella, f"{cod}.TXT"), "w", encoding="utf-8-sig", newline="\n") as f:
                f.write(testo)
            report.append({"codice": cod, "nome_listino": it["descr"], "fonte": f"scheda esistente {base}, formato dal listino"})
            continue
        testo, fonte = scheda(cod, it, None)
        with open(os.path.join(cartella, f"{cod}.TXT"), "w", encoding="utf-8-sig", newline="\n") as f:
            f.write(testo)
        report.append({"codice": cod, "nome_listino": it["descr"],
                       "fonte": fonte if cod.startswith("OBE") else fonte + " (codice ufficiale non OBE del fornitore 19010883)"})
    with open(os.path.join(USCITA, "_DA_VERIFICARE.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["codice", "nome_listino", "fonte"], delimiter=";")
        w.writeheader()
        w.writerows(report)
    from collections import Counter
    print(Counter(r["fonte"] for r in report))


if __name__ == "__main__":
    main()
