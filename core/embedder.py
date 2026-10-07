# -*- coding: utf-8 -*-
"""
Embedding delle query con cache (memoria + SQLite).

Ogni ricerca costa una chiamata di embedding e il piano gratuito ne concede 1000 al giorno. Le query si ripetono
molto (safety net, slot dei ricettari, clienti diversi che chiedono "salumi" o "olive"): con la cache la stessa
query si paga una volta sola. Il vettore dipende solo da (modello, testo, task), quindi la cache non scade.

    EmbedderGemini(api_key, modello).embed_query(testo) -> list[float]

Variabili: EMBEDDING_CACHE_DB (default data/cache_embedding.sqlite), EMBEDDING_CACHE=0 per disattivarla.
La quota esaurita (429) non si ritenta: il chiamante ricade sul solo lessicale.
"""
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict

_RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CacheEmbedding:
    def __init__(self, percorso: "str | None" = None, max_memoria: int = 2000):
        self.percorso = percorso or os.getenv("EMBEDDING_CACHE_DB", os.path.join(_RADICE, "data", "cache_embedding.sqlite"))
        self.memoria: "OrderedDict[str, list]" = OrderedDict()
        self.max_memoria = max_memoria
        self.lock = threading.Lock()
        self.colpi = self.mancati = 0

    def _esegui(self, sql: str, parametri: tuple):
        """Esegue una query e CHIUDE la connessione (con `with sqlite3.connect()` resta aperta e su Windows il file
        rimane bloccato)."""
        os.makedirs(os.path.dirname(self.percorso), exist_ok=True)
        c = sqlite3.connect(self.percorso, timeout=10)
        try:
            with c:
                c.execute("CREATE TABLE IF NOT EXISTS emb (chiave TEXT PRIMARY KEY, vettore TEXT)")
                return c.execute(sql, parametri).fetchone()
        finally:
            c.close()

    @staticmethod
    def chiave(modello: str, testo: str, task: str) -> str:
        return f"{modello}|{task}|{' '.join((testo or '').split()).lower()}"

    def leggi(self, chiave: str) -> "list | None":
        with self.lock:
            if chiave in self.memoria:
                self.memoria.move_to_end(chiave)
                self.colpi += 1
                return self.memoria[chiave]
        try:
            r = self._esegui("SELECT vettore FROM emb WHERE chiave = ?", (chiave,))
        except sqlite3.Error:
            r = None
        if r:
            v = json.loads(r[0])
            self._in_memoria(chiave, v)
            self.colpi += 1
            return v
        self.mancati += 1
        return None

    def scrivi(self, chiave: str, vettore: list) -> None:
        self._in_memoria(chiave, vettore)
        try:
            self._esegui("INSERT OR REPLACE INTO emb (chiave, vettore) VALUES (?, ?)", (chiave, json.dumps(vettore)))
        except sqlite3.Error as e:
            print(f"[EMBEDDING] cache su disco non scritta: {e}")

    def _in_memoria(self, chiave: str, vettore: list) -> None:
        with self.lock:
            self.memoria[chiave] = vettore
            self.memoria.move_to_end(chiave)
            while len(self.memoria) > self.max_memoria:
                self.memoria.popitem(last=False)


class EmbedderGemini:
    """Calcola i vettori delle query con gemini-embedding-2 (stesso spazio dei prodotti in ChromaDB)."""

    def __init__(self, api_key: str, model_name: str, client=None, cache: "CacheEmbedding | None" = None):
        if client is None:
            from google import genai
            client = genai.Client(api_key=api_key)
        self.client = client
        self.model_name = model_name
        attiva = os.getenv("EMBEDDING_CACHE", "1") == "1"
        self.cache = cache if cache is not None else (CacheEmbedding() if attiva else None)

    def embed_query(self, testo: str) -> list:
        chiave = CacheEmbedding.chiave(self.model_name, testo, "RETRIEVAL_QUERY")
        if self.cache is not None:
            v = self.cache.leggi(chiave)
            if v is not None:
                return v
        from google.genai import types
        ultimo = None
        for tentativo in range(2):
            try:
                response = self.client.models.embed_content(
                    model=self.model_name, contents=[testo],
                    config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"))
                vettore = response.embeddings[0]
                valori = list(vettore.values if hasattr(vettore, "values") else vettore)
                if self.cache is not None:
                    self.cache.scrivi(chiave, valori)
                return valori
            except Exception as e:
                ultimo = e
                from core.errori import e_quota
                if e_quota(e) or tentativo == 1:
                    break
                time.sleep(1.0)
        raise ultimo
