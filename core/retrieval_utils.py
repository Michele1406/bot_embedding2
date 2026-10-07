from core.domain_rules import check_board_violations, prodotto_appartiene_a_famiglia, INCOMPATIBILITY_MATRIX
from core.testo_prodotto import prima_riga, nome_senza_produttore
from core.parse_formato import formato_prodotto
from core import logistica, ontologia
"""
retrieval_utils.py
====================
Logica di recupero condivisa tra app.py (server web) e main_chatbot_v2.py (CLI).

RICERCA IBRIDA in tre livelli, sempre combinati:
1. Match ESATTO su codice prodotto (se la query contiene un codice noto).
2. Match su fornitore/brand (se la query nomina un produttore o un suo alias
   inequivocabile — es. "Farino", "De Giorgi").
3. Ricerca vettoriale semantica su ChromaDB per tutto il resto.

NOTA DI DESIGN (importante, leggi prima di aggiungere nuove regole):
Questo file un tempo conteneva anche un "classificatore di intent" per
parole chiave (tagliere, aperitivo, cucina pugliese, cucina romana, ecc.)
che filtrava e forzava fornitori "prioritari" per ciascuna categoria.
È stato rimosso perché causava due problemi seri, verificati sulle chat
reali del bot:
  1. Suggeriva sempre gli stessi ~10 fornitori (quelli inseriti a mano nelle
     liste "fornitori_prioritari"), indipendentemente da cosa chiedesse
     davvero il cliente — un bias di business hardcoded nel codice, non un
     comportamento emergente dell'IA.
  2. Scartava dal contesto RAG prodotti validi solo perché il loro testo
     conteneva una parola nella blocklist ("crema di", "julienne", ecc.),
     facendo sì che il bot dicesse "non ce l'ho" su prodotti realmente a
     catalogo, semplicemente perché il filtro li aveva già buttati via
     prima che il modello li vedesse.
Se in futuro serve davvero escludere una categoria di prodotti da un
contesto specifico, è molto più sicuro farlo con un'istruzione in linguaggio
naturale nel system prompt (che il modello applica con buon senso, caso per
caso) piuttosto che con una blocklist di sottostringhe nel codice (che non
distingue il contesto ed è un elenco infinito da manutenere).
"""

import os
import re
import json

# retrieval_utils e' ora una FACADE: la logica vive nei moduli sotto, qui si ri-esportano tutti i nomi
# (anche quelli con underscore, usati da app.py e dai test) per non rompere gli import esistenti.
import contextvars
from core import vincoli_dieta as _vincoli_dieta
from core import ricerca_base as _ricerca_base
from core import ricerca_prodotti as _ricerca_prodotti
from core import contesto_prodotti as _contesto_prodotti
from core import ricettario as _ricettario


def _riesporta():
    g = globals()
    for _mod in (_vincoli_dieta, _ricerca_base, _ricerca_prodotti, _contesto_prodotti, _ricettario):
        for _k, _v in vars(_mod).items():
            if not _k.startswith('__') and _k not in g:
                g[_k] = _v


_riesporta()
