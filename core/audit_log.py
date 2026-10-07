# -*- coding: utf-8 -*-
"""
Audit log per risposta (JSONL, una riga per turno): serve a capire PERCHE' Nino ha risposto cosi'
(profilo applicato, prodotti nel contesto, esiti dei guardrail, termini senza riscontro, tempi).

    log/audit/AAAA-MM-GG.jsonl      (cartella ignorata da git, env AUDIT_DIR per cambiarla, AUDIT_LOG=0 per spegnerlo)

Uso: audit.inizia(sid, query) a inizio turno; audit.nota("chiave", valore) da qualunque punto del turno;
audit.chiudi(stato, risposta) a fine turno. Non solleva mai eccezioni (il log non deve rompere la chat).
"""
import contextvars
import datetime
import json
import os
import time

_TURNO: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("audit_turno", default=None)


def _attivo() -> bool:
    return os.getenv("AUDIT_LOG", "1") == "1"


def _cartella() -> str:
    return os.getenv("AUDIT_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "log", "audit"))


def inizia(sid: str, query: str) -> None:
    _TURNO.set({"sid": str(sid)[:12], "query": (query or "")[:500], "_t0": time.time(), "note": {}})


def nota(chiave: str, valore) -> None:
    t = _TURNO.get()
    if t is not None:
        t["note"][chiave] = valore


def chiudi(stato: dict, risposta: str = "") -> "dict | None":
    """Scrive la riga di audit del turno e la ritorna (None se spento o turno non iniziato)."""
    t = _TURNO.get()
    if t is None:
        return None
    if not _attivo():
        _TURNO.set(None)
        return None
    try:
        riga = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "sid": t["sid"],
            "query": t["query"],
            "ms": int((time.time() - t["_t0"]) * 1000),
            "profilo": {k: stato.get(k) for k in ("tipo_locale", "canale_locale", "filtro_dieta", "citta", "zona_consegna",
                                                 "stile_cucina") if stato.get(k)},
            "esclusioni": list(stato.get("esclusioni_cliente") or []),
            "risposta_len": len(risposta or ""),
            **t["note"],
        }
        os.makedirs(_cartella(), exist_ok=True)
        percorso = os.path.join(_cartella(), datetime.date.today().isoformat() + ".jsonl")
        with open(percorso, "a", encoding="utf-8") as f:
            f.write(json.dumps(riga, ensure_ascii=False, default=str) + "\n")
        return riga
    except Exception as e:  # il log non deve mai rompere la chat
        print(f"[AUDIT] errore (ignorato): {e}")
        return None
    finally:
        _TURNO.set(None)
