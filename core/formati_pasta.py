# -*- coding: utf-8 -*-
"""
Formati di pasta: logica sul formato giusto per il piatto (dati in ontologia_horeca.yaml, sezione formati_pasta).

Prima uno slot "spaghetti" veniva riempito con gli Spaghettini, "gnocchi di patate" con un preparato per patate e
"pasta ripiena" con la pasta mista (ricerca per parole simili). Ora:
  1. lo slot vuole ESATTAMENTE il formato nominato (spaghetti = spaghetti);
  2. se il cliente nomina un formato ("la carbonara con i rigatoni") vale il suo, non quello della ricetta;
  3. senza varianti speciali (integrale, farro, tartufo, limone...) se nessuno le chiede; "all'uovo" -> pasta all'uovo,
     "fresca" -> pasta fresca, produttore nominato -> quel produttore;
  4. se il formato non c'e', un formato VICINO dichiarato come alternativa; se non ce ne sono (gnocchi di patate,
     pasta ripiena) lo slot resta "non a catalogo".

  formato_di(testo)                 -> nome canonico del formato o None ("Spaghetti 12 Min." -> "spaghetti")
  scegli(ingrediente, query, indice, esclusi, canale) -> (prodotto | None, esatto: bool)  oppure None se l'ingrediente
                                       non e' un formato di pasta
"""
import re

_CACHE: dict = {}


def _cfg() -> dict:
    if "cfg" not in _CACHE:
        from core import ontologia
        dati = ontologia._carica().get("formati_pasta") or {}
        forme = sorted(((f, nome) for nome, d in (dati.get("formati") or {}).items() for f in d.get("forme", [])),
                       key=lambda x: -len(x[0]))
        _CACHE["cfg"] = {"formati": dati.get("formati") or {}, "forme": forme, "speciali": dati.get("speciali") or []}
    return _CACHE["cfg"]


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "").lower().replace("’", "'"))


def forma_di(testo: str) -> "tuple":
    """(forma trovata, formato canonico): "Tortiglioni" -> ("tortiglioni", "rigatoni")."""
    t = _norm(testo)
    for forma, nome in _cfg()["forme"]:
        if re.search(r"(?<![a-zà-ù])" + re.escape(forma) + r"(?![a-zà-ù])", t):
            return forma, nome
    return None, None


def formato_di(testo: str) -> "str | None":
    return forma_di(testo)[1]


def senza_formati(testo: str) -> str:
    """Il testo senza i formati di pasta, se resta qualcosa che dice il piatto ("carbonara con i rigatoni" ->
    "carbonara con i"); se il formato e' tutto ("dei rigatoni") il testo resta com'e'."""
    t = testo or ""
    for forma, _nome in _cfg()["forme"]:
        t = re.sub(r"(?<![a-zà-ù])" + re.escape(forma) + r"(?![a-zà-ù])", " ", t, flags=re.IGNORECASE)
    resto = [w for w in re.findall(r"[a-zà-ù]{4,}", t.lower()) if w not in ("fammi", "vorrei", "dammi", "proponimi", "pasta", "piatto", "primo")]
    return t if resto else (testo or "")


_NON_PASTA = ("sugo", "salsa", "condiment", "preparat", "crema", "pesto", " per ", "ragu", "ragù")


def e_pasta(p: dict) -> bool:
    """Pasta vera (non "sugo per spaghetti"): sottocategoria PASTA*, o un prodotto di dispensa che si chiama come un
    formato (la fregola sarda e' tra i cereali)."""
    m = p.get("metadata") or {}
    if str(m.get("sottocategoria") or "").upper().startswith("PASTA"):
        return True
    n = _nome(p)
    return str(m.get("reparto") or "").upper() == "DISPENSA" and not any(k in n for k in _NON_PASTA) and bool(formato_di(n))


def _nome(p: dict) -> str:
    from core.testo_prodotto import prima_riga
    return _norm(prima_riga(p.get("document", "")))


def _candidati(formato: str, indice: list, esclusi: set) -> list:
    from core import ontologia
    from core.vincoli_dieta import _DIETA_CORRENTE, prodotto_compatibile_con_dieta
    dieta = _DIETA_CORRENTE.get()
    out = []
    for p in indice:
        if p["id"] in esclusi or not e_pasta(p) or formato_di(_nome(p)) != formato:
            continue
        if dieta and not prodotto_compatibile_con_dieta(p["metadata"], dieta):
            continue
        if ontologia.prodotto_escluso_da_cliente(p["metadata"], p.get("document", "")):
            continue
        out.append(p)
    return out


def _ordina(cand: list, richiesta: str, canale: "str | None", forma: "str | None" = None) -> list:
    from core.anagrafica_fornitori import ANAGRAFICA, stesso_fornitore
    r = _norm(richiesta)
    speciali_chiesti = [s for s in _cfg()["speciali"] if s in r]
    vuole_uovo = "uovo" in r
    vuole_fresca = bool(re.search(r"fresc", r))
    produttori = ANAGRAFICA.fornitori_in_testo(richiesta)

    from core import territorio
    regione = territorio.regione_richiesta(richiesta)  # "orecchiette pugliesi" -> prima un pastificio pugliese

    def chiave(p):
        n, m = _nome(p), p["metadata"]
        speciali = [s for s in _cfg()["speciali"] if s in n]
        return (
            not any(stesso_fornitore(m.get("nome_fornitore") or "", f) for f in produttori) if produttori else False,
            bool(regione) and not territorio.di_regione(p, regione),
            bool(forma) and forma_di(n)[0] != forma,  # "rigatoni" prima dei tortiglioni, "penne" prima dei pennoni
            (not any(s in n for s in speciali_chiesti)) if speciali_chiesti else bool(speciali),
            vuole_uovo and str(m.get("sottocategoria") or "").upper() != "PASTA ALL'UOVO",
            vuole_fresca != ("fresc" in n),
            bool(canale) and str(m.get("canale_formato") or "") not in ("", canale),
            p["id"],
        )
    return sorted(cand, key=chiave)


def scegli(ingrediente: str, query_cliente: str, indice: list, esclusi: "set | None" = None,
           canale: "str | None" = None) -> "tuple | None":
    """None se l'ingrediente non e' un formato di pasta. Altrimenti (prodotto o None, esatto)."""
    fmt_slot = formato_di(ingrediente)
    generico = bool(re.search(r"(?<![a-zà-ù])pasta(?![a-zà-ù])", _norm(ingrediente)))
    fmt_cliente = formato_di(query_cliente)
    if not fmt_slot and not (generico and fmt_cliente):
        return None
    # il formato chiesto dal cliente vince su quello della ricetta, ma solo tra paste dello stesso uso
    fmt = fmt_slot
    if fmt_cliente and fmt_cliente != fmt_slot:
        fam = lambda f: (_cfg()["formati"].get(f) or {}).get("famiglia")
        if not fmt_slot or fam(fmt_slot) not in ("gnocchi", "ripiena", "minestra") and fam(fmt_cliente) not in ("gnocchi", "ripiena", "minestra"):
            fmt = fmt_cliente
    esclusi = set(esclusi or ())
    richiesta = f"{ingrediente} {query_cliente}"
    forma = forma_di(ingrediente)[0] if fmt == fmt_slot else forma_di(query_cliente)[0]
    cand = _ordina(_candidati(fmt, indice, esclusi), richiesta, canale, forma)
    if cand:
        return cand[0], True
    for vicino in (_cfg()["formati"].get(fmt) or {}).get("vicini") or []:
        cand = _ordina(_candidati(vicino, indice, esclusi), richiesta, canale)
        if cand:
            return cand[0], False
    return None, False
