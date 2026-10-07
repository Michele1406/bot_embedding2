# -*- coding: utf-8 -*-
"""
Pulizia del testo generato dal modello (markdown leggero adatto a web e WhatsApp). Un solo posto, con test.

Prima le stesse ~15 regex erano copiate in due punti di app.py e una di queste, `\\*\\*\\s*\\*\\*` (togliere il grassetto
vuoto), con `\\s` catturava anche l'a-capo: "**Taralli Caserecci**" + a capo + "**2. Olive**" diventava
"**Taralli Caserecci2. Olive**" e il guardrail cancellava le righe credendolo un prodotto inventato (chat reale).
Regola: nessuna sostituzione puo' attraversare un a-capo, salvo quelle che riducono le righe vuote.
"""
import re

_SOSTITUZIONI = [
    (re.compile(r"\b[Ss]ottofondo\b"), "sottovuoto"),
    (re.compile(r"PRODOTTI[ \t]+SOFOUND", re.IGNORECASE), "PRODOTTI SOFOOD"),
    (re.compile(r"SOFOUND"), "SOFOOD"),
    # titoli markdown "## Titolo" -> **Titolo**
    (re.compile(r"^[ \t]*#{1,6}[ \t]*(.+)$", re.MULTILINE), r"**\1**"),
    # "- ***Nome**" e simili: asterischi in eccesso dopo il punto elenco
    (re.compile(r"^([ \t]*[\*\-][ \t]*)\*{3,}", re.MULTILINE), r"\1**"),
    (re.compile(r"\*{4,}"), "**"),
    # grassetto vuoto "** **" ISOLATO sulla stessa riga (mai tra due righe)
    (re.compile(r"(?<!\S)\*\*[ \t]*\*\*(?!\S)"), ""),
    # "**Produttore - Nome**" -> "**Nome**" (il produttore va fuori dal grassetto, il nome resta verificabile)
    (re.compile(r"\*\*([^\n*]{1,40}?)[ \t]+-[ \t]+([^\n*]+?)\*\*"), r"**\2**"),
    # termini di magazzino dentro il nome in grassetto
    (re.compile(r"(\*\*[^*\n]*?)[ \t]*\b(?:[Ss]ottovuoto|[Ss]/[Vv]|[Aa][Tt][Mm]|[Ss]/[Oo])\b[ \t]*([^*\n]*?\*\*)"), r"\1\2"),
    (re.compile(r"\*\*[ \t]*([^*\n]+?)[ \t]*\*\*"), r"**\1**"),
    # gergo interno che non deve arrivare al cliente
    (re.compile(r"\bnel[ \t]+catalogo[ \t]+di[ \t]+questo[ \t]+turno\b", re.IGNORECASE), "a catalogo"),
    (re.compile(r"\bin[ \t]+questo[ \t]+turno\b", re.IGNORECASE), "al momento"),
    (re.compile(r"\bnel[ \t]+contesto[ \t]+(?:fornito|a[ \t]+disposizione)\b", re.IGNORECASE), "a catalogo"),
    (re.compile(r"\bdi[ \t]+questo[ \t]+turno\b", re.IGNORECASE), "del catalogo"),
    (re.compile(r"\n{3,}"), "\n\n"),
]


def pulisci_markdown(testo: str) -> str:
    t = testo or ""
    for rx, sost in _SOSTITUZIONI:
        t = rx.sub(sost, t)
    return t


def pulisci_chunk(testo: str) -> str:
    """Correzioni sicure anche su un pezzo di streaming (nessuna regola che dipende dal contesto)."""
    return (testo or "").replace("sottofondo", "sottovuoto").replace("Sottofondo", "Sottovuoto").replace("SOFOUND", "SOFOOD")
