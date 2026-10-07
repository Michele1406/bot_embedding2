# -*- coding: utf-8 -*-
"""
Aggiorna le anagrafiche in PRODOTTI SOFOOD (core/percorsi.py) con i prodotti di una nuova cartella fornitore (es. 19010883 Oberto, schede generate
da scripts/genera_schede_oberto.py): Tassonomia.xlsx, riassunto_prodotti.xlsx (+ arricchito_spezie), varianti_prodotto.csv.

Regole: si AGGIUNGONO solo i codici che mancano (le righe esistenti non si toccano); la classificazione segue le
convenzioni gia' presenti in Tassonomia (Carne > SCOTTONA > COSTATA/LOMBATA/ANTERIORE..., III LAVORAZIONE > HAMBURGER /
MACINATO / ALTRI ELABORATI CRUDI, Salumi > SALUMI INTERI/TRANCI > BRESAOLA...). Attributi (glutine, lattosio, allergeni)
solo da cio' che c'e' nella scheda: "SI" se dichiarato, "SI*" dedotto per i tagli anatomici, "?" se gli ingredienti
mancano (legenda di riassunto_prodotti.xlsx). Nessuna chiamata API.

    .venv\\Scripts\\python.exe scripts\\aggiorna_anagrafiche_nuovi_prodotti.py 19010883 OBERTO
"""
import csv
import os
import re
import sys
from collections import defaultdict

import openpyxl

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
from core import percorsi  # noqa: E402

# (parole nel nome, reparto, categoria, sottocategoria, livello 4) - la prima che combacia vince
REGOLE = [
    (("gelo", "surgelat"), "Gelo", "SURGELATI", "SURG CARNE", "ND"),
    (("bresaola",), "Salumi", "SALUMI INTERI/TRANCI", "BRESAOLA", "ND"),
    (("salame",), "Salumi", "SALUMI", "SALAME", "ND"),
    (("coppa",), "Salumi", "SALUMI", "SALUMI INTERI/TRANCI", "ND"),
    (("lardo",), "Salumi", "SALUMI", "SALUMI INTERI/TRANCI", "ND"),
    (("coscia stagionata", "carpaccio marinato", "carpaccio affumicato"), "Salumi", "SALUMI", "SALUMI INTERI/TRANCI", "ND"),
    (("wurstel", "bockwurst", "kasewurst", "bratwurst"), "Salumi", "SALUMI", "ALTRI", "ND"),
    (("pate'", "aspic"), "Salumi", "SALUMI", "ALTRI", "ND"),
    (("roast beef cotto", "girello cotto"), "Carne", "BOVINO ADULTO", "COSCIA", "ND"),
    (("hamburger", "burger", "polpa imperiale"), "Carne", "III LAVORAZIONE", "HAMBURGER", "HAMBURGER"),
    (("trita", "macinato", "tartare", "gran cruda", "crudo d'alba", "da battere", "tartara"), "Carne", "III LAVORAZIONE",
     "MACINATO", "MACINATO"),
    (("salsiccia", "polpett", "sfilacc"), "Carne", "III LAVORAZIONE", "ALTRI ELABORATI CRUDI", "ND"),
    (("agnello",), "Carne", "OVINO", "AGNELLO", "ND"),
    (("suino",), "Carne", "SUINO", "PANCIA", "ND"),
    (("costata", "fiorentina", "tomahawk", "cowboy", "bistecca"), "Carne", "SCOTTONA", "COSTATA", "COSTATA"),
    (("lombata", "cube roll", "rib eye", "controfiletto", "sottofiletto", "filetto con cordone", "filetto c/c"), "Carne",
     "SCOTTONA", "LOMBATA", "LOMBATA"),
    (("anteriore", "spalla", "fesone", "reale", "sottopaletta", "chuck", "brutto e buono", "spezzatino", "punta",
      "pancia", "ribs", "biancostato", "shoulder", "flatiron", "lesso"), "Carne", "SCOTTONA", "ANTERIORE", "ANTERIORE"),
    (("fesa", "sottofesa", "girello", "noce", "scamone", "picanha", "codone", "spinacino", "gallinella", "muscolo",
      "stinco", "tagliata", "carpaccio", "fettine", "bavetta", "tasca"), "Carne", "SCOTTONA", "COSCIA", "ND"),
    (("lingua", "trippa", "testa", "coda", "guancia", "diaframma", "lombetto"), "Carne", "SCOTTONA", "ALTRI", "ND"),
]
TAGLI_ANATOMICI = ("Carne",)  # SI* su glutine/lattosio solo per la carne fresca non elaborata
ELABORATI = ("hamburger", "burger", "salsiccia", "polpett", "sfilacc", "wurst", "pate'", "aspic", "salame", "bresaola",
             "coppa", "marinato", "affumicato", "stagionata", "cotto", "lardo", "impasto", "polpa imperiale")


def leggi_scheda(percorso: str) -> dict:
    campi = {}
    for riga in open(percorso, encoding="utf-8-sig"):
        m = re.match(r"^([A-Z' /]+):\s*(.*)$", riga.rstrip("\n"))
        if m:
            campi[m.group(1).strip()] = m.group(2).strip()
    return campi


def classifica(nome: str) -> tuple:
    n = nome.lower()
    for parole, rep, cat, sub, l4 in REGOLE:
        if any(p in n for p in parole):
            return rep, cat, sub, l4
    return "Carne", "SCOTTONA", "ALTRI", "ND"


def attributi(nome: str, campi: dict, reparto: str) -> dict:
    n, car, ingr = nome.lower(), campi.get("CARATTERISTICHE", "").lower(), campi.get("INGREDIENTI", "")
    formaggio = any(k in n for k in ("kasewurst", "bra duro", "formagg"))
    elaborato = any(k in n for k in ELABORATI) or reparto != "Carne"
    if "senza glutine" in car:
        glutine = "SI"
    elif ingr:
        glutine = "SI*" if not re.search(r"glutin|frumento|farina|pane", ingr, re.I) else "NO"
    else:
        glutine = "?" if elaborato else "SI*"
    if formaggio:
        lattosio, milk, allergeni = "NO", "NO", "Latte"
    elif ingr or not elaborato:
        lattosio, milk, allergeni = "NO", "SI*", "Nessuno rilevato"  # legenda: SENZA LATTOSIO = SI solo se dichiarato
    else:
        lattosio, milk, allergeni = "?", "?", "?"
    return {"BIOLOGICO": "NO", "VEGANO": "NO", "VEGETARIANO": "NO", "SENZA GLUTINE": glutine, "SENZA LATTOSIO": lattosio,
            "MILK FREE": milk, "KOSHER": "NO", "ALLERGENI": allergeni, "TRACCE DI": None}


def main():
    cod_forn, nome_forn = sys.argv[1], sys.argv[2]
    cartella = os.path.join(RADICE, cod_forn)
    if not os.path.isdir(cartella):  # gia' spostata nella cartella prodotti (si legge soltanto)
        cartella = os.path.join(os.getenv("DATA_LAKE_PATH", r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD"), cod_forn)
    prodotti = []
    for cod in sorted(os.listdir(cartella)):
        f = os.path.join(cartella, cod, f"{cod}.TXT")
        if not os.path.isfile(f):
            continue
        campi = leggi_scheda(f)
        nome = campi.get("PRODOTTO", cod)
        rep, cat, sub, l4 = classifica(nome)
        prodotti.append({"codice": cod, "nome": nome, "formato": campi.get("FORMATO", ""), "rep": rep, "cat": cat,
                         "sub": sub, "l4": l4, "attr": attributi(nome, campi, rep)})

    # ---- Tassonomia.xlsx
    p = percorsi.dati("Tassonomia.xlsx")
    wb = openpyxl.load_workbook(p)
    ws = wb.worksheets[0]
    gia = {str(r[2]) for r in ws.iter_rows(min_row=2, values_only=True)}
    nuovi_t = 0
    for x in prodotti:
        if x["codice"] in gia:
            continue
        spec = re.sub(r"^Oberto - ", "", x["nome"]).upper()
        ws.append([cod_forn, nome_forn, x["codice"], x["nome"], x["rep"], x["cat"], x["sub"],
                   f"{x['rep']} > {x['cat']} > {x['sub']}", x["rep"], x["l4"], "ND", spec, x["formato"].upper()])
        nuovi_t += 1
    wb.save(p)

    # ---- riassunto_prodotti.xlsx e versione arricchita: un foglio per reparto, riepilogo per fornitore
    for nome_file in ("riassunto_prodotti.xlsx", "riassunto_prodotti_arricchito_spezie.xlsx"):
        p = percorsi.dati(nome_file)
        wb = openpyxl.load_workbook(p)
        presenti = set()
        for foglio in ("Carne", "Salumi", "Gelo"):
            for r in wb[foglio].iter_rows(min_row=2, values_only=True):
                presenti.add(str(r[2]))
        aggiunti = 0
        for x in prodotti:
            if x["codice"] in presenti:
                continue
            a = x["attr"]
            wb[x["rep"]].append([cod_forn, nome_forn.capitalize(), x["codice"], f"{x['nome']} - {x['formato']}", a["BIOLOGICO"],
                                 a["VEGANO"], a["VEGETARIANO"], a["SENZA GLUTINE"], a["SENZA LATTOSIO"], a["MILK FREE"],
                                 a["KOSHER"], a["ALLERGENI"], a["TRACCE DI"]])
            aggiunti += 1
        rp = wb["Riepilogo per Fornitore"]
        for riga in rp.iter_rows(min_row=2):
            if str(riga[0].value) == cod_forn:
                tutti = prodotti
                riga[2].value = len(tutti)
                riga[3].value = 0
                riga[4].value = 0
                riga[5].value = sum(1 for x in tutti if x["attr"]["SENZA GLUTINE"] in ("SI", "SI*"))
                riga[6].value = sum(1 for x in tutti if x["attr"]["SENZA LATTOSIO"] == "SI")
                riga[7].value = sum(1 for x in tutti if x["attr"]["MILK FREE"] in ("SI", "SI*"))
                riga[8].value = sum(1 for x in tutti if x["attr"]["ALLERGENI"] not in ("Nessuno rilevato", "?"))
        wb.save(p)
        print(nome_file, "righe aggiunte:", aggiunti)

    # ---- varianti_prodotto.csv: lo stesso prodotto in piu' formati (stesso nome) -> una riga di codici (max 4)
    p = percorsi.dati("varianti_prodotto.csv")
    righe = list(csv.reader(open(p, encoding="utf-8-sig")))
    gia_v = {c for r in righe[1:] if r and r[0] == cod_forn for c in r[1:] if c}
    gruppi = defaultdict(list)
    for x in prodotti:
        gruppi[x["nome"].lower()].append(x["codice"])
    nuove_v = 0
    for codici in gruppi.values():
        codici = [c for c in codici if c not in gia_v]
        for i in range(0, len(codici), 4):
            blocco = codici[i:i + 4]
            if len(blocco) >= 2:
                righe.append([cod_forn] + blocco + [""] * (4 - len(blocco)))
                nuove_v += 1
    with open(p, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(righe)
    print("Tassonomia righe aggiunte:", nuovi_t, "| varianti nuove righe:", nuove_v)
    from collections import Counter
    print(Counter((x["rep"], x["cat"], x["sub"]) for x in prodotti))


if __name__ == "__main__":
    main()
