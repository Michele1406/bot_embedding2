# -*- coding: utf-8 -*-
"""Golden set di regressione (tests/golden/casi.yaml). Offline rispetto al modello di generazione:
usa DB reale e gli embedding delle query (con cache su disco: tests/ambiente.py).

    python -m unittest tests.test_golden -v
    python -m tests.test_golden          # stampa il rapporto per caso
"""
import os
import re
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import ambiente
from core import allergeni, ontologia, retrieval_utils as ru
from core.testo_prodotto import nome_prodotto

FILE_CASI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "casi.yaml")
with open(FILE_CASI, encoding="utf-8") as _f:
    CASI = yaml.safe_load(_f)["casi"]


def _campo(rec, campo):
    m = rec["metadata"]
    if campo == "nome":
        return nome_prodotto(rec.get("document") or "")
    return str(m.get(campo) or "")


class NonValutabile(Exception):
    """Embedding non disponibile durante il caso: il motore e' ricaduto sul solo lessicale, esito non confrontabile."""


def esegui_caso(caso: dict, amb: dict) -> list:
    """Ritorna la lista di record prodotto restituiti dal motore per il caso."""
    errori_prima = amb["emb"].errori
    prodotti = _esegui(caso, amb)
    if amb["emb"].errori > errori_prima:
        raise NonValutabile("embedding non disponibile (quota/rete)")
    return prodotti


def _esegui(caso: dict, amb: dict) -> list:
    dieta = caso.get("dieta")
    ontologia.ESCLUSIONI_CORRENTI.set(caso.get("esclusioni") or [])
    allergeni.ALLERGIE_CORRENTI.set(caso.get("allergie") or [])
    canale = ru.rileva_canale_locale(caso.get("tipo_locale")) if caso.get("tipo_locale") else None
    try:
        if caso["modo"] == "ricetta":
            out = ru.componi_proposta_da_ricettario(
                caso["query"], caso.get("tipo_locale"), amb["ric"], amb["col"], amb["ic"], amb["emb"],
                indice_fornitori=amb["inf"], indice_testuale=amb["it"], query_completa=caso["query"],
                filtro_dieta=dieta)
            caso["_ricetta"] = (out or {}).get("id") or (out or {}).get("id_ricetta") if isinstance(out, dict) else None
            slot = (out or {}).get("slot", []) if isinstance(out, dict) else []
            return [s["prodotto_trovato"] for s in slot if s.get("prodotto_trovato")]
        ru._DIETA_CORRENTE.set(dieta)
        return ru.cerca_prodotti(amb["col"], amb["ic"], amb["emb"], caso["query"], caso.get("n", 10),
                                 indice_fornitori=amb["inf"], indice_testuale=amb["it"],
                                 tipo_locale=caso.get("tipo_locale"), filtro_dieta=dieta,
                                 canale_locale=canale, esclusioni=caso.get("esclusioni"),
                                 intento=caso.get("intento"))
    finally:
        ontologia.ESCLUSIONI_CORRENTI.set([])
        allergeni.ALLERGIE_CORRENTI.set([])
        ru._DIETA_CORRENTE.set(None)


def valuta(caso: dict, prodotti: list) -> list:
    """Ritorna la lista delle violazioni (vuota = caso superato)."""
    err = []
    if len(prodotti) < max(caso.get("min_risultati", 0), 1):  # un esito vuoto non e' mai "superato"
        err.append(f"risultati {len(prodotti)} < {caso.get('min_risultati', 1)}")
    if caso.get("dieta_ok"):
        for p in prodotti:
            if not ru.prodotto_compatibile_con_dieta(p["metadata"], caso["dieta_ok"]):
                err.append(f"dieta {caso['dieta_ok']} violata da {_campo(p, 'nome')}")
    for campo, pattern in (caso.get("vietati") or {}).items():
        for p in prodotti:
            for rx in pattern:
                if re.search(rx, _campo(p, campo), re.IGNORECASE):
                    err.append(f"vietato {campo}~{rx}: {_campo(p, 'nome')}")
    entro = caso.get("entro", len(prodotti) or 1)
    for campo, pattern in (caso.get("richiesti") or {}).items():
        for rx in pattern:
            if not any(re.search(rx, _campo(p, campo), re.IGNORECASE) for p in prodotti[:entro]):
                err.append(f"manca {campo}~{rx} nei primi {entro}")
    if caso.get("allergie"):
        for p in prodotti:
            motivo = allergeni.rischio(p["metadata"], p.get("document") or "", caso["allergie"])
            if motivo:
                err.append(f"allergia violata ({motivo}): {_campo(p, 'nome')}")
    if caso.get("varieta"):
        from core.famiglie_tagliere import doppioni
        for motivo, nomi in doppioni(prodotti):
            err.append(f"tagliere poco vario ({motivo}): {nomi}")
    for campo, valori in (caso.get("solo") or {}).items():
        for p in prodotti:
            if _campo(p, campo) not in valori:
                err.append(f"{campo}={_campo(p, campo)} non ammesso: {_campo(p, 'nome')}")
    return err


@unittest.skipUnless(ambiente.carica(), "serve il database_vettoriale")
class TestGolden(unittest.TestCase):
    pass


def _crea(caso):
    def t(self):
        amb = ambiente.carica()
        try:
            prodotti = esegui_caso(dict(caso), amb)
        except NonValutabile as e:  # quota/rete: non e' un fallimento del motore
            self.skipTest(str(e))
        self.assertEqual(valuta(caso, prodotti), [], caso["id"])
    return t


for _c in CASI:
    setattr(TestGolden, f"test_{_c['id']}", _crea(_c))


if __name__ == "__main__":
    amb = ambiente.carica()
    ok = 0
    for c in CASI:
        try:
            pr = esegui_caso(dict(c), amb)
            errori = valuta(c, pr)
        except NonValutabile as e:
            print("SKIP " + c["id"], f"({e})")
            continue
        except Exception as e:
            pr, errori = [], [f"ECCEZIONE {e}"]
        ok += not errori
        print(("OK  " if not errori else "KO  ") + c["id"], f"({len(pr)} prodotti)")
        for e in errori:
            print("      -", e)
        if errori:
            print("      primi:", [_campo(p, "nome")[:40] for p in pr[:4]])
    print(f"\n{ok}/{len(CASI)} casi superati")
