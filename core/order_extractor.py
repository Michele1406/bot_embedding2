# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field, field_validator
from google.genai import types


class ProdottoOrdine(BaseModel):
    codice_articolo: str | None = Field(default=None, description="Codice articolo se presente, altrimenti null")
    nome_prodotto: str = Field(description="Nome esatto del prodotto come scritto da Nino")
    fornitore: str | None = Field(default=None, description="Nome del produttore se indicato")
    quantita: int = Field(default=1, description="Quantita' richiesta, default 1")
    unita: str | None = Field(default=None, description="Unita' della quantita': cartoni, colli, confezioni, pezzi, kg, forme... null se il cliente non l'ha detta")
    note: str | None = Field(default=None, description="Note della riga (es. 'affettato', 'formato grande'), null se assenti")


class CheckoutOrdine(BaseModel):
    ragione_sociale: str | None = Field(default=None, description="Ragione sociale dell'azienda")
    partita_iva: str | None = Field(default=None, description="Partita IVA fornita (deve essere di 11 cifre numeriche)")
    referente: str | None = Field(default=None, description="Nome della persona di riferimento, se detto")
    telefono: str | None = Field(default=None, description="Telefono indicato dal cliente, se detto")
    email: str | None = Field(default=None, description="Email indicata dal cliente, se detta")
    indirizzo_consegna: str | None = Field(default=None, description="Indirizzo di consegna, se detto")
    data_consegna: str | None = Field(default=None, description="Data o giorno di consegna richiesto, se detto")
    note: str | None = Field(default=None, description="Altre note sull'ordine, se presenti")
    prodotti: list[ProdottoOrdine] = Field(default_factory=list, description="Lista dei prodotti confermati")

    @field_validator('partita_iva', mode='before')
    @classmethod
    def process_piva(cls, v):
        if not v:
            return None
        v = ''.join(filter(str.isdigit, str(v)))
        return v if len(v) == 11 else None


ISTRUZIONI = '''
Leggi la cronologia della chat tra un cliente HORECA e l'assistente Nino (So Food).
Estrai SOLO dati scritti nella chat, senza dedurre nulla:
1. I prodotti che il cliente ha ESPLICITAMENTE deciso di ordinare, con quantita' e unita' (cartoni, kg, pezzi...).
   Se un prodotto e' stato solo proposto da Nino ma non scelto dal cliente, NON includerlo.
   Se il cliente ha tolto o cambiato un prodotto, vale l'ultima versione.
2. Ragione sociale, Partita IVA, referente, telefono, email, indirizzo e data di consegna, note: solo se scritti dal cliente.
Usa per i prodotti il nome esatto come compare nei messaggi di Nino (e il codice articolo se presente).
'''


def estrai_ordine_da_chat(client_genai, storico_messaggi: list, model_name: str, riassunto: str = "") -> CheckoutOrdine:
    righe_chat = []
    for m in storico_messaggi:
        # Supporto polimorfico: sia tipi types.Content che dizionari
        if isinstance(m, dict):
            raw_role = m.get("role") or "unknown"
            raw_parts = m.get("parts") or []
        else:
            raw_role = getattr(m, "role", None) or "unknown"
            raw_parts = getattr(m, "parts", None) or []
        ruolo = "CLIENTE" if str(raw_role) == "user" else "NINO"
        frammenti = []
        for p in raw_parts:
            if isinstance(p, str):
                frammenti.append(p)
            elif hasattr(p, "text") and p.text:
                frammenti.append(str(p.text))
        testo = " ".join(frammenti).strip()
        if testo:
            righe_chat.append(f"{ruolo}: {testo}")

    chat_text = "\n".join(righe_chat)
    contesto = f"\n\nRIASSUNTO DEI MESSAGGI PIU' VECCHI:\n{riassunto}" if riassunto else ""
    full_prompt = ISTRUZIONI + contesto + "\n\nCRONOLOGIA CHAT:\n" + chat_text[-12000:]

    try:
        risposta = client_genai.models.generate_content(
            model=model_name,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=CheckoutOrdine,
            )
        )
        testo_json = (risposta.text or "").strip()
        # Estrai il blocco JSON puro anche in presenza di markdown o caratteri spuri
        start_idx = testo_json.find("{")
        end_idx = testo_json.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            testo_json = testo_json[start_idx:end_idx + 1]

        return CheckoutOrdine.model_validate_json(testo_json)
    except Exception as e:
        from core.errori import breve
        print(f"[ORDINE] estrazione fallita: {breve(e)}")
        return CheckoutOrdine(prodotti=[])
