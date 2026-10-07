# -*- coding: utf-8 -*-
"""Messaggi d'errore brevi per i log (un 429 di Google stampa ~40 righe di JSON e rende i log illeggibili)."""


def breve(e, massimo: int = 160) -> str:
    s = str(e)
    if "RESOURCE_EXHAUSTED" in s or s.startswith("429"):
        return "429 quota API esaurita"
    if "UNAVAILABLE" in s or s.startswith("503"):
        return "503 servizio del modello non disponibile"
    s = " ".join(s.split())
    return s[:massimo] + ("..." if len(s) > massimo else "")


def e_quota(e) -> bool:
    s = str(e)
    return "RESOURCE_EXHAUSTED" in s or s.startswith("429") or " 429 " in s
