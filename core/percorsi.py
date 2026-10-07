# -*- coding: utf-8 -*-
"""
Dove stanno i file di anagrafica: nella cartella dati PRODOTTI SOFOOD (DATA_LAKE_PATH), fonte unica.

  radice:   Tassonomia.xlsx, fornitori.csv, riassunto_prodotti*.xlsx, varianti_prodotto.csv (+ cartelle fornitore)
  sofood/:  ABSTRACT.xlsx, consegne.xlsx, calendario_freschi.xlsx, SOFOOD.txt, regioni_produttori.csv

Le schede prodotto (cartelle 19xxxxxx) restano in sola lettura; old/ contiene le versioni precedenti allo spostamento.
"""
import os

from dotenv import load_dotenv

_RADICE_PROGETTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_RADICE_PROGETTO, ".env"))

_AZIENDA = {"ABSTRACT.xlsx", "consegne.xlsx", "calendario_freschi.xlsx", "SOFOOD.txt", "regioni_produttori.csv"}


def cartella_dati() -> str:
    return os.getenv("DATA_LAKE_PATH", r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD")


def dati(nome: str) -> str:
    """Percorso completo di un file di anagrafica (vedi docstring)."""
    return os.path.join(cartella_dati(), "sofood", nome) if nome in _AZIENDA else os.path.join(cartella_dati(), nome)
