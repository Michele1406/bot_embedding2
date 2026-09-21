# -*- coding: utf-8 -*-
"""
agent_topology.py
=================
Schemi Pydantic / TypedDict per lo scambio dati tipizzato tra i Nodi
della pipeline multi-agente AntiGravity.
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field

# -------------------------------------------------------------------------
# NODO 1: Intent & State Classifier
# -------------------------------------------------------------------------
class IntentClassification(BaseModel):
    intent: Literal["TAGLIERE_O_RICETTA", "RICERCA_CATALOGO", "CONVERSAZIONE", "STOCK_REPLACEMENT"] = Field(
        ..., description="La tipologia principale di richiesta."
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    requires_tree_search: bool = Field(
        ..., description="True se è una richiesta custom fuori ricettario standard (es. fammi un tagliere a modo mio)."
    )
    user_context: Optional[str] = Field(None, description="Contesto opzionale dedotto (es. 'Vuole roba piccante').")

# -------------------------------------------------------------------------
# NODO 2: Domain Decomposer & Attribute Extractor
# -------------------------------------------------------------------------
class SlotConstraints(BaseModel):
    must_have: List[str] = Field(default_factory=list, description="Ingredienti esplicitamente richiesti")
    must_not_have: List[str] = Field(default_factory=list, description="Ingredienti o allergeni vietati")
    fat_profile: Optional[Literal["HIGH", "MEDIUM", "LOW"]] = None
    texture: Optional[Literal["SOFT", "HARD", "CREAMY", "CRUNCHY"]] = None

class ComponentSlot(BaseModel):
    node_id: str = Field(..., description="ID univoco dello slot (es. 'slot_salume_1')")
    macro_family: Literal["SALUMI", "FORMAGGI", "MARE", "PANE", "OLIVE", "SOTTOLI", "EXTRA", "RICERCA"]
    quantity: int = Field(1, description="Quantità di referenze per questo slot")
    forced_subcategory: Optional[str] = Field(None, description="Sottocategoria testuale forzata (es. 'erborinato')")
    constraints: SlotConstraints = Field(default_factory=SlotConstraints)

class DecomposedTree(BaseModel):
    slots: List[ComponentSlot] = Field(..., description="I nodi da risolvere sul catalogo")
    global_exclusions: List[str] = Field(default_factory=list)

# -------------------------------------------------------------------------
# NODO 4: Domain Rule Verifier
# -------------------------------------------------------------------------
class RuleViolation(BaseModel):
    rule: str = Field(..., description="Nome della regola violata (es. 'REDUNDANCY_SAME_CATEGORY_SUBTYPE')")
    description: str = Field(..., description="Spiegazione discorsiva del problema")
    offending_nodes: List[str] = Field(..., description="Gli ID dei prodotti o slot incriminati")

class VerificationResult(BaseModel):
    status: Literal["APPROVED", "REJECTED"]
    score: int = Field(..., ge=1, le=10, description="Punteggio qualitativo del board")
    violations: List[RuleViolation] = Field(default_factory=list)
