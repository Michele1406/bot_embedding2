# -*- coding: utf-8 -*-
"""Ambiente condiviso dei test di integrazione: DB reale + embedder con cache su disco.

La cache (data/cache_embedding_test.json) evita di consumare la quota API a ogni esecuzione:
solo le query nuove chiamano Gemini.
"""
import hashlib
import json
import os
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
FILE_CACHE = os.path.join(RADICE, "data", "cache_embedding_test.json")
MODELLO = "models/gemini-embedding-2"


class EmbedderCache:
    def __init__(self, client=None):
        self.client = client
        self.cache = {}
        self.errori = 0   # chiamate fallite (quota/rete): i casi che ne subiscono non sono valutabili
        try:
            with open(FILE_CACHE, encoding="utf-8") as f:
                self.cache = json.load(f)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _k(t):
        return hashlib.sha1(f"{MODELLO}|{t}".encode("utf-8")).hexdigest()

    def embed_query(self, testo):
        k = self._k(testo)
        if k in self.cache:
            return self.cache[k]
        from google.genai import types
        try:
            if self.client is None:
                raise RuntimeError("GEMINI_API_KEY mancante")
            r = self.client.models.embed_content(model=MODELLO, contents=[testo],
                                                 config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"))
        except Exception:
            self.errori += 1
            raise
        v = r.embeddings[0]
        vet = list(v.values if hasattr(v, "values") else v)
        self.cache[k] = vet
        try:
            with open(FILE_CACHE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f)
        except OSError:
            pass
        return vet


_AMB = None


def carica(collezione=None, ricette=None):
    """Ritorna il dizionario d'ambiente oppure None se mancano chiave/DB. Memorizzato."""
    global _AMB
    if _AMB is not None:
        return _AMB or None
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(RADICE, ".env"))
    except Exception:
        pass
    chiave = os.getenv("GEMINI_API_KEY")
    if not os.path.isdir(os.path.join(RADICE, "database_vettoriale")):
        _AMB = False
        return None
    import chromadb
    from core import retrieval_utils as ru
    client = None
    if chiave:
        from google import genai
        client = genai.Client(api_key=chiave)
    db = chromadb.PersistentClient(path=os.path.join(RADICE, "database_vettoriale"))
    col = db.get_collection(collezione or os.getenv("CATALOGO_COLLECTION", "catalogo_v2"))
    _AMB = {
        "db": db, "col": col, "emb": EmbedderCache(client),
        "ric": db.get_collection(ricette or os.getenv("RICETTE_COLLECTION", "ricette_v2")),
        "ic": ru.costruisci_indice_codici(col),
        "inf": ru.costruisci_indice_fornitori(col),
        "it": ru.costruisci_indice_testuale(col),
    }
    return _AMB
