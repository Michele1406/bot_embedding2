#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_tassonomia_sofood.py
============================
Rigenera tassonomia_sofood.py leggendo Tassonomia.xlsx. Va lanciato ogni
volta che Tassonomia.xlsx cambia (nuove sottocategorie, nuovi prodotti
classificati, correzioni della pipeline offline ECR).

Dopo aver rigenerato tassonomia_sofood.py, ricordati di lanciare anche
rigenera_tassonomia_db.py per applicare lo stesso Tassonomia.xlsx ai
metadati già presenti in ChromaDB.

Uso:
    python genera_tassonomia_sofood.py
    python genera_tassonomia_sofood.py --file Tassonomia.xlsx --out tassonomia_sofood.py
"""

import argparse
import pandas as pd


def _norm(s) -> str:
    s = str(s).strip()
    if s.lower() in ("nan", "none", "nd", "n.d.", "n/a"):
        return ""
    return " ".join(s.split()).upper()


TEMPLATE_HEADER = '''# -*- coding: utf-8 -*-
"""
tassonomia_sofood.py
=====================
SINGOLA FONTE DI VERITA' per la classificazione merceologica del catalogo
So Food, generata automaticamente da Tassonomia.xlsx (standard ECR Grocery)
con genera_tassonomia_sofood.py. NON modificare questo file a mano: quando
Tassonomia.xlsx cambia, rilancia genera_tassonomia_sofood.py.

Nota importante: Reparto e la vecchia "Categoria Prodotto" sono ORA LO
STESSO CAMPO (unificati nel nuovo Excel): 6 macro-aree, sempre in MAIUSCOLO
-> CARNE, DISPENSA, FORMAGGI, GELO, MARE, SALUMI.

TASSONOMIA_LISTA contiene le combinazioni Reparto > Categoria Tassonomia >
Sottocategoria realmente presenti a catalogo. Il campo "n" è il numero di
prodotti reali con quella esatta combinazione nell'ultima Tassonomia.xlsx
caricata: serve a MAPPA_SOTTOCATEGORIA qui sotto per scegliere, quando una
sottocategoria compare sotto più di una Categoria Tassonomia (capita per una
manciata di voci: è un'imprecisione ereditata dalla classificazione LLM di
origine, non un bug di questo file), la combinazione più frequente invece
che la prima in ordine alfabetico.
"""

TASSONOMIA_LISTA = [
'''

TEMPLATE_FOOTER = '''
]

# ============================================================================
# Strutture derivate (calcolate automaticamente da TASSONOMIA_LISTA, non
# toccarle a mano)
# ============================================================================
REPARTI = sorted({v["reparto"] for v in TASSONOMIA_LISTA})
SOTTOCATEGORIE_NOMI = sorted({v["sottocategoria"] for v in TASSONOMIA_LISTA})

# Mappa sottocategoria (lowercase) -> dict completo {sottocategoria, categoria, reparto}.
# Quando una sottocategoria compare sotto più di una combinazione categoria/
# reparto, si tiene quella con più prodotti reali (campo "n"), non la prima
# trovata in ordine alfabetico.
MAPPA_SOTTOCATEGORIA = {}
for _voce in sorted(TASSONOMIA_LISTA, key=lambda v: v["n"], reverse=True):
    _chiave = _voce["sottocategoria"].strip().lower()
    if _chiave not in MAPPA_SOTTOCATEGORIA:
        MAPPA_SOTTOCATEGORIA[_chiave] = _voce

# Mappa Categoria Tassonomia (lowercase) -> reparto (comodo per filtri ampi).
MAPPA_CATEGORIA_REPARTO = {}
for _voce in TASSONOMIA_LISTA:
    _chiave = _voce["categoria"].strip().lower()
    MAPPA_CATEGORIA_REPARTO.setdefault(_chiave, _voce["reparto"])


# ============================================================================
# CLASSIFICAZIONE "TERRA / MARE" (usata per il consiglio commerciale, non per
# filtri di esclusione: un locale di mare NON deve vedersi negare la carne,
# vedi regole in system_prompt_v2.py e retrieval_utils.py)
# ============================================================================
REPARTI_MARE = {"MARE"}
REPARTI_TERRA = {"CARNE", "SALUMI"}
REPARTI_NEUTRI = {"FORMAGGI", "DISPENSA", "GELO"}


def classifica_terra_mare(reparto: str) -> str:
    """Ritorna 'mare', 'terra' o 'neutro' a partire dal reparto ufficiale del
    prodotto. Usata per capire quali referenze spingere per primi in un
    locale di mare (senza per questo vietare le altre)."""
    r = (reparto or "").strip().upper()
    if r in REPARTI_MARE:
        return "mare"
    if r in REPARTI_TERRA:
        return "terra"
    return "neutro"
'''


def genera(file_excel: str = "Tassonomia.xlsx", file_out: str = "tassonomia_sofood.py") -> None:
    print(f"[1/3] Lettura '{file_excel}'...")
    df = pd.read_excel(file_excel, dtype=str)
    df["Reparto_n"] = df["Reparto"].map(_norm)
    df["Cat_n"] = df["Categoria Tassonomia"].map(_norm)
    df["Sub_n"] = df["Sottocategoria"].map(_norm)

    print("[2/3] Calcolo combinazioni uniche Reparto > Categoria > Sottocategoria...")
    g = df.groupby(["Reparto_n", "Cat_n", "Sub_n"]).size().reset_index(name="n")
    g = g.sort_values(["Reparto_n", "Cat_n", "Sub_n"])

    righe = []
    for _, r in g.iterrows():
        righe.append(
            f'    {{"sottocategoria": {r["Sub_n"]!r}, "categoria": {r["Cat_n"]!r}, '
            f'"reparto": {r["Reparto_n"]!r}, "n": {int(r["n"])}}},'
        )
    corpo = "\n".join(righe)

    print(f"[3/3] Scrittura '{file_out}' ({len(righe)} combinazioni)...")
    with open(file_out, "w", encoding="utf-8") as f:
        f.write(TEMPLATE_HEADER + corpo + "\n" + TEMPLATE_FOOTER)

    print(f"\n[OK] {file_out} rigenerato con successo.")
    print("Prossimo passo: lancia rigenera_tassonomia_db.py per applicare gli stessi dati a ChromaDB.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rigenera tassonomia_sofood.py da Tassonomia.xlsx")
    parser.add_argument("--file", default="Tassonomia.xlsx", help="Percorso del file Tassonomia.xlsx")
    parser.add_argument("--out", default="tassonomia_sofood.py", help="File Python di destinazione")
    args = parser.parse_args()
    genera(args.file, args.out)
