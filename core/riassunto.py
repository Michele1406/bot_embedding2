# -*- coding: utf-8 -*-
"""
Riassunto rolling della conversazione.

Nino tiene in chiaro solo gli ultimi N scambi; prima i piu' vecchi venivano semplicemente scartati (il cliente
aveva gia' detto "no ai fritti", "voglio 3 formaggi", "locale a Bari" e Nino lo dimenticava). Profilo, dieta ed
esclusioni sono gia' persistenti; qui si conserva il RESTO: cosa e' stato scelto, scartato, chiesto.

  aggiorna(client, modello, precedente, messaggi) -> nuovo riassunto (max ~900 caratteri)

Una chiamata al modello LITE ogni qualche turno (solo quando lo storico supera la soglia), temperatura 0, regola
ferrea "solo fatti presenti nei messaggi". Se la chiamata fallisce si tiene il riassunto precedente.
"""

MAX_CARATTERI = 900

ISTRUZIONI = """Aggiorna il riassunto di una conversazione commerciale tra un cliente HORECA e l'assistente Nino (So Food).
Scrivi SOLO fatti presenti nel riassunto precedente o nei nuovi messaggi, niente deduzioni, niente prezzi.
Conserva: richieste fatte e non ancora concluse, prodotti/ricette scelti o confermati dal cliente, prodotti o idee
scartati e perche', quantita', vincoli (dieta, allergie, budget, giorni di consegna), domande lasciate aperte.
Scarta convenevoli e dettagli superati. Massimo 8 punti elenco brevi, in italiano. Rispondi solo con l'elenco."""


def testo_da_contents(contents) -> str:
    """Converte i messaggi dello storico (types.Content di google-genai o dict) in testo 'Ruolo: testo'."""
    righe = []
    for c in contents or []:
        ruolo = getattr(c, "role", None) or (c.get("role") if isinstance(c, dict) else "")
        parti = getattr(c, "parts", None) or (c.get("parts") if isinstance(c, dict) else [])
        testo = " ".join((getattr(p, "text", None) or (p.get("text") if isinstance(p, dict) else "") or "") for p in parti).strip()
        if testo:
            righe.append(f"{'Cliente' if ruolo == 'user' else 'Nino'}: {testo[:700]}")
    return "\n".join(righe)


def aggiorna(client, modello: str, precedente: str, messaggi) -> str:
    nuovi = testo_da_contents(messaggi) if not isinstance(messaggi, str) else messaggi
    if not nuovi.strip():
        return precedente or ""
    prompt = (f"{ISTRUZIONI}\n\n[RIASSUNTO PRECEDENTE]\n{precedente or '(vuoto)'}\n\n[NUOVI MESSAGGI]\n{nuovi}\n\n[RIASSUNTO AGGIORNATO]")
    try:
        from google.genai import types
        r = client.models.generate_content(model=modello, contents=prompt,
                                           config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=400))
        testo = (r.text or "").strip()
        return testo[:MAX_CARATTERI] if testo else (precedente or "")
    except Exception as e:
        print(f"[RIASSUNTO] errore (si tiene il precedente): {str(e)[:120]}")
        return precedente or ""


def blocco_prompt(riassunto: str) -> str:
    if not riassunto:
        return ""
    return ("\n    [RIASSUNTO DELLA CONVERSAZIONE PRECEDENTE (messaggi piu' vecchi non piu' visibili: usalo per non far ripetere "
            "al cliente quello che ha gia' detto)]\n    " + riassunto.replace("\n", "\n    ") + "\n")
