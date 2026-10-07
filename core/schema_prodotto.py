# -*- coding: utf-8 -*-
"""
schema_prodotto.py
==================
Attributi strutturati per prodotto (Fase 1 di implementationplan.md).

Il reparto/sottocategoria della Tassonomia dicono COSA e' il prodotto; qui si
aggiunge COME si usa in cucina (`ruoli_uso`) e di che materia prima e' fatto
(`origine_proteina`). Sono i campi che mancavano: per il motore di ricerca il
wurstel era un "salume" come il culatello.
"""

from typing import Literal

from pydantic import BaseModel, Field

RUOLI_USO = [
    "tagliere",             # da servire freddo/a temperatura ambiente su un tagliere o piatto di degustazione
    "antipasto",
    "aperitivo",            # stuzzichino, snack da aperitivo
    "primo",                # pasta, riso, gnocchi, sughi e condimenti per primi
    "secondo",              # piatto principale a base di proteina
    "contorno",
    "dolce",                # dessert, gelato, pasticceria
    "colazione",
    "panino_fast_food",     # farcitura per panini, hot dog, burger
    "cottura_griglia_padella",  # va cucinato prima di mangiarlo (griglia, padella, forno, bollitura)
    "ingrediente_base",     # ingrediente di cucina (farina, olio, burro, uova, conserve da cuocere)
    "condimento_salsa",     # salse, condimenti, spezie, aromi, aceti
    "pizza_pane",           # basi per pizza, pane, lievitati, farine da panificazione
    "bevanda",
    "altro",
]

ORIGINE_PROTEINA = [
    "carne_suina", "carne_bovina", "pollame", "altra_carne", "pesce", "latticini", "uova",
    "vegetale", "nessuna",
]


class AttributiProdotto(BaseModel):
    id: str = Field(description="L'id del prodotto, copiato esattamente dall'input.")
    tipo_prodotto: str = Field(
        description="Nome generico minuscolo del tipo di prodotto, senza marchio ne' formato "
                    "(es. 'wurstel', 'prosciutto crudo', 'finocchiona', 'latte fresco intero', 'orecchiette').")
    origine_proteina: Literal[
        "carne_suina", "carne_bovina", "pollame", "altra_carne", "pesce", "latticini", "uova", "vegetale", "nessuna"
    ] = Field(description="Materia prima principale. 'pesce' anche per 'salumi di pesce'/ 'prosciutto di tonno'.")
    ruoli_uso: list[Literal[
        "tagliere", "antipasto", "aperitivo", "primo", "secondo", "contorno", "dolce", "colazione",
        "panino_fast_food", "cottura_griglia_padella", "ingrediente_base", "condimento_salsa",
        "pizza_pane", "bevanda", "altro"
    ]] = Field(description="Da 1 a 4 impieghi gastronomici REALI del prodotto, dal piu' tipico al meno tipico.")
    richiede_cottura: bool = Field(description="True se va cotto/cucinato prima del consumo.")
    fascia: Literal["premium", "standard", "industriale"] = Field(
        description="premium=DOP/IGP/artigianale di pregio/stagionatura lunga; industriale=prodotto "
                    "di massa/fast food/primo prezzo; altrimenti standard.")
    confidenza: float = Field(description="0-1: quanto sei sicuro di questa classificazione.")


class BatchAttributi(BaseModel):
    prodotti: list[AttributiProdotto]
