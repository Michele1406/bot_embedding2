# -*- coding: utf-8 -*-
"""
Quando mostrare le foto dei prodotti, e di quali prodotti si sta parlando (prodotti "in primo piano").

Regola di sicurezza: una foto si mostra SOLO se il prodotto e' identificato senza ambiguita' (citazione verificata dal
guardrail con un solo candidato a catalogo) e il file esiste davvero. Non tutti i prodotti hanno la foto.

Si mostra quando serve al cliente:
  - la chiede ("foto", "immagine", "mi fai vedere", "com'e' fatto", "che aspetto ha");
  - e' INCERTO su un prodotto ("non so", "non sono sicuro", "cos'e'", "non lo conosco", "mi convince");
  - chiede informazioni su un prodotto preciso (la risposta parla di 1-2 prodotti identificati).
Mai in risposte con tanti prodotti (proposte, elenchi): diventerebbe rumore.

  vuole_foto(testo) / incerto(testo) / anaforico(testo)
  percorso(meta)                       -> percorso del file o None
  scegli(testo, certi, per_id, info_su_prodotto) -> id dei prodotti di cui mostrare la foto (max 2)
  inserisci(testo, certi, ids, per_id) -> testo con [IMG: percorso] sotto la riga che cita il prodotto
"""
import os
import re

_RE_FOTO = re.compile(r"\b(foto\w*|immagin\w*|vedere|vederl[oaie]|mostra\w*|fammel[oa]|aspetto|com'?\s?[eè] fatt[oa]|"
                      r"come si presenta)\b", re.IGNORECASE)
_RE_INCERTO = re.compile(r"\b(non so|non sono sicur\w*|indecis\w*|dubbi\w*|non (lo|la|li|le) conosco|non conosco|"
                         r"cos'?\s?[eè]|che cos'?\s?[eè]|com'?\s?[eè]|mi convinc\w*|boh|forse|non ho capito|che roba)\b",
                         re.IGNORECASE)
_RE_ANAFORA = re.compile(r"\b(quest[oaie]|quell[oaie]|di cui (mi )?parli|che (mi )?hai (detto|proposto|consigliato|citato)|"
                         r"l'ultim[oa]|il primo|il secondo|la prima|la seconda|ce l'hai|hai (una |un'|la )?(foto|immagine))\b",
                         re.IGNORECASE)
MAX_FOTO = 2


def vuole_foto(testo: str) -> bool:
    return bool(_RE_FOTO.search(testo or ""))


def incerto(testo: str) -> bool:
    return bool(_RE_INCERTO.search(testo or ""))


def anaforico(testo: str) -> bool:
    """Il messaggio si riferisce a prodotti gia' nominati ("questa pancetta", "hai un'immagine?")."""
    return bool(_RE_ANAFORA.search(testo or "")) or (vuole_foto(testo) and len((testo or "").split()) <= 6)


def percorso(meta: dict) -> "str | None":
    p = str((meta or {}).get("percorso_immagine") or "").strip()
    if not meta or not meta.get("ha_immagine_primaria") or not p or p.lower() in ("nan", "none", "false"):
        return None
    if not p.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")) or not os.path.exists(p):
        return None
    return p


def scegli(testo: str, certi: list, per_id: dict, info_su_prodotto: bool = False, preferiti: "list | None" = None) -> list:
    """Id dei prodotti di cui mostrare la foto. `certi` = [(citazione, id)] identificati senza ambiguita' nella risposta;
    `info_su_prodotto` = il turno riguarda uno o pochi prodotti precisi; `preferiti` = i prodotti di cui il cliente
    sta parlando (se noti, la foto e' solo la loro: mai quella dell'abbinamento suggerito, chat reale)."""
    ids = list(dict.fromkeys(i for _c, i in certi))
    if preferiti:
        ids = [i for i in ids if i in set(preferiti)] or [i for i in preferiti if i in per_id]
    else:
        # prima i prodotti il cui nome ha parole in comune con il messaggio ("questa pancetta" -> la pancetta)
        parole = {w[:5] for w in re.findall(r"[a-zàèéìòù]+", (testo or "").lower()) if len(w) >= 4}
        cit = {i: c for c, i in certi}
        ids.sort(key=lambda i: -len(parole & {w[:5] for w in re.findall(r"[a-zàèéìòù]+", cit.get(i, "").lower()) if len(w) >= 4}))
    con_foto = [i for i in ids if i in per_id and percorso(per_id[i]["metadata"])]
    if not con_foto:
        return []
    quante = MAX_FOTO if re.search(r"\b(le foto|immagini|foto di tutt)", testo or "", re.IGNORECASE) else 1
    if vuole_foto(testo):
        return con_foto[:quante]
    if (incerto(testo) or info_su_prodotto) and len(ids) <= 2:
        return con_foto[:1]
    return []


def inserisci(testo: str, certi: list, ids: list, per_id: dict) -> str:
    """Mette [IMG: percorso] subito sotto la prima riga che cita in grassetto ciascun prodotto scelto."""
    if not ids:
        return testo
    citazione = {}
    for c, i in certi:
        citazione.setdefault(i, c)
    righe = (testo or "").split("\n")
    for i in ids:
        c = citazione.get(i)
        p = percorso(per_id[i]["metadata"]) if i in per_id else None
        if not c or not p:
            continue
        for k, r in enumerate(righe):
            if f"**{c}**" in r:
                righe.insert(k + 1, f"[IMG: {p}]")
                break
        else:
            righe.append(f"[IMG: {p}]")
    return "\n".join(righe)
