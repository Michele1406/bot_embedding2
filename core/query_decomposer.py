# -*- coding: utf-8 -*-
import json
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
from core.agent_topology import IntentClassification, DecomposedTree, VerificationResult

load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")
client_genai = genai.Client(api_key=api_key)

MODELLO_BACKGROUND = "gemini-3.5-flash-lite"

def classify_intent(user_query: str, contesto: str = "") -> IntentClassification:
    prompt = f"""Sei il NODO 1: Intent & State Classifier.
Analizza l'ultima richiesta dell'utente nel contesto della conversazione e classificala secondo lo schema richiesto.

CONTESTO:
{contesto}

RICHIESTA UTENTE:
{user_query}
"""
    try:
        risposta = client_genai.models.generate_content(
            model=MODELLO_BACKGROUND,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=IntentClassification,
            )
        )
        return IntentClassification.model_validate_json(risposta.text)
    except Exception as e:
        print(f"[ATTENZIONE] Fallita classificazione intent: {e}")
        return IntentClassification(
            intent="RICERCA_CATALOGO",
            confidence=0.5,
            requires_tree_search=False
        )

def decompose_domain(user_query: str, contesto: str = "") -> DecomposedTree:
    prompt = f"""Sei il NODO 2: Domain Decomposer & Attribute Extractor.
Il tuo compito è trasformare la richiesta dell'utente in un albero di requisiti (Slot) per la composizione di un tagliere o piatto.

REGOLE CRUCIALI (PENA IL FALLIMENTO DEL SISTEMA):
1. DEVI SPEZZARE richieste miste nello STESSO ruolo. Se l'utente chiede "3 formaggi di cui 1 erborinato e 2 freschi", DEVI CREARE DUE SLOT DISTINTI:
   - Slot 1: quantity=1, forced_subcategory="erborinato", macro_family="FORMAGGI"
   - Slot 2: quantity=2, forced_subcategory="freschi", macro_family="FORMAGGI"
   MAI accorpare attributi forzati con quantità miste.

2. `forced_subcategory` DEVE essere una stringa semplice e categorica (es. "erborinato", "crudo", "cotto", "macinato", "pecorino").

3. `must_not_have` contiene gli ingredienti esplicitamente vietati.

CONTESTO:
{contesto}

RICHIESTA UTENTE:
{user_query}
"""
    try:
        risposta = client_genai.models.generate_content(
            model=MODELLO_BACKGROUND,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=DecomposedTree,
            )
        )
        return DecomposedTree.model_validate_json(risposta.text)
    except Exception as e:
        print(f"[ATTENZIONE] Fallita decomposizione dominio: {e}")
        from core.agent_topology import ComponentSlot, SlotConstraints
        return DecomposedTree(
            slots=[
                ComponentSlot(
                    node_id="fallback_1",
                    macro_family="RICERCA",
                    quantity=1,
                    constraints=SlotConstraints()
                )
            ]
        )

