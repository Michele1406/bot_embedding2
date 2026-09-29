# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field
from google.genai import types

class ProdottoOrdine(BaseModel):
    codice_articolo: str | None = Field(description="Codice articolo se presente, altrimenti null")
    nome_prodotto: str = Field(description="Nome esatto del prodotto")
    fornitore: str | None = Field(description="Nome del fornitore se specificato")
    quantita: int = Field(default=1, description="Quantita richiesta, default 1")

class CheckoutOrdine(BaseModel):
    ragione_sociale: str | None = Field(description="Ragione sociale dell'azienda")
    partita_iva: str | None = Field(description="Partita IVA fornita")
    prodotti: list[ProdottoOrdine] = Field(description="Lista dei prodotti confermati")

def estrai_ordine_da_chat(client_genai, storico_messaggi: list, model_name: str) -> CheckoutOrdine:
    prompt = '''
Leggi attentamente la cronologia della chat allegata.
Il cliente e l'assistente (Nino) hanno appena concluso o stanno concludendo un ordine.
Estrai in modo preciso:
1. Tutti i prodotti che il cliente ha ESPLICITAMENTE deciso di acquistare o confermare. 
2. La ragione sociale del cliente (se fornita nelle ultime battute).
3. La Partita IVA del cliente (se fornita).

Se un prodotto e' stato solo proposto dall'assistente ma non confermato dal cliente, NON INCLUDERLO.
'''
    righe_chat = []
    for m in storico_messaggi:
        # Supporto polimorfico: sia tipi types.Content che dizionari
        if isinstance(m, dict):
            raw_role = m.get("role") or "unknown"
            raw_parts = m.get("parts") or []
        else:
            raw_role = getattr(m, "role", None) or "unknown"
            raw_parts = getattr(m, "parts", None) or []

        ruolo = str(raw_role).upper()
        
        # Estrai testo gestendo oggetti Part, stringhe e messaggi multi-part
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
    full_prompt = prompt + "\n\nCRONOLOGIA CHAT:\n" + chat_text[-4000:] 
    
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
        print(f"Errore in estrazione ordine: {e}")
        return CheckoutOrdine(ragione_sociale=None, partita_iva=None, prodotti=[])
