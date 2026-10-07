# -*- coding: utf-8 -*-
"""
Conservazione dei dati (fase di test, scelta D6 del report):
  - log/chat/*.json e log/audit/*.jsonl  : 30 giorni (servono a debug e audit delle risposte)
  - data/sessioni.db                      : 7 giorni di inattivita' (TTL gia' in core/sessioni_store.py)
  - data/ordini/*.json                    : NON cancellati in automatico (documenti commerciali)
  - static/audio_uploads/*                : 7 giorni (vocali della UI di test)
Si esegue all'avvio del server. Variabile RETENTION_GIORNI_LOG per cambiare i 30 giorni (0 = non cancellare).
"""
import glob
import os
import time

_RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pulisci(schema: str, giorni: int) -> int:
    if giorni <= 0:
        return 0
    limite = time.time() - giorni * 86400
    n = 0
    for f in glob.glob(os.path.join(_RADICE, schema)):
        try:
            if os.path.isfile(f) and os.path.getmtime(f) < limite:
                os.remove(f)
                n += 1
        except OSError:
            pass
    return n


def pulizia_retention() -> dict:
    giorni_log = int(os.getenv("RETENTION_GIORNI_LOG", "30") or 0)
    esito = {
        "log_chat": _pulisci(os.path.join("log", "chat", "*.json"), giorni_log),
        "log_audit": _pulisci(os.path.join("log", "audit", "*.jsonl"), giorni_log),
        "audio": _pulisci(os.path.join("static", "audio_uploads", "*"), 7),
    }
    try:
        from core import sessioni_store
        esito["sessioni"] = sessioni_store.elimina_scadute()
    except Exception:
        esito["sessioni"] = 0
    if any(esito.values()):
        print(f"[MANUTENZIONE] file rimossi per scadenza: {esito}")
    return esito
