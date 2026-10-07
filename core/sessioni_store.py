# -*- coding: utf-8 -*-
"""
sessioni_store.py
=================
Persistenza delle sessioni di chat su SQLite (Fase 4 di implementationplan.md).

Prima lo stato della conversazione viveva solo in un dict in RAM (`sessioni` in app.py): a ogni
riavvio del server il cliente perdeva profilo (tipo di locale, dieta, citta'), prodotti gia'
proposti e storico, e il file di log del giorno veniva sovrascritto da una sessione vuota. Con piu'
processi (gunicorn) ogni worker avrebbe avuto una memoria diversa.

Il profilo (dieta, tipo di locale...) e' un VINCOLO PERSISTENTE: un cliente vegano non deve tornare a
vedere carne dopo un riavvio.

Uso (app.py): al primo accesso di un sid si prova `carica(sid)`; dopo ogni risposta si chiama `salva(sid, stato)`.
Nessuna dipendenza esterna (sqlite3 della libreria standard).
"""

import contextlib
import json
import os
import sqlite3
import time

_PERCORSO = os.getenv("SESSIONI_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sessioni.db"))
_TTL_SEC = 7 * 24 * 3600  # le sessioni inattive da piu' di 7 giorni non vengono ricaricate

# campi semplici (JSON-serializzabili) e insiemi da convertire in liste
_CAMPI = ["tipo_locale", "filtro_dieta", "senza_affettatrice", "citta", "canale_locale", "stile_cucina",
          "ultimo_piatto_proposto", "contatore_messaggi", "prodotti_mostrati_ordinati", "log_chat", "esclusioni_cliente", "riassunto",
          "allergie", "modalita_composizione", "ordine_in_attesa", "ordini_registrati", "costruzione", "prodotti_in_focus"]
_INSIEMI = ["prodotti_mostrati", "ricette_mostrate"]


@contextlib.contextmanager
def _conn():
    """Connessione con commit e CHIUSURA garantita: `with sqlite3.connect()` fa solo il commit e lascia il file
    aperto (a ogni turno una connessione in piu'; su Windows il file resta bloccato)."""
    os.makedirs(os.path.dirname(_PERCORSO), exist_ok=True)
    c = sqlite3.connect(_PERCORSO, timeout=10)
    try:
        with c:
            c.execute("CREATE TABLE IF NOT EXISTS sessioni (sid TEXT PRIMARY KEY, aggiornata REAL, dati TEXT)")
            yield c
    finally:
        c.close()


def _serializza_storico(storico) -> list:
    out = []
    for c in storico or []:
        try:
            testo = "".join(getattr(p, "text", "") or "" for p in c.parts)
            out.append({"role": c.role, "text": testo})
        except Exception:
            continue
    return out


def _deserializza_storico(righe: list):
    from google.genai import types
    return [types.Content(role=r["role"], parts=[types.Part.from_text(text=r["text"])]) for r in righe if r.get("text")]


def salva(sid: str, stato: dict) -> bool:
    try:
        dati = {k: stato.get(k) for k in _CAMPI}
        for k in _INSIEMI:
            dati[k] = sorted(stato.get(k) or [])
        dati["storico"] = _serializza_storico(stato.get("storico"))
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO sessioni (sid, aggiornata, dati) VALUES (?, ?, ?)",
                      (sid, time.time(), json.dumps(dati, ensure_ascii=False, default=str)))
        return True
    except Exception as e:
        print(f"[SESSIONI] salvataggio fallito: {e}")
        return False


def carica(sid: str) -> "dict | None":
    """Stato ricostruito per `sid`, o None se assente/scaduto."""
    try:
        with _conn() as c:
            r = c.execute("SELECT aggiornata, dati FROM sessioni WHERE sid = ?", (sid,)).fetchone()
        if not r or time.time() - r[0] > _TTL_SEC:
            return None
        dati = json.loads(r[1])
        stato = {k: dati.get(k) for k in _CAMPI}
        for k in _INSIEMI:
            stato[k] = set(dati.get(k) or [])
        stato["storico"] = _deserializza_storico(dati.get("storico") or [])
        stato["log_chat"] = stato.get("log_chat") or []
        stato["prodotti_mostrati_ordinati"] = stato.get("prodotti_mostrati_ordinati") or []
        stato["contatore_messaggi"] = stato.get("contatore_messaggi") or 0
        return stato
    except Exception as e:
        print(f"[SESSIONI] caricamento fallito: {e}")
        return None


def elimina_scadute() -> int:
    try:
        with _conn() as c:
            return c.execute("DELETE FROM sessioni WHERE aggiornata < ?", (time.time() - _TTL_SEC,)).rowcount
    except Exception:
        return 0
