# -*- coding: utf-8 -*-
"""
Adattatore WhatsApp Cloud API (Meta) per Nino. SPENTO di default: si attiva con WHATSAPP_ENABLED=1.

Variabili d'ambiente (mai nel codice):
  WHATSAPP_ENABLED          1 per registrare gli endpoint
  WHATSAPP_VERIFY_TOKEN     token scelto da noi per la verifica del webhook (GET)
  WHATSAPP_APP_SECRET       segreto dell'app Meta: se presente, la firma X-Hub-Signature-256 e' OBBLIGATORIA
  WHATSAPP_ACCESS_TOKEN     token per inviare messaggi (Graph API)
  WHATSAPP_PHONE_NUMBER_ID  id del numero mittente
  WHATSAPP_API_VERSION      default v21.0

Flusso: Meta fa POST /webhook/whatsapp -> rispondiamo 200 subito (Meta ritenta se tardiamo) -> un thread
elabora il messaggio con Nino (sessione = numero del cliente, persistita su SQLite) -> risposta spezzata in
messaggi da <= 3500 caratteri e inviata con la Graph API. Gli id messaggio gia' visti si ignorano (Meta ritenta).

Vocali: scaricati dalla Graph API e trascritti con la funzione di Nino (se la trascrizione fallisce, invito a scrivere).
Limiti noti (v1): le immagini [IMG] di Nino non vengono inviate, nessuna finestra di 24 ore / template per messaggi iniziati da noi.
"""
import collections
import hashlib
import hmac
import json
import os
import re
import threading
import urllib.error
import urllib.request

MAX_CARATTERI = 3500
MSG_NON_SUPPORTATO = ("Per ora riesco a leggere solo messaggi di testo: puoi scrivermi cosa ti serve? "
                      "Ti aiuto volentieri con prodotti, proposte di menu e ordini.")
MSG_TROPPO_VELOCE = "Ricevo molti messaggi ravvicinati: dammi qualche istante e riscrivimi, cosi' ti rispondo con calma."
MSG_ERRORE = "Mi scuso, c'e' stato un problema tecnico. Puoi riscrivermi la richiesta tra qualche istante?"

from core.sicurezza import LimitatoreFrequenza  # noqa: E402

LIMITE_MESSAGGI = LimitatoreFrequenza(10, 60)   # al piu' 10 messaggi al minuto per numero
_AVVISATI: dict = {}                            # sid -> istante dell'ultimo avviso "troppo veloce"
_VISTI: "collections.OrderedDict" = collections.OrderedDict()
_VISTI_LOCK = threading.Lock()
_LOCK_CLIENTE: dict = {}
_LOCK_GLOBALE = threading.Lock()


# --------------------------------------------------------------------------- funzioni pure (testabili)
def verifica_firma(secret: str, corpo: bytes, intestazione: "str | None") -> bool:
    """Valida X-Hub-Signature-256 ('sha256=<hex>') con HMAC-SHA256 del corpo grezzo."""
    if not secret:
        return True  # firma non configurata: ammesso solo in sviluppo (in produzione impostare WHATSAPP_APP_SECRET)
    if not intestazione or not intestazione.startswith("sha256="):
        return False
    atteso = hmac.new(secret.encode("utf-8"), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(atteso, intestazione.split("=", 1)[1])


def estrai_messaggi(payload: dict) -> list:
    """[{id, da, tipo, testo}] dai messaggi in ingresso del payload Meta (ignora gli stati di consegna)."""
    out = []
    for entry in (payload or {}).get("entry", []) or []:
        for ch in entry.get("changes", []) or []:
            val = ch.get("value", {}) or {}
            for m in val.get("messages", []) or []:
                tipo = m.get("type")
                testo = ""
                if tipo == "text":
                    testo = (m.get("text") or {}).get("body", "")
                elif tipo == "interactive":
                    i = m.get("interactive") or {}
                    testo = ((i.get("button_reply") or i.get("list_reply") or {}).get("title")) or ""
                    tipo = "text" if testo else tipo
                elif tipo == "button":
                    testo = (m.get("button") or {}).get("text", "")
                    tipo = "text" if testo else tipo
                media = m.get("audio") or m.get("voice") or {}
                out.append({"id": m.get("id"), "da": m.get("from"), "tipo": tipo, "testo": (testo or "").strip(),
                            "media_id": media.get("id"), "mime": media.get("mime_type")})
    return out


def converti_markdown(testo: str) -> str:
    """Formattazione WhatsApp: **grassetto** -> *grassetto*, tag [IMG: ...] rimossi, a-capo multipli ridotti."""
    t = re.sub(r"\[IMG:\s*[^\]]+\]", "", testo or "")
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t, flags=re.DOTALL)
    t = re.sub(r"^\s*#{1,6}\s*(.+)$", r"*\1*", t, flags=re.MULTILINE)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def spezza_testo(testo: str, massimo: int = MAX_CARATTERI) -> list:
    """Divide in messaggi <= massimo caratteri tagliando su paragrafi, poi su righe, poi su spazi."""
    testo = (testo or "").strip()
    if not testo:
        return []
    if len(testo) <= massimo:
        return [testo]
    pezzi, corrente = [], ""
    for par in testo.split("\n\n"):
        for blocco in _taglia(par, massimo):
            if corrente and len(corrente) + 2 + len(blocco) > massimo:
                pezzi.append(corrente)
                corrente = blocco
            else:
                corrente = f"{corrente}\n\n{blocco}" if corrente else blocco
    if corrente:
        pezzi.append(corrente)
    return pezzi


def _taglia(par: str, massimo: int) -> list:
    if len(par) <= massimo:
        return [par]
    out, cur = [], ""
    for riga in par.split("\n"):
        while len(riga) > massimo:
            spazio = riga.rfind(" ", 0, massimo)
            cut = spazio if spazio > massimo // 2 else massimo
            if cur:
                out.append(cur)
                cur = ""
            out.append(riga[:cut].rstrip())
            riga = riga[cut:].lstrip()
        if cur and len(cur) + 1 + len(riga) > massimo:
            out.append(cur)
            cur = riga
        else:
            cur = f"{cur}\n{riga}" if cur else riga
    if cur:
        out.append(cur)
    return out


def sid_da_numero(numero: str) -> str:
    """Identificativo di sessione stabile per numero, senza salvare il numero in chiaro nei log (hash)."""
    return "wa_" + hashlib.sha256(str(numero).encode("utf-8")).hexdigest()[:24]


def gia_visto(id_msg: "str | None", massimo: int = 2000) -> bool:
    """True se l'id messaggio e' gia' stato elaborato (Meta ritenta le consegne); registra l'id."""
    if not id_msg:
        return False
    with _VISTI_LOCK:
        if id_msg in _VISTI:
            return True
        _VISTI[id_msg] = True
        while len(_VISTI) > massimo:
            _VISTI.popitem(last=False)
    return False


# --------------------------------------------------------------------------- invio
def invia_testo(numero: str, testo: str, token: str, phone_id: str, versione: str = "v21.0", timeout: int = 15) -> bool:
    url = f"https://graph.facebook.com/{versione}/{phone_id}/messages"
    corpo = json.dumps({"messaging_product": "whatsapp", "to": numero, "type": "text",
                        "text": {"preview_url": False, "body": testo}}).encode("utf-8")
    req = urllib.request.Request(url, data=corpo, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"[WHATSAPP] invio fallito: {str(e)[:150]}")
        return False


def scarica_media(media_id: str, token: str, versione: str = "v21.0", timeout: int = 20, massimo: int = 5_000_000):
    """Scarica un file (vocale) dalla Graph API: ritorna (bytes, mime) oppure (None, None). Max 5 MB."""
    try:
        req = urllib.request.Request(f"https://graph.facebook.com/{versione}/{media_id}",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8"))
        url = info.get("url")
        if not url or int(info.get("file_size") or 0) > massimo:
            return None, None
        req2 = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req2, timeout=timeout) as r:
            dati = r.read(massimo + 1)
        return (dati, info.get("mime_type")) if len(dati) <= massimo else (None, None)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"[WHATSAPP] download media fallito: {str(e)[:150]}")
        return None, None


def _lock_cliente(sid: str) -> threading.Lock:
    with _LOCK_GLOBALE:
        return _LOCK_CLIENTE.setdefault(sid, threading.Lock())


def gestisci_messaggio(msg: dict, elabora, stato_per_sid, invia, trascrivi=None) -> None:
    """Elabora UN messaggio in ingresso. `elabora(testo, stato, sid) -> {"reply": str}`, `stato_per_sid(sid) -> stato`,
    `invia(numero, testo) -> bool` sono iniettati (cosi' si testa senza rete ne' modelli).
    `trascrivi(msg) -> str|None` (opzionale) trasforma un vocale in testo."""
    numero, sid = msg["da"], sid_da_numero(msg["da"])
    if msg["tipo"] == "audio" and trascrivi is not None and msg.get("media_id"):
        try:
            testo_vocale = (trascrivi(msg) or "").strip()
        except Exception as e:
            print(f"[WHATSAPP] trascrizione fallita: {str(e)[:150]}")
            testo_vocale = ""
        if testo_vocale:
            msg = dict(msg, tipo="text", testo=testo_vocale)
    if msg["tipo"] != "text" or not msg["testo"]:
        invia(numero, MSG_NON_SUPPORTATO)
        return
    if not LIMITE_MESSAGGI.consenti(sid):
        import time
        if time.time() - _AVVISATI.get(sid, 0) > 60:   # un solo avviso al minuto, poi si ignora
            _AVVISATI[sid] = time.time()
            invia(numero, MSG_TROPPO_VELOCE)
        return
    with _lock_cliente(sid):  # un messaggio alla volta per cliente: lo stato di sessione non e' thread-safe
        try:
            risposta = elabora(msg["testo"][:2000], stato_per_sid(sid), sid).get("reply", "")
        except Exception as e:
            print(f"[WHATSAPP] errore elaborazione: {str(e)[:200]}")
            risposta = MSG_ERRORE
        for parte in spezza_testo(converti_markdown(risposta)):
            invia(numero, parte)


# --------------------------------------------------------------------------- registrazione su Flask
def registra_whatsapp(app, elabora, stato_per_sid, trascrivi_audio=None) -> bool:
    """Registra GET/POST /webhook/whatsapp su `app` se WHATSAPP_ENABLED=1. Ritorna True se registrato."""
    if os.getenv("WHATSAPP_ENABLED", "0") != "1":
        return False
    from flask import request

    verify = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    secret = os.getenv("WHATSAPP_APP_SECRET", "")
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    versione = os.getenv("WHATSAPP_API_VERSION", "v21.0")
    if not (verify and token and phone_id):
        print("[WHATSAPP] ATTENZIONE: WHATSAPP_VERIFY_TOKEN / ACCESS_TOKEN / PHONE_NUMBER_ID mancanti, endpoint non registrati")
        return False
    if not secret:
        print("[WHATSAPP] ATTENZIONE: WHATSAPP_APP_SECRET non impostato: le richieste NON sono firmate (solo sviluppo!)")

    def invia(numero, testo):
        return invia_testo(numero, testo, token, phone_id, versione)

    def trascrivi(msg):
        """Vocale -> testo con la funzione di trascrizione di Nino (trascrivi_audio(bytes, mime) -> str)."""
        if trascrivi_audio is None:
            return None
        dati, mime = scarica_media(msg["media_id"], token, versione)
        return trascrivi_audio(dati, mime or msg.get("mime") or "audio/ogg") if dati else None

    @app.route("/webhook/whatsapp", methods=["GET"])
    def wa_verifica():
        if request.args.get("hub.mode") == "subscribe" and hmac.compare_digest(
                request.args.get("hub.verify_token", ""), verify):
            return request.args.get("hub.challenge", ""), 200
        return "Forbidden", 403

    @app.route("/webhook/whatsapp", methods=["POST"])
    def wa_ricevi():
        corpo = request.get_data()
        if not verifica_firma(secret, corpo, request.headers.get("X-Hub-Signature-256")):
            return "Firma non valida", 403
        try:
            payload = json.loads(corpo.decode("utf-8"))
        except ValueError:
            return "Bad request", 400
        for msg in estrai_messaggi(payload):
            if gia_visto(msg["id"]):
                continue
            threading.Thread(target=gestisci_messaggio, args=(msg, elabora, stato_per_sid, invia, trascrivi), daemon=True).start()
        return "OK", 200

    print("[WHATSAPP] webhook registrato su /webhook/whatsapp")
    return True
