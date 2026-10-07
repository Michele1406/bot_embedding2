# -*- coding: utf-8 -*-
"""Controllo di integrita' del sistema (nessuna chiamata API): da lanciare dopo ogni aggiornamento dati.

    python scripts/verifica_sistema.py

Controlla: collezioni e conteggi, copertura degli attributi, fornitori <-> fornitori.csv <-> ABSTRACT <-> calendario freschi,
configurazioni YAML, variabili d'ambiente (senza stamparne i valori). Esce con codice 1 se ci sono ERRORI (gli AVVISI no).
"""
import csv
import os
import re
import sys

RADICE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, RADICE)
from core import percorsi  # noqa: E402

errori, avvisi = [], []


def ok(msg):
    print(f"  [ok]     {msg}")


def err(msg):
    errori.append(msg)
    print(f"  [ERRORE] {msg}")


def avv(msg):
    avvisi.append(msg)
    print(f"  [avviso] {msg}")


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", str(t or "").lower()).strip()


def main():
    import chromadb
    import yaml
    from dotenv import load_dotenv
    load_dotenv(os.path.join(RADICE, ".env"))

    print("Ambiente")
    if os.getenv("GEMINI_API_KEY"):
        ok("GEMINI_API_KEY presente")
    else:
        err("GEMINI_API_KEY mancante")
    if not os.getenv("FLASK_SECRET_KEY"):
        avv("FLASK_SECRET_KEY non impostata: le sessioni del browser si perdono a ogni riavvio")
    if os.getenv("WHATSAPP_ENABLED") == "1" and not os.getenv("WHATSAPP_APP_SECRET"):
        avv("WhatsApp attivo senza WHATSAPP_APP_SECRET: le richieste non sono firmate")
    if not os.getenv("ERP_WEBHOOK_URL"):
        avv("ERP_WEBHOOK_URL vuoto: gli ordini si salvano in data/ordini/ (nessun invio al gestionale)")

    print("Database vettoriale")
    db = chromadb.PersistentClient(os.path.join(RADICE, "database_vettoriale"))
    nomi = {c.name for c in db.list_collections()}
    cat_nome = os.getenv("CATALOGO_COLLECTION", "catalogo_v2")
    ric_nome = os.getenv("RICETTE_COLLECTION", "ricette_v2")
    for n in (cat_nome, ric_nome, "aziende_sofood"):
        (ok if n in nomi else err)(f"collezione {n}" + ("" if n in nomi else " mancante"))
    if cat_nome not in nomi:
        return finale()
    cat = db.get_collection(cat_nome).get(include=["metadatas", "documents"])
    n = len(cat["ids"])
    ok(f"{cat_nome}: {n} prodotti")
    metas = cat["metadatas"]
    for campo, minimo in (("attributi_ok", 0.97), ("modalita_ok", 0.97), ("canale_formato", 0.99), ("tipo_prodotto", 0.97)):
        pieni = sum(1 for m in metas if m.get(campo) not in (None, "", False))
        (ok if pieni / n >= minimo else avv)(f"{campo}: {pieni}/{n} ({100 * pieni / n:.0f}%)")
    sconosciuti = sum(1 for m in metas if m.get("canale_formato") in ("sconosciuto", "", None))
    (ok if sconosciuti / n < 0.15 else avv)(f"canale_formato sconosciuto: {sconosciuti}/{n}")

    print("Fornitori")
    forn_cat = {}
    for m in metas:
        forn_cat[str(m.get("codice_fornitore") or "")] = str(m.get("nome_fornitore") or "")
    righe = list(csv.DictReader(open(percorsi.dati("fornitori.csv"), encoding="utf-8-sig")))
    codici_csv = {str(r.get("an_forn") or "").strip() for r in righe}
    mancanti_csv = [f"{c} {nm}" for c, nm in forn_cat.items() if c and c not in codici_csv]
    (ok if not mancanti_csv else err)("tutti i fornitori del catalogo sono in fornitori.csv" if not mancanti_csv
                                      else f"fornitori del catalogo non in fornitori.csv: {mancanti_csv}")
    import openpyxl
    ws = openpyxl.load_workbook(percorsi.dati("ABSTRACT.xlsx"), read_only=True).active
    codici_abs = {str(r[0]).strip() for r in ws.iter_rows(min_row=2, values_only=True) if r and r[0]}
    senza_abs = [f"{c} {nm}" for c, nm in forn_cat.items() if c and c not in codici_abs]
    (ok if not senza_abs else avv)("tutti i fornitori hanno la scheda in ABSTRACT.xlsx" if not senza_abs
                                   else f"fornitori senza scheda ABSTRACT: {senza_abs}")
    if "aziende_sofood" in nomi:
        az = db.get_collection("aziende_sofood").get(include=["metadatas"])
        nomi_az = {norm(m.get("nome_fornitore")) for m in az["metadatas"]}
        mancanti_db = [nm for c, nm in forn_cat.items() if c in codici_abs and norm(nm) not in nomi_az
                       and not any(norm(nm) in x or x in norm(nm) for x in nomi_az)]
        (ok if not mancanti_db else avv)("schede aziende caricate nel database" if not mancanti_db
                                         else f"schede in ABSTRACT non caricate in aziende_sofood (lanciare carica_abstract_fornitori_v2.py): {mancanti_db}")
    from core import logistica
    nomi_forn = [norm(x) for x in forn_cat.values()]
    for az_cal in logistica.calendario_ordini():
        if not any(az_cal in x or x in az_cal for x in nomi_forn):
            avv(f"calendario freschi: '{az_cal}' non corrisponde a nessun fornitore del catalogo")

    print("Configurazioni")
    for f in ("regole_cliente.yaml", "core/ontologia_horeca.yaml", "core/ricettario_extra.yaml", "core/zone_consegna.yaml"):
        try:
            yaml.safe_load(open(os.path.join(RADICE, f), encoding="utf-8"))
            ok(f)
        except Exception as e:
            err(f"{f}: {e}")
    reg = yaml.safe_load(open(os.path.join(RADICE, "regole_cliente.yaml"), encoding="utf-8"))
    for k in ("azienda", "regole_composizione", "regole_dieta"):
        (ok if k in reg else err)(f"regole_cliente.yaml: sezione '{k}'")

    print("Ricettario")
    if ric_nome in nomi:
        r = db.get_collection(ric_nome).get(include=["metadatas"])
        ok(f"{ric_nome}: {len(r['ids'])} ricette")
        senza = sum(1 for m in r["metadatas"] if not m.get("copertura_catalogo"))
        (ok if senza == 0 else avv)(f"ricette senza copertura_catalogo: {senza}")
    return finale()


def finale():
    print(f"\n{len(errori)} errori, {len(avvisi)} avvisi")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())
