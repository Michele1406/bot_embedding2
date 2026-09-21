import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

"""
ispettore_v2.py
================
Diagnostica sullo stato del database vettoriale. Oltre al riepilogo per
fornitore, ora controlla che l'indice vettoriale HNSW (usato per la ricerca
per similarità) sia allineato al numero di documenti effettivamente salvati
nei metadati. Se non lo è, alcuni prodotti a catalogo sono invisibili a
QUALSIASI ricerca vettoriale, pur essendo presenti e "corretti" nei dati.
"""

import pickle
from pathlib import Path
import chromadb

PERCORSO_DB = "./database_vettoriale"
NOME_COLLEZIONE = "catalogo_sofood"


def controlla_allineamento_indice(percorso_db: str, n_documenti_totali: int) -> None:
    """Cerca il file index_metadata.pickle dentro la struttura interna di
    Chroma e confronta 'total_elements_added' con il numero reale di
    documenti.

    NOTA: un numero diverso qui NON significa che quei prodotti siano
    irraggiungibili dalla ricerca. Chroma tiene gli elementi aggiunti di
    recente in una coda interna che viene comunque interrogata insieme
    all'indice HNSW su disco al momento della query, anche prima che quella
    coda venga scritta in modo definitivo nel file binario. Questo controllo
    resta utile solo come informazione di manutenzione (dimensione della
    coda non ancora "compattata"), non come diagnosi di un bug funzionale."""
    percorso_pickle = None
    for p in Path(percorso_db).rglob("index_metadata.pickle"):
        percorso_pickle = p
        break

    if percorso_pickle is None:
        print("\n[AVVISO] Non ho trovato index_metadata.pickle: impossibile leggere questa informazione.")
        return

    try:
        with open(percorso_pickle, "rb") as f:
            dati_indice = pickle.load(f)
    except Exception as e:
        print(f"\n[AVVISO] Impossibile leggere {percorso_pickle}: {e}")
        return

    elementi_nell_indice = dati_indice.get("total_elements_added")
    if elementi_nell_indice is None:
        print("\n[AVVISO] Il file dell'indice non contiene 'total_elements_added'.")
        return

    print(f"\n[INFO INDICE VETTORIALE — solo informativo, non è un problema funzionale]")
    print(f"   Documenti nei metadati (ChromaDB): {n_documenti_totali}")
    print(f"   Elementi già compattati nell'indice su disco: {elementi_nell_indice}")
    if elementi_nell_indice != n_documenti_totali:
        print(f"   ({n_documenti_totali - elementi_nell_indice} prodotti sono ancora nella coda interna,")
        print("    ma restano comunque ricercabili: Chroma li include nelle query.)")


def main():
    client = chromadb.PersistentClient(path=PERCORSO_DB)
    try:
        collezione = client.get_collection(NOME_COLLEZIONE)
        dati = collezione.get()

        totale = len(dati.get("ids", []))
        print(f"Totale prodotti indicizzati finora: {totale}")

        fornitori_conteggio = {}
        categorie_conteggio = {}
        prodotti_senza_immagine = 0

        for meta in dati.get("metadatas", []):
            nome_supp = meta.get("nome_fornitore", "Sconosciuto")
            cod_supp = meta.get("codice_fornitore", "Sconosciuto")
            chiave = f"{cod_supp} - {nome_supp}"
            fornitori_conteggio[chiave] = fornitori_conteggio.get(chiave, 0) + 1

            categoria = meta.get("categoria_prodotto", "Sconosciuta")
            categorie_conteggio[categoria] = categorie_conteggio.get(categoria, 0) + 1

            if not meta.get("ha_immagine_primaria"):
                prodotti_senza_immagine += 1

        print("\nRiepilogo per fornitore:")
        for supp, count in sorted(fornitori_conteggio.items(), key=lambda x: x[1], reverse=True):
            print(f"   - {supp}: {count} prodotti")

        print("\nRiepilogo per categoria:")
        for cat, count in sorted(categorie_conteggio.items(), key=lambda x: x[1], reverse=True):
            print(f"   - {cat}: {count} prodotti")

        print(f"\nProdotti senza immagine mappata: {prodotti_senza_immagine} / {totale}")

        controlla_allineamento_indice(PERCORSO_DB, totale)

    except Exception as e:
        print(f"Errore nella lettura del DB: {e}")


if __name__ == "__main__":
    main()
