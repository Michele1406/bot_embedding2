# -*- coding: utf-8 -*-
"""
Ordini in DUE passi: riepilogo -> conferma esplicita del cliente -> salvataggio (data/ordini/*.json, tutte le info).

Prima l'ordine partiva appena il modello classificava il messaggio come "chiusura_ordine", senza riepilogo da
confermare; un secondo "confermo" lo reinviava; nel file mancavano unita' di misura, contatti e conversazione.

  prepara(estratto, indice_testuale, stato)  -> (bozza | None, messaggio_per_il_cliente)
  e_conferma(testo) / e_annullamento(testo)  -> riconoscono "CONFERMO" / "annulla" (senza chiamate LLM)
  registra(bozza, stato, sid)                -> (ok, messaggio, id_ordine) salva il JSON (o lo invia a ERP_WEBHOOK_URL)

La bozza vive in stato["ordine_in_attesa"]; le impronte degli ordini registrati in stato["ordini_registrati"].
"""
import datetime
import hashlib
import json
import os
import re

from core.ordine_validazione import piva_valida, valida_righe

_RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RE_CONFERMA = re.compile(r"^\W*(confermo|conferma|confermato|s[iì]\W*confermo|ok\W*confermo|procedi|invia(lo)?|"
                          r"s[iì]\W*(invia|procedi|va bene)|va bene\W*(invia|procedi)?)\W*$", re.IGNORECASE)
_RE_ANNULLA = re.compile(r"\b(annulla|annullare|lascia (stare|perdere)|non (lo )?inviare|cancella l'ordine|stop)\b",
                         re.IGNORECASE)
UNITA_AMMESSE = ("cartoni", "colli", "confezioni", "pezzi", "kg", "forme", "vaschette", "bottiglie", "secchielli")


def e_conferma(testo: str) -> bool:
    return bool(_RE_CONFERMA.match((testo or "").strip()))


def e_annullamento(testo: str) -> bool:
    return bool(_RE_ANNULLA.search(testo or ""))


def _unita(u: "str | None") -> str:
    u = (u or "").strip().lower()
    for a in UNITA_AMMESSE:
        if u.startswith(a[:4]):
            return a
    return {"kg.": "kg", "chili": "kg", "chilogrammi": "kg", "pz": "pezzi", "conf": "confezioni"}.get(u, u or "da definire")


def impronta(righe: list, piva: str) -> str:
    base = json.dumps(sorted((r["codice_prodotto"], r["quantita"], r.get("unita", "")) for r in righe)) + str(piva)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def prepara(estratto, indice_testuale: list, stato: dict) -> tuple:
    """Valida l'ordine estratto dalla chat e prepara la bozza da far confermare. Ritorna (bozza|None, messaggio)."""
    if not estratto or not estratto.prodotti:
        return None, "Non trovo prodotti da ordinare nella nostra conversazione: quali prodotti e quantita' vuoi ordinare?"
    if not estratto.partita_iva:
        return None, ("Per registrare l'ordine mi serve la **Partita IVA** (11 cifre) e la ragione sociale. "
                      "Puoi scrivermele?")
    if not piva_valida(estratto.partita_iva):
        return None, "La Partita IVA che mi hai indicato non risulta valida (controllo sulle cifre): puoi ricontrollarla?"
    prodotti = [p.model_dump() for p in estratto.prodotti]
    righe, scartate = valida_righe(prodotti, indice_testuale)
    if scartate:
        elenco = "; ".join(f"{n or 'riga senza nome'} ({m})" for n, m in scartate)
        return None, (f"Prima di preparare l'ordine devo chiarire alcune righe: {elenco}. "
                      "Puoi indicarmi il prodotto esatto (nome o codice), oppure dirmi se le tolgo?")
    from core import logistica
    meta_per_id = {p["id"]: p for p in indice_testuale}
    for r, p in zip(righe, prodotti):
        r["unita"] = _unita(p.get("unita"))
        r["note"] = (p.get("note") or "").strip()
        rec = meta_per_id.get(r["id_catalogo"])
        if rec:
            from core.parse_formato import formato_prodotto
            f = formato_prodotto(rec["metadata"], rec.get("document") or "")
            r["formato"] = (f"{f['valore']:g} {f['unita']}" if f.get("valore") else "") + (" (peso variabile)" if f.get("peso_variabile") else "")
            r["temperatura"] = rec["metadata"].get("temperatura") or logistica.classe_temperatura(rec["metadata"], rec.get("document") or "")
    avvisi = []
    if stato.get("zona_consegna") == "fuori" and any(r.get("temperatura") not in (None, "ambiente") for r in righe):
        avvisi.append("Alcuni prodotti richiedono il freddo e il cliente risulta fuori dalla zona refrigerata: da verificare.")
    if any(r["unita"] == "da definire" for r in righe):
        avvisi.append("Unita' di misura non indicata per alcune righe: il commerciale la conferma con il cliente.")
    cliente = {
        "ragione_sociale": estratto.ragione_sociale or "", "partita_iva": estratto.partita_iva,
        "referente": getattr(estratto, "referente", None) or "", "telefono": getattr(estratto, "telefono", None) or "",
        "email": getattr(estratto, "email", None) or "", "indirizzo_consegna": getattr(estratto, "indirizzo_consegna", None) or "",
        "citta": stato.get("citta") or "", "zona_consegna": stato.get("zona_consegna") or "",
        "tipo_locale": stato.get("tipo_locale") or "", "canale": stato.get("canale_locale") or "",
    }
    bozza = {"righe": righe, "cliente": cliente, "data_consegna_richiesta": getattr(estratto, "data_consegna", None) or "",
             "note": getattr(estratto, "note", None) or "", "avvisi": avvisi,
             "impronta": impronta(righe, estratto.partita_iva)}
    gia = (stato.get("ordini_registrati") or {}).get(bozza["impronta"])
    if gia:
        return None, f"Questo ordine e' gia' stato registrato (n. {gia}): se vuoi modificarlo dimmi cosa cambiare."
    return bozza, riepilogo(bozza)


def riepilogo(bozza: dict) -> str:
    c = bozza["cliente"]
    righe = "\n".join(f"- {r['quantita']} {r['unita']} x **{r['nome']}** ({r['fornitore']}, cod. {r['codice_prodotto']}"
                      + (f", {r['formato']}" if r.get("formato") else "") + ")" + (f" - {r['note']}" if r.get("note") else "")
                      for r in bozza["righe"])
    testa = f"Riepilogo ordine per {c['ragione_sociale'] or 'cliente'} (P.IVA {c['partita_iva']}):"
    extra = []
    if bozza.get("data_consegna_richiesta"):
        extra.append(f"Consegna richiesta: {bozza['data_consegna_richiesta']}")
    if c.get("indirizzo_consegna"):
        extra.append(f"Indirizzo: {c['indirizzo_consegna']}")
    for a in bozza.get("avvisi", []):
        extra.append(f"Nota: {a}")
    return (testa + "\n" + righe + ("\n" + "\n".join(extra) if extra else "")
            + "\n\nI prezzi e la conferma definitiva te li manda il commerciale. Se e' tutto corretto scrivi **CONFERMO**; "
              "altrimenti dimmi cosa cambiare.")


def _cartella() -> str:
    return os.getenv("ORDINI_DIR") or os.path.join(_RADICE, "data", "ordini")


def registra(bozza: dict, stato: dict, sid: str, conversazione: "list | None" = None) -> tuple:
    """Salva l'ordine confermato in data/ordini/<id>.json (o lo invia a ERP_WEBHOOK_URL). Ritorna (ok, messaggio, id)."""
    ora = datetime.datetime.now()
    id_ordine = f"{ora.strftime('%Y%m%d-%H%M%S')}-{bozza['impronta'][:6]}"
    payload = {
        "id_ordine": id_ordine, "stato": "da_confermare_commerciale", "creato_il": ora.isoformat(timespec="seconds"),
        "canale_contatto": "whatsapp" if str(sid).startswith("wa_") else "web", "sessione": str(sid),
        "contatto_whatsapp": stato.get("numero_whatsapp") or "",
        "cliente": bozza["cliente"], "righe": bozza["righe"], "data_consegna_richiesta": bozza.get("data_consegna_richiesta", ""),
        "note": bozza.get("note", ""), "avvisi": bozza.get("avvisi", []), "impronta": bozza["impronta"],
        "riepilogo_confermato": riepilogo(bozza), "conversazione": (conversazione or [])[-30:],
    }
    url = os.getenv("ERP_WEBHOOK_URL", "").strip()
    try:
        if url:
            import requests
            requests.post(url, json=payload, timeout=10).raise_for_status()
        else:
            os.makedirs(_cartella(), exist_ok=True)
            with open(os.path.join(_cartella(), f"ordine_{id_ordine}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        from core.errori import breve
        print(f"[ORDINE] registrazione fallita: {breve(e)}")
        return False, "Non sono riuscito a registrare l'ordine: riprova tra qualche istante.", None
    stato.setdefault("ordini_registrati", {})[bozza["impronta"]] = id_ordine
    return True, (f"Ordine registrato (n. {id_ordine}). Il commerciale ti contattera' per la conferma con prezzi e data "
                  "di consegna."), id_ordine
