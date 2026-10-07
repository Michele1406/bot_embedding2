"""
Ricettario e composizione gastronomica: template, slot, taglieri, tris, sostituti.
(Estratto da retrieval_utils.py: i nomi restano importabili anche da li').
"""
from core.domain_rules import check_board_violations, prodotto_appartiene_a_famiglia, INCOMPATIBILITY_MATRIX
from core.testo_prodotto import prima_riga, nome_senza_produttore
from core.parse_formato import formato_prodotto
from core import logistica, ontologia, formati_pasta

import os
import re
import json
from core.fornitori_config import (
    FORNITORI,
    TEMI_REGIONALI,
    elenco_fornitori_per_ruolo_regione,
    match_fornitore,
)
from core.profilazione_locale import (
    rileva_canale_locale,
    rileva_formato_prodotto,
    punteggio_coerenza_canale,
)
from core.tassonomia_sofood import classifica_terra_mare
from core.contesto_prodotti import pulisci_nome_commerciale
from core.ricerca_base import parse_db_json_field
from core.ricerca_prodotti import cerca_prodotti
from core.vincoli_dieta import _CANALE_CORRENTE, _DIETA_CORRENTE, _QUERY_ORIGINALE, _template_ok_dieta, prodotto_compatibile_con_dieta


def formatta_proposta_ricetta(template_scelto, slot_riempiti):
    return {
        "template": template_scelto,
        "slot": slot_riempiti
    }

def componi_proposta_da_ricettario(richiesta_cliente: str, tipo_locale: "str | None", collezione_ricette,
                                    collezione_prodotti, indice_codici: dict, embedder,
                                    indice_fornitori: "dict | None" = None,
                                    indice_testuale: "list | None" = None,
                                    target_salumi: "int | None" = None,
                                    target_formaggi: "int | None" = None,
                                    prodotti_gia_proposti: "list | None" = None,
                                    piano_ricerca: "dict | None" = None,
                                    query_completa: str = "",
                                    prodotti_esclusi: set = None,
                                    filtro_dieta: str = None,
                                    ricette_escluse: set = None,
                                    categoria_ereditata: str = None,
                                    piatto_precedente_nome: str = None) -> str:
    """Implementa la pipeline RAG per il Ricettario (vedi documentazione in
    docs/struttura_rag_ricettario.md per i dettagli implementativi e il
    funzionamento del ciclo di selezione e arricchimento)."""
    
    prodotti_esclusi = set(prodotti_esclusi or []) | set(prodotti_gia_proposti or [])
    
    # Se il piano ricerca definisce quantità forzate globali per i taglieri, usale
    if piano_ricerca and piano_ricerca.get("intento") == "tagliere_o_ricetta":
        for comp in piano_ricerca.get("componenti", []):
            if comp.get("ruolo") == "salumi" and comp.get("quantita_target") is not None:
                target_salumi = int(comp.get("quantita_target"))
            if comp.get("ruolo") == "formaggi" and comp.get("quantita_target") is not None:
                target_formaggi = int(comp.get("quantita_target"))
                
    from core.config_manager import get_regole_tagliere
    regole_tagliere = get_regole_tagliere()
    # Quantita' non indicate dall'analisi: si leggono dal messaggio ("degustazione di 5 formaggi" -> 0 salumi, 5 formaggi);
    # prima il default fisso aggiungeva 3 salumi anche a chi chiedeva solo formaggi
    ts_q, tf_q = estrai_conteggi_tagliere(query_completa or richiesta_cliente)
    target_salumi = target_salumi if target_salumi is not None else ts_q
    target_formaggi = target_formaggi if target_formaggi is not None else tf_q
    if target_salumi is None:
        target_salumi = regole_tagliere.get("quantita_default_salumi", 3)
    if target_formaggi is None:
        target_formaggi = regole_tagliere.get("quantita_default_formaggi", 3)

    # Step 1: Trova il miglior template
    # il formato di pasta nominato dal cliente non sceglie il piatto ("carbonara con i rigatoni" e' una carbonara, non
    # i "Rigatoni alla Gricia"): il formato lo applica poi lo slot della pasta (core/formati_pasta.py)
    richiesta_piatto = formati_pasta.senza_formati(richiesta_cliente)
    query_completa_piatto = formati_pasta.senza_formati(query_completa or richiesta_cliente)
    query_ibrida = f"{richiesta_piatto} {tipo_locale or ''} {filtro_dieta or ''}".strip()

    try:
        q_emb = embedder.embed_query(query_ibrida)
        res = collezione_ricette.query(
            query_embeddings=[q_emb],
            n_results=10,
            include=["metadatas"]
        )
        risultati_ricettario = []
        for d_id, meta in zip(res["ids"][0], res["metadatas"][0]):
            risultati_ricettario.append({
                "id_ricetta": d_id,
                "nome_piatto": meta.get("nome_piatto", "Senza Nome"),
                "categoria": meta.get("categoria", "sconosciuta"),
                "slot": parse_db_json_field(meta.get("ingredienti_json", meta.get("slot", "[]"))),
                "canali_sconsigliati": parse_db_json_field(meta.get("canali_sconsigliati", "[]")),
                "tag_dieta": meta.get("tag_dieta"),
                "copertura_catalogo": meta.get("copertura_catalogo"),
            })
    except Exception as e:
        from core.errori import breve
        print(f"[RICETTARIO] ricerca vettoriale delle ricette fallita ({breve(e)}): scelta per parole del nome")
        risultati_ricettario = _ricette_per_parole(collezione_ricette, query_completa_piatto, filtro_dieta)

    if filtro_dieta and risultati_ricettario:
        # Con una dieta attiva si scelgono solo template taggati per quella dieta
        # (es. "Tagliere Vegetale" per un vegano); se nessuno lo e', restano tutti
        # e sara' il filtro a livello di prodotto a proteggere.
        d_ok = [r for r in risultati_ricettario if _template_ok_dieta(r.get("tag_dieta"), filtro_dieta)]
        if d_ok:
            risultati_ricettario = d_ok

    if risultati_ricettario:
        # Stabile: le ricette con protagonisti introvabili a catalogo (copertura "bassa",
        # vedi scripts/aggiorna_ricettario.py) passano dopo le altre.
        risultati_ricettario = sorted(risultati_ricettario, key=lambda r: r.get("copertura_catalogo") == "bassa")

    pref_ids = ontologia.template_preferiti(query_completa or richiesta_cliente, tipo_locale, filtro_dieta)
    if pref_ids:
        # es. "tris" chiesto da un bar -> prima i template "Tris da Bar" (3 ciotoline di snack)
        try:
            pr = collezione_ricette.get(ids=pref_ids, include=["metadatas"])
            per_id = dict(zip(pr["ids"], pr["metadatas"]))
            preferiti = []
            for pid in pref_ids:
                meta_p = per_id.get(pid)
                if meta_p is None or (ricette_escluse and pid in ricette_escluse):
                    continue
                if not _template_ok_dieta(meta_p.get("tag_dieta"), filtro_dieta):
                    continue
                preferiti.append({
                    "id_ricetta": pid,
                    "nome_piatto": meta_p.get("nome_piatto", "Senza Nome"),
                    "categoria": meta_p.get("categoria", "sconosciuta"),
                    "slot": parse_db_json_field(meta_p.get("ingredienti_json", meta_p.get("slot", "[]"))),
                    "canali_sconsigliati": parse_db_json_field(meta_p.get("canali_sconsigliati", "[]")),
                    "tag_dieta": meta_p.get("tag_dieta"),
                })
            if preferiti:
                risultati_ricettario = preferiti + [r for r in risultati_ricettario if r["id_ricetta"] not in pref_ids]
        except Exception as e:
            print(f"[ONTOLOGIA] template preferiti non caricati: {e}")

    if not risultati_ricettario:
        print(f"[DEBUG RAG] Richiesta '{richiesta_cliente}' NON PERTINENTE al ricettario. Fallback al catalogo.")
        return ""

    def _ammesso(r, con_categoria=True):
        if ricette_escluse and r["id_ricetta"] in ricette_escluse:
            return False
        if tipo_locale and tipo_locale in r.get("canali_sconsigliati", []):
            return False
        if rileva_canale_locale(tipo_locale) != "retail" and re.search(r"bottega|retail|da banco", str(r.get("nome_piatto", "")).lower()):
            return False  # template pensati per la rivendita: solo a clienti retail
        return not (con_categoria and categoria_ereditata and r.get("categoria") != categoria_ereditata)

    if categoria_ereditata and not any(_ammesso(r) for r in risultati_ricettario):
        # nessuna ricetta della categoria ereditata tra quelle trovate per similarita': si cercano PER CATEGORIA
        # invece di passare a un'altra portata (chat reale: dopo un tris arrivavano tagliatelle al ragu')
        risultati_ricettario = _ricette_della_categoria(collezione_ricette, categoria_ereditata, filtro_dieta) + risultati_ricettario
    ordine_template = [r for r in risultati_ricettario if _ammesso(r)]
    ordine_template += [r for r in risultati_ricettario if r not in ordine_template and _ammesso(r, False)]
    if not ordine_template:
        ordine_template = risultati_ricettario[:1]

    # Una ricetta senza il suo protagonista a catalogo non si propone: si prova la successiva (max 3 template,
    # ogni tentativo costa ricerche). Se nessuna e' completa resta la prima, con gli slot mancanti dichiarati.
    prima_scelta = None
    for template_scelto in ordine_template[:3]:
        slot_riempiti, violations = _riempi_con_vincoli(
            template_scelto, collezione_prodotti, indice_codici, embedder, indice_fornitori, indice_testuale,
            target_salumi, target_formaggi, richiesta_cliente, prodotti_esclusi, piano_ricerca, filtro_dieta,
            tipo_locale, query_completa)
        if prima_scelta is None:
            prima_scelta = (template_scelto, slot_riempiti, violations)
        if proponibile(slot_riempiti):
            break
        print(f"[RICETTARIO] '{template_scelto.get('nome_piatto')}' non proponibile (protagonista mancante): provo la successiva")
    else:
        template_scelto, slot_riempiti, violations = prima_scelta

    _aggiungi_alternative(slot_riempiti, collezione_prodotti, indice_codici, embedder, indice_fornitori,
                          indice_testuale, prodotti_esclusi)

    if violations:
        # Rete di sicurezza finale: dopo 3 tentativi il board ha ancora
        # violazioni note. Prima venivano semplicemente ignorate e la proposta
        # usciva comunque come se fosse a posto. Ora lo segnaliamo esplicitamente
        # nel testo che arriva al modello, così Nino può almeno scegliere di
        # ammorbidire la proposta o avvisare il cliente, invece di presentare
        # con sicurezza una combinazione che il sistema stesso sa essere
        # discutibile.
        print(f"[CONSTRAINT SOLVER] Esauriti i tentativi con violazioni residue: {violations}")
        template_scelto = dict(template_scelto)
        nota_esistente = template_scelto.get("note_composizione", "") or ""
        template_scelto["note_composizione"] = (
            nota_esistente
            + " [AVVISO INTERNO NON DA MOSTRARE AL CLIENTE: la combinazione proposta potrebbe avere una "
            + "ripetizione di texture/famiglia non ideale (" + "; ".join(violations) + "). "
            + "Se puoi, presenta la proposta con più cautela o offri di modificarla su richiesta.]"
        )

    return formatta_proposta_ricetta(template_scelto, slot_riempiti)


_RICETTE_CACHE: dict = {}
_STOP_RICETTA = {"vorrei", "fammi", "proponi", "proponimi", "consigli", "consigliami", "piatto", "piatti", "ricetta",
                 "ristorante", "locale", "nostro", "nostra", "menu", "qualcosa", "un'idea", "idea", "della", "delle", "degli",
                 "alla", "alle", "con", "per", "dei", "una", "uno", "cosa", "come", "mi", "serve", "servono"}


def _ricette_per_parole(collezione_ricette, richiesta: str, dieta: "str | None" = None, n: int = 10) -> list:
    """Ripiego senza embedding (quota esaurita): ricette ordinate per parole della richiesta presenti in nome,
    categoria e ingredienti. Prima, senza embedding, il ricettario restituiva sempre "nessuna ricetta"."""
    if id(collezione_ricette) not in _RICETTE_CACHE:
        try:
            d = collezione_ricette.get(include=["metadatas"])
            _RICETTE_CACHE[id(collezione_ricette)] = list(zip(d["ids"], d["metadatas"]))
        except Exception:
            return []
    parole = {w[:6] for w in re.findall(r"[a-zàèéìòù']+", (richiesta or "").lower()) if len(w) >= 4 and w not in _STOP_RICETTA}
    if not parole:
        return []
    punteggi = []
    for rid, meta in _RICETTE_CACHE[id(collezione_ricette)]:
        nome = str(meta.get("nome_piatto") or "").lower()
        cat = str(meta.get("categoria") or "").lower()
        ingr = str(meta.get("ingredienti_json") or "").lower()
        p = sum(3 for w in parole if w in nome) + sum(2 for w in parole if w in cat) + sum(1 for w in parole if w in ingr)
        if dieta and dieta.lower() in ("vegano", "vegetariano"):
            if not _template_ok_dieta(meta.get("tag_dieta"), dieta):
                continue  # con una dieta vegetale solo ricette taggate per quella dieta
            p += 2
        if p >= 3:  # almeno una parola nel nome o due indizi deboli
            punteggi.append((-p, meta.get("copertura_catalogo") == "bassa", rid, meta))
    punteggi.sort(key=lambda x: x[:3])
    return [{"id_ricetta": rid, "nome_piatto": meta.get("nome_piatto", "Senza Nome"), "categoria": meta.get("categoria", "sconosciuta"),
             "slot": parse_db_json_field(meta.get("ingredienti_json", meta.get("slot", "[]"))),
             "canali_sconsigliati": parse_db_json_field(meta.get("canali_sconsigliati", "[]")),
             "tag_dieta": meta.get("tag_dieta"), "copertura_catalogo": meta.get("copertura_catalogo"),
             "note_composizione": meta.get("note_composizione", "")} for _p, _b, rid, meta in punteggi[:n]]


def _ricette_della_categoria(collezione_ricette, categoria: str, dieta: "str | None" = None, n: int = 10) -> list:
    """Ricette di una categoria (copertura del catalogo alta prima), con il filtro dieta dei template."""
    try:
        d = collezione_ricette.get(where={"categoria": categoria}, include=["metadatas"])
    except Exception:
        return []
    out = []
    for rid, meta in zip(d["ids"], d["metadatas"]):
        if dieta and not _template_ok_dieta(meta.get("tag_dieta"), dieta) and dieta.lower() in ("vegano", "vegetariano"):
            continue
        out.append({"id_ricetta": rid, "nome_piatto": meta.get("nome_piatto", "Senza Nome"), "categoria": meta.get("categoria"),
                    "slot": parse_db_json_field(meta.get("ingredienti_json", meta.get("slot", "[]"))),
                    "canali_sconsigliati": parse_db_json_field(meta.get("canali_sconsigliati", "[]")),
                    "tag_dieta": meta.get("tag_dieta"), "copertura_catalogo": meta.get("copertura_catalogo"),
                    "note_composizione": meta.get("note_composizione", "")})
    out.sort(key=lambda r: ({"alta": 0, "media": 1}.get(r.get("copertura_catalogo"), 2), r["id_ricetta"]))
    return out[:n]


def _riempi_con_vincoli(template_scelto, collezione_prodotti, indice_codici, embedder, indice_fornitori, indice_testuale,
                        target_salumi, target_formaggi, richiesta_cliente, prodotti_esclusi, piano_ricerca, filtro_dieta,
                        tipo_locale, query_completa) -> tuple:
    """Riempie gli slot di un template con fino a 3 tentativi del constraint solver. Ritorna (slot, violazioni)."""
    violations = []
    slot_riempiti = []
    for attempt in range(3):
        slot_riempiti = riempi_slot_ricetta(
            template_scelto,
            collezione_prodotti,
            indice_codici,
            embedder,
            indice_fornitori,
            indice_testuale,
            target_salumi=target_salumi,
            target_formaggi=target_formaggi,
            query_utente=richiesta_cliente,
            prodotti_esclusi=prodotti_esclusi,
            piano_ricerca=piano_ricerca,
            filtro_dieta=filtro_dieta,
            canale_locale=rileva_canale_locale(tipo_locale),
            query_originale=query_completa or None
        )

        # Check violations
        board_products = [s["prodotto_trovato"] for s in slot_riempiti if s.get("prodotto_trovato")]
        from core.config_manager import get_regole_tagliere
        # In yaml: incompatibilita -> terra_mare (true = incompatibili, false = mix permesso)
        allow_terra_mare = not get_regole_tagliere().get("incompatibilita", {}).get("terra_mare", True)
        violations = check_board_violations(board_products, allow_terra_mare=allow_terra_mare)
        if not violations:
            break

        print(f"[CONSTRAINT SOLVER] Violazioni rilevate (Attempt {attempt+1}): {violations}")
        # FIX: prima escludevamo TUTTI i prodotti del board (anche quelli senza
        # nessun problema, es. pane/olive già corretti), buttando via lavoro
        # buono e rischiando comunque di ripescare la stessa combinazione
        # sbagliata al giro dopo perché l'esclusione non era mirata alla
        # famiglia incriminata. Ora escludiamo solo i prodotti effettivamente
        # coinvolti nelle violazioni rilevate (quelli citati nel messaggio di
        # errore), lasciando intatte le altre scelte già corrette.
        nomi_incriminati = set()
        for v in violations:
            if "referenze coinvolte:" in v:
                elenco = v.split("referenze coinvolte:")[-1].strip(" )")
                nomi_incriminati.update(n.strip().lower() for n in elenco.split(","))
        if nomi_incriminati:
            for prod in board_products:
                doc = prod.get("document", "") or ""
                nome = prima_riga(doc).strip().lower() if doc else ""
                if nome in nomi_incriminati:
                    prodotti_esclusi.add(prod["id"])
        else:
            # Fallback: se per qualche motivo non si riescono a isolare i nomi
            # (es. regola TERRA_MARE_MIX, che non ne elenca), escludiamo
            # comunque tutto il board come rete di sicurezza (comportamento
            # precedente), meglio ripartire da capo che restare bloccati.
            for prod in board_products:
                prodotti_esclusi.add(prod["id"])
    return slot_riempiti, violations


def _aggiungi_alternative(slot_riempiti: list, collezione_prodotti, indice_codici, embedder, indice_fornitori,
                          indice_testuale, prodotti_esclusi) -> None:
    """Per ogni slot NON_TROVATO cerca un'ALTERNATIVA verificata a catalogo (stessa categoria attesa), con tutti i
    vincoli del cliente applicati da cerca_prodotti (dieta, esclusioni, allergie, zona, canale). Prima il modello
    riceveva "proponi alternative" senza alternative: le doveva inventare o pescare a caso dal contesto."""
    usati = {s["prodotto_trovato"]["id"] for s in slot_riempiti if s.get("prodotto_trovato")}
    for s in slot_riempiti:
        if s.get("esito") != "NON_TROVATO" or s.get("alternativa") or s.get("senza_alternative"):
            continue
        nome = re.sub(r"\(.*?\)", "", str(s.get("ingrediente_richiesto") or "")).strip()
        if not nome:
            continue
        if any(w in nome.lower() for w in _ERBE_FRESCHE):
            continue  # erbe aromatiche fresche: non si sostituiscono con sughi o oli aromatizzati
        try:
            cand = cerca_prodotti(collezione_prodotti, indice_codici, embedder, nome, n_risultati=4,
                                  indice_fornitori=indice_fornitori, indice_testuale=indice_testuale,
                                  filtro_categoria=s.get("categoria_attesa") or None)
        except Exception as e:
            from core.errori import breve
            print(f"[RICETTARIO] alternativa per '{nome}' non cercata: {breve(e)}")
            continue
        # un risultato solo lessicale vale come alternativa solo se pertinente ("uova fresche" non porta "orecchiette
        # fresche"); quelli vettoriali sono gia' semanticamente vicini e hanno la categoria attesa
        cand = [r for r in cand if (not r.get("match_lessicale") or _prodotto_pertinente(nome, r))
                and not forma_diversa(nome, r)]  # alternativa a "gamberi": mai una crema di scampi
        nuovi = [r for r in cand if r["id"] not in usati]
        # meglio un prodotto mai proposto in questa conversazione; se non c'e', va bene anche uno gia' visto
        cand = [r for r in nuovi if r["id"] not in (prodotti_esclusi or ())] or nuovi
        if cand:
            s["alternativa"] = cand[0]
            usati.add(cand[0]["id"])


_ERBE_FRESCHE = ("basilico", "prezzemolo", "origano fresco", "menta", "rosmarino fresco", "timo", "erba cipollina")

_STOP_INGREDIENTE = {"misti", "miste", "assortiti", "assortite", "qualita", "tipo", "artigianale", "artigianali", "fresco",
                     "fresca", "freschi", "fresche", "italiano", "italiana", "nostrano", "classico", "classica", "naturale",
                     "grattugiato", "grattugiata", "piccolo", "piccola", "grande", "buona", "ottimo", "ottima", "oppure",
                     "affettato", "affettati", "stagionato", "stagionata", "dolce", "piccante", "come", "della", "delle",
                     "degli", "dello", "alla", "alle", "agli", "sulla", "sotto", "sopra", "trafilati", "trafilata", "bronzo",
                     "grano", "duro", "vero", "vera", "veri", "tostato", "tostata",
                     # aggettivi di preparazione: "bacon affumicato" non e' "capocollo affumicato"
                     "affumicato", "affumicata", "affumicati", "affumicate", "grigliato", "grigliata", "grigliati",
                     "marinato", "marinata", "marinati", "croccante", "croccanti", "cotto", "cotta", "crudo", "cruda",
                     "intero", "intera", "tritato", "tritata", "salato", "salata", "salati", "speziato", "speziata",
                     "biologico", "biologica", "extravergine", "burger", "panino", "pizza", "ragu", "ragù"}


def _per_parola_principale(ingrediente: str, indice_testuale: "list | None") -> list:
    """Prodotti del catalogo il cui NOME contiene la parola principale dell'ingrediente (o un suo sinonimo: "tuorli
    d'uovo" -> uova), nel rispetto di dieta ed esclusioni del cliente. Solo la prima parola significativa: "uova fresche"
    non deve trovare le "orecchiette fresche"."""
    from core.testo_prodotto import nome_prodotto
    parole = [w for w in re.findall(r"[a-zàèéìòù]+", (ingrediente or "").lower()) if len(w) >= 4 and w not in _STOP_INGREDIENTE]
    if not parole or any(parole[0].startswith(p) for p in _PREPARAZIONI):
        return []  # "crema di uova e pecorino" e' una preparazione dello chef, non un prodotto da cercare per nome
    testa = parole[0][:5]
    radici = {testa} | set(_SINONIMI_INGREDIENTE.get(testa, ()))
    dieta = _DIETA_CORRENTE.get()
    out = []
    for p in indice_testuale or []:
        nome = set(re.findall(r"[a-zàèéìòù]+", nome_prodotto(p.get("document") or "").lower()))
        if not any(w.startswith(r) for w in nome for r in radici) or forma_diversa(ingrediente, p):
            continue
        if (dieta and not prodotto_compatibile_con_dieta(p["metadata"], dieta)) or ontologia.prodotto_escluso_da_cliente(p["metadata"], p.get("document", "")):
            continue
        out.append({"id": p["id"], "metadata": p["metadata"], "document": p["document"], "match_esatto": False})
    return out


def _prodotto_pertinente(ingrediente: str, prodotto: dict) -> bool:
    """True se il prodotto scelto per uno slot corrisponde davvero all'ingrediente (almeno una parola significativa
    dell'ingrediente compare in nome/tipo/sottocategoria del prodotto). Evita che il primo risultato di una ricerca
    vettoriale ("vongole veraci" -> una salsa qualsiasi) venga presentato come l'ingrediente richiesto."""
    from core.testo_prodotto import nome_prodotto
    m = prodotto.get("metadata") or {}
    testo = " ".join([nome_prodotto(prodotto.get("document") or ""), str(m.get("tipo_prodotto") or ""),
                      str(m.get("sottocategoria") or ""), str(m.get("specifiche_liv4") or "")]).lower()
    parole_prod = set(re.findall(r"[a-zàèéìòù]+", testo))
    if forma_diversa(ingrediente, prodotto):
        return False  # "gamberi" non e' una "crema di scampi", "frutti di mare" non e' un "sughetto di mare"
    radici = {w[:5] for w in re.findall(r"[a-zàèéìòù]+", (ingrediente or "").lower())
              if len(w) >= 4 and w not in _STOP_INGREDIENTE}
    for r in list(radici):
        radici |= set(_SINONIMI_INGREDIENTE.get(r, ()))
    # animale diverso da quello richiesto ("manzo macinato" -> hamburger di maiale): mai pertinente
    parole_ingr = re.findall(r"[a-zàèéìòù]+", (ingrediente or "").lower())
    animale_ingr = {a for a, radici_a in _ANIMALI.items() if any(w.startswith(r) for w in parole_ingr for r in radici_a)}
    animale_prod = {a for a, radici_a in _ANIMALI.items() if any(w.startswith(r) for w in parole_prod for r in radici_a)}
    if animale_ingr and animale_prod and not (animale_ingr & animale_prod):
        return False
    return any(any(p.startswith(r) for p in parole_prod) for r in radici)


# Preparazioni pronte: un prodotto con una di queste parole e' un'altra FORMA rispetto all'ingrediente base
_PREPARAZIONI = ("crema", "creme", "sugo", "sughi", "sughetto", "salsa", "salse", "pesto", "pate", "paté", "patè", "ragu",
                 "ragù", "condimento", "spalmabil", "mousse", "vellutat", "brodo", "fondo", "zuppa", "polpa di riccio")


def forma_diversa(ingrediente: str, prodotto: dict) -> bool:
    """True se lo slot chiede un ingrediente base e il prodotto e' una preparazione pronta (o viceversa non conta:
    se lo slot chiede proprio "sugo"/"crema", la preparazione va bene)."""
    from core.testo_prodotto import nome_prodotto
    ing = (ingrediente or "").lower()
    if any(p in ing for p in _PREPARAZIONI):
        return False
    m = prodotto.get("metadata") or {}
    testo = f" {nome_prodotto(prodotto.get('document') or '')} {m.get('tipo_prodotto') or ''} ".lower()
    return any(re.search(r"(?<![a-zàèéìòù])" + re.escape(p), testo) for p in _PREPARAZIONI)


_ANIMALI = {"bovino": ("manzo", "bovin", "fasso", "scott", "angus", "vitel"), "suino": ("maial", "suin", "porc"),
            "pollo": ("pollo", "pollame"), "tacchino": ("tacch",), "agnello": ("agnel", "ovin"), "pesce": ("pesce", "tonno", "salmo")}


# Sinonimi di dominio (radice dell'ingrediente del ricettario -> radici che lo indicano nei nomi a catalogo)
_SINONIMI_INGREDIENTE = {
    "manzo": ("fasso", "bovin", "scott", "angus", "vitel", "manzo"), "macin": ("trita", "hambu", "macin"),
    "bacon": ("pance",), "uova": ("uovo", "uova"), "uovo": ("uova", "uovo"), "tuorl": ("uova", "uovo", "tuorl"), "pomod": ("datte", "pelat", "passa", "pomod"),
    "mozza": ("fiord", "fior", "mozza"), "parmi": ("grana", "parmi"), "grana": ("parmi", "grana"),
    "acciu": ("alici", "acciu"), "alici": ("acciu", "alici"), "maion": ("maion", "mayo"), "patat": ("patat", "chips"),
}


def _togli_doppioni_tra_slot(slot_riempiti: list) -> None:
    """Lo stesso prodotto non puo' riempire due slot della stessa ricetta (es. tre slot "verdure sott'olio" con la
    stessa giardiniera): dal secondo in poi lo slot torna NON_TROVATO e ricevera' un'alternativa verificata."""
    from core.testo_prodotto import nome_prodotto
    visti = set()
    for s in slot_riempiti:
        p = s.get("prodotto_trovato")
        if not p:
            continue
        # stesso id o stesso prodotto in un altro formato ("Giardiniera 280 g" e "Giardiniera 1,5 kg")
        chiave = re.sub(r"[\d.,]+\s*(kg|gr|g|ml|lt|l|cl)\b|\d+", "", nome_prodotto(p.get("document") or "").lower()).strip()
        if p["id"] in visti or (chiave and chiave in visti):
            s["prodotto_trovato"] = None
            s["esito"] = "NON_TROVATO"
            s["motivo_scelta"] = "prodotto gia' usato in un altro slot"
        visti |= {p["id"], chiave}


# ====================================================================
# MODULO RICETTARIO E COMPOSIZIONE GASTRONOMICA (TASK 2)
# ====================================================================
SOGLIA_DISTANZA_SOSTITUTO = 0.35  # oltre questa distanza, meglio omettere che forzare un match debole


def _embed_testo_query(embedder, testo: str) -> list:
    """Helper compatibile con EmbedderMultimodaleGemini e EmbedderGemini."""
    if hasattr(embedder, "embed_query"):
        return embedder.embed_query(testo)
    elif hasattr(embedder, "embed_documento"):
        return embedder.embed_documento(testo)
    raise AttributeError("L'embedder fornito non possiede né embed_query né embed_documento.")


SOGLIA_DISTANZA_MAX_TEMPLATE = 0.72  # oltre questa distanza il match è rumoroso e non pertinente


def trova_template_ricetta(collezione_ricette, embedder, richiesta_cliente: str,
                            tipo_locale: "str | None" = None, n_candidati: int = 4,
                            filtro_dieta: "str | None" = None,
                            ricette_escluse: "set | list | None" = None,
                            categoria_ereditata: "str | None" = None,
                            piatto_precedente_nome: "str | None" = None) -> list:
    """
    Cerca il/i template di ricetta più vicini alla richiesta del cliente.
    Il filtro di canale si applica QUI (selezione del template), non sui
    singoli prodotti del RAG generico altrove nel codice — vedi Guardrail.
    Regola Pizzerie: le pizzerie NON usano basi pinsa o padellino precotte!
    """
    richiesta_lower = richiesta_cliente.lower()
    embedding = _embed_testo_query(embedder, richiesta_cliente)

    # Identificazione portata/categoria richiesta dall'utente
    portate_target = None
    if any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["tagliere", "taglieri"]):
        portate_target = {"tagliere"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["risotto", "riso"]):
        portate_target = {"risotto"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in [
        "primo piatto", "primi piatti", "un primo", "i primi", "come primo", "pasta", "spaghetti", "spaghettone", "paccheri",
        "rigatoni", "gnocchi", "fregola", "calamarata", "orecchiette", "linguine", "fusilli", "scialatielli"
    ]) or (re.search(r"\bprimo\b", richiesta_lower) and not re.search(r"\b(dal|del|al|nel|col|questo|quel)\s+primo\b", richiesta_lower)):
        portate_target = {"primo", "risotto"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["secondo", "secondi"]):
        portate_target = {"secondo"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["antipasto", "antipasti", "entrée", "entree"]):
        portate_target = {"antipasto"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["dolce", "dolci", "dessert"]):
        portate_target = {"dolce"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["contorno", "contorni"]):
        portate_target = {"contorno"}
    elif bool(re.search(r"\b(?:panin[oi]|burger|hamburg\w*)\b", richiesta_lower)):
        if not any(k in richiesta_lower for k in ["al piatto", "secondo piatto", "senza pane", "senza bun"]):
            portate_target = {"panino"}
        else:
            portate_target = {"secondo"}
    elif any(re.search(r"\b" + w + r"\b", richiesta_lower) for w in ["pizza", "pinsa", "padellino"]):
        portate_target = {"pizza"}
    elif categoria_ereditata:
        # Se l'utente non ha specificato una portata nuova esplicita, eredita la categoria precedente
        cat_ered = categoria_ereditata.lower().strip()
        if cat_ered in ["risotto", "riso"]:
            portate_target = {"risotto"}
        elif cat_ered == "primo" and piatto_precedente_nome and any(k in piatto_precedente_nome.lower() for k in ["risotto", "riso"]):
            portate_target = {"risotto"}
        else:
            portate_target = {cat_ered}

    # Query ChromaDB con eventuale filtro where sulla categoria per massima precisione
    where_filter = None
    if portate_target:
        if len(portate_target) == 1:
            where_filter = {"categoria": list(portate_target)[0]}
        else:
            where_filter = {"categoria": {"$in": list(portate_target)}}

    try:
        if where_filter:
            risultati = collezione_ricette.query(query_embeddings=[embedding], n_results=35, where=where_filter)
        else:
            risultati = collezione_ricette.query(query_embeddings=[embedding], n_results=40)
    except Exception as e:
        risultati = collezione_ricette.query(query_embeddings=[embedding], n_results=40)

    candidati = []
    if risultati.get("ids") and risultati["ids"][0]:
        for i, id_ricetta in enumerate(risultati["ids"][0]):
            if ricette_escluse and id_ricetta in ricette_escluse:
                continue

            meta = risultati["metadatas"][0][i]
            # Filtro unidirezionale: esclude solo i template esplicitamente sconsigliati per questo canale
            if tipo_locale and tipo_locale.lower() in str(meta.get("canali_sconsigliati", "")).lower():
                continue

            # Regola categorica per pizzerie: non proporre basi pinsa/padellino a una pizzeria
            nome_basso = meta["nome_piatto"].lower()
            if tipo_locale and tipo_locale.lower() == "pizzeria" and any(p in nome_basso for p in ["pinsa", "padellino"]):
                continue

            # Filtro per panini di pesce / tartare a crudo:
            # Proponili SOLO se l'utente ha esplicitamente richiesto pesce, mare, tonno, tartare, crudo, polpo, salmone, alici.
            # Per richieste generiche di panino/burger ("un panino", "un panino più semplice", "burger"), non proporre tartare di pesce
            is_panino_or_burger = bool(re.search(r"\b(?:panin[oi]|burger|hamburg\w*|sandwich)\b", richiesta_lower))
            ha_richiesta_pesce = any(w in richiesta_lower for w in ["tonno", "salmone", "pesce", "polpo", "spada", "gamber", "mare", "alici", "tartare", "crudo"])
            if is_panino_or_burger and not ha_richiesta_pesce:
                if any(w in nome_basso for w in ["tartare", "tonno", "polpo", "salmone", "pesce", "alici", "bottarga"]):
                    continue

            # Filtro dietetico sui template (vegano / vegetariano)
            if filtro_dieta and filtro_dieta.lower() == "vegano":
                cat_ricetta = meta.get("categoria", "").lower()
                ingr_raw = str(meta.get("ingredienti_json", "")).lower()
                parole_non_vegane = [
                    "carne", "salumi", "tagliere", "salame", "crudo", "pesce", "tonno", "alici", "gamberi",
                    "formaggio", "formaggi", "caciocavallo", "provola", "mozzarella", "burrata", "ricotta",
                    "pecorino", "parmigiano", "latte", "uovo", "uova", "lardo", "pancetta", "guanciale"
                ]
                if any(k in nome_basso for k in parole_non_vegane) or any(k in ingr_raw for k in parole_non_vegane):
                    continue

            distanza = risultati["distances"][0][i] if "distances" in risultati and risultati["distances"] else 0.0

            # Soglia di distanza semantica: scarta match spuri (>0.72) se non c'è corrispondenza testuale significativa
            tokens_richiesta = [t for t in re.findall(r"\w+", richiesta_lower) if len(t) > 3 and t not in ["formati", "formato", "confezioni", "confezione", "questi", "ingredienti", "avete"]]
            ha_match_lessicale = any(t in nome_basso or (len(t) >= 6 and t[:6] in nome_basso) for t in tokens_richiesta)
            if distanza > SOGLIA_DISTANZA_MAX_TEMPLATE and not ha_match_lessicale:
                continue

            candidati.append({
                "id_ricetta": id_ricetta,
                "nome_piatto": meta["nome_piatto"],
                "categoria": meta["categoria"],
                "note_composizione": meta.get("note_composizione", ""),
                "ingredienti": parse_db_json_field(meta.get("ingredienti_json", "[]")),
                "distanza": distanza,
            })

    # Se è stata richiesta esplicitamente una portata, filtra rigorosamente le ricette della portata corretta
    if portate_target:
        candidati = [c for c in candidati if str(c.get("categoria", "")).lower() in portate_target]

    # Reranking selettivo su preparazioni specifiche o ingredienti protagonisti nominati dal cliente
    parole_focus = [
        "pinsa", "padellino", "maritozzo", "toast", "scrocchiarella", "burger", "hamburg", "paella",
        "polpo", "ricci", "riccio", "cozze", "vongole", "astice", "scampi", "gamberi", "tartufo",
        "carbonara", "amatriciana", "cacio e pepe", "totano", "seppia",
        "spagna", "spagnolo", "spagnola", "spagnoli", "spagnole", "tapas", "iberico", "iberica", "cecina", "bellota"
    ]
    focus_richiesto = next((p for p in parole_focus if (p in richiesta_lower or re.search(r"\b" + p, richiesta_lower))), None)
    if focus_richiesto:
        corrispondenti = [
            t for t in candidati
            if focus_richiesto in t["nome_piatto"].lower()
            or any(focus_richiesto in str(ing.get("INGREDIENTE_GENERICO", "")).lower() or focus_richiesto in str(ing.get("NOTE_INGREDIENTE", "")).lower() for ing in t.get("ingredienti", []))
        ]
        altri = [t for t in candidati if t not in corrispondenti]
        corrispondenti.sort(key=lambda x: (0 if str(x.get("id_ricetta", "")).startswith("SO_") else 1, x.get("distanza", 99.0)))
        altri.sort(key=lambda x: (0 if str(x.get("id_ricetta", "")).startswith("SO_") else 1, x.get("distanza", 99.0)))
        candidati = corrispondenti + altri
    else:
        candidati.sort(key=lambda x: (0 if str(x.get("id_ricetta", "")).startswith("SO_") else 1, x.get("distanza", 99.0)))

    return candidati[:n_candidati]


PAROLE_NUMERI = {
    "un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4,
    "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10
}


def _parse_numero_italiano(val_str: str) -> int:
    val_str = val_str.strip().lower()
    if val_str.isdigit():
        return int(val_str)
    return PAROLE_NUMERI.get(val_str, 1)


def estrai_conteggi_tagliere(testo: str, ha_gia_prodotti: bool = False) -> tuple[int, int]:
    """Estrae il numero target di salumi e formaggi desiderati per un tagliere.
    Ritorna: (target_salumi, target_formaggi)
    - Default tagliere generico se non specificato: (3, 3)
    - 'altri 2 salumi oltre questo e altrettanti formaggi' -> (2, 2) se ha già prodotti, altrimenti (3, 3)
    - 'degustazione con 5 formaggi diversi' -> (0, 5)
    - '4 salumi e 2 formaggi' -> (4, 2)
    - 'tagliere per il mio pub' -> (3, 3)
    """
    testo_basso = testo.lower()
    num_pattern = r"(?:\d+|un|uno|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)"

    target_salumi = None
    salumi_match = re.search(rf"(altri\s+)?({num_pattern})\s+(?:tipi\s+di\s+|referenze\s+di\s+|qualit[àa]\s+di\s+)?(?:salumi|salume|prosciutti|prosciutto)", testo_basso)
    if salumi_match:
        is_altri = bool(salumi_match.group(1))
        n = _parse_numero_italiano(salumi_match.group(2))
        if (is_altri or "oltre" in testo_basso) and not ha_gia_prodotti:
            n += 1
        target_salumi = n

    target_formaggi = None
    formaggi_match = re.search(rf"(altri\s+)?({num_pattern})\s+(?:tipi\s+di\s+|referenze\s+di\s+|qualit[àa]\s+di\s+)?(?:formaggi|formaggio|caci)", testo_basso)
    if formaggi_match:
        is_altri = bool(formaggi_match.group(1))
        n = _parse_numero_italiano(formaggi_match.group(2))
        if (is_altri or "oltre" in testo_basso) and not ha_gia_prodotti:
            n += 1
        target_formaggi = n

    if "altrettanti formaggi" in testo_basso or "altrettante referenze di formaggi" in testo_basso:
        target_formaggi = target_salumi if target_salumi is not None else 3
    elif "altrettanti salumi" in testo_basso:
        target_salumi = target_formaggi if target_formaggi is not None else 3

    ha_parole_salumi = bool(re.search(r"\b(salum[ie]|prosciutt[ie]|insaccat[ie]|speck|lardo|pancett[ae]|capocoll[oi]|bresaol[ae])\b", testo_basso))
    ha_parole_formaggi = bool(re.search(r"\b(formagg[ie]|caci[oi]|pecorin[oi]|parmigian[oi]|mozzarell[ae]|burrat[ae])\b", testo_basso))

    if ha_parole_formaggi and not ha_parole_salumi:
        if target_formaggi is None:
            target_formaggi = 3
        target_salumi = 0
    elif ha_parole_salumi and not ha_parole_formaggi:
        if target_salumi is None:
            target_salumi = 3
        target_formaggi = 0

    if target_salumi is None and target_formaggi is None:
        target_salumi = 3
        target_formaggi = 3
    else:
        if target_salumi is None:
            target_salumi = 3
        if target_formaggi is None:
            target_formaggi = 3

    return (target_salumi, target_formaggi)


PAROLE_PLURALI_SLOT = ["misti", "miste", "assortiti", "assortite", "selezione", "mix"]
N_ESPANSIONE_PLURALE = 3  # quanti prodotti distinti generare da uno slot plurale


# ====================================================================
# CLUSTER REGIONALI E TERRITORIALI PER TAGLIERI E MENU
# ====================================================================
def rileva_cluster_regionale(testo: str) -> "str | None":
    """Rileva se il testo fa riferimento a una specifica regione o paese
    (Spagna, Toscana, Puglia, ecc.), in base alle parole chiave dichiarate in
    fornitori_config.TEMI_REGIONALI. Aggiungere o modificare una regione si fa
    SOLO in fornitori_config.py, non qui."""
    t_lower = testo.lower()
    for reg, cfg in TEMI_REGIONALI.items():
        parole = cfg.get("parole_chiave", [])
        # le parole chiave lunghe sono spesso radici ("spagnol", "toscan"): match a inizio parola; quelle corte
        # ("parma", "sard") a parola intera, altrimenti "parmigiano" o "sardine" attiverebbero la regione
        if parole and any(re.search(r"\b" + re.escape(w) + ("" if len(w) >= 6 else r"\b"), t_lower) for w in parole):
            return reg
    return None


# Esclusioni di QUALITA' (non di brand): prodotti che, per il loro impiego
# specifico, non stanno bene in un tagliere anche se appartengono al reparto
# giusto (es. una crema spalmabile fusa non va tra i formaggi da affettare).
# Questa è l'unica lista "a mano" rimasta: non contiene MAI nomi di fornitore,
# solo caratteristiche del prodotto, quindi non va toccata quando cambia il
# catalogo fornitori.
_QUALITA_ESCLUSIONI_TAGLIERE = {
    "formaggi": ("julienne", "cubettata", "per pizza", "latte ", "burro", "besciamella",
                 "cremoso", "yogurt", "tiramis", "cacao", "caramello", "dessert", "dolce",
                 "pasticceria", "spalmabile", "fuso", "crema di formaggio",
                 "mozzarella", "burrata", "treccia", "stracciatella", "ricotta",
                 "carpinello", "calugi", "salsa", "mousse", "pesto",
                 # confezioni assortite da rivendita: non sono un formaggio da tagliere (chat reale: "Sfizi mix con espositore")
                 "espositore", "souvenir", "sfizi mix"),
    "salumi": ("wurstel", "würstel", "wurst", "trita", "macinato", "hamburger", "espositore", "souvenir"),
    "olive": ("sugo", "patè", "paté", "pate", "candit", "granell", "pastella", "crema", "carciof", "olio"),
}


from core.territorio import solo_regione  # noqa: E402  (regola condivisa con la ricerca prodotti)


# Extra che il cliente puo' aggiungere a un tagliere: ruolo (fornitori_config) -> radici che lo nominano.
# Per ogni radice, le parole che contano come "lo stesso tipo" (una marmellata va bene anche se si chiama confettura).
_SOTTOLIO = ("sott'olio", "sottolio", " in olio", "grigliat")
_EXTRA_TAGLIERE = {
    "sottoli": {"sottoli": _SOTTOLIO, "sott'oli": _SOTTOLIO, "sott'olio": _SOTTOLIO, "sottolio": _SOTTOLIO,
                "sott olio": _SOTTOLIO, "giardinier": ("giardinier",), "carciofin": ("carciof",)},
    "mostarde_confetture": {"marmellat": ("marmellat", "confettur"), "confettur": ("confettur", "marmellat"),
                            "mostard": ("mostard",), "compost": ("compost",), "miele": ("miele",)},
}
ETICHETTE_EXTRA = {"sottoli": "Sottoli", "mostarde_confetture": "Confetture/mostarde/miele"}
_NUMERI = {"un paio": 2, "una coppia": 2, "qualche": 2, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
           "un": 1, "uno": 1, "una": 1, "un'": 1}
_DEFAULT_EXTRA = {"sottoli": 2, "mostarde_confetture": 1}
_RE_NUMERO_PRIMA = re.compile(r"\b(\d+|un paio|una coppia|qualche|due|tre|quattro|cinque|un'|una|uno|un)\s+"
                              r"(?:di\s+)?(?:(?:vasett|barattol|vas|tip|referenz|confezion|piccol|buon)[a-z]*\s+(?:di\s+)?)?$", re.IGNORECASE)
_RE_DIMINUTIVO = re.compile(r"[a-z']+(in[aeio]|ett[aeio])\b")  # "marmellatina", "vasetto": formato piccolo
_RE_NUMERO = re.compile(r"(\d+|un paio|una coppia|qualche|due|tre|quattro|cinque|un'|una|uno|un)\b", re.IGNORECASE)


# parole del nome che non dicono l'ingrediente (tipo di preparazione, colore, formato)
_NON_INGREDIENTE = {"confe", "marme", "mosta", "compo", "grigl", "agrod", "sotto", "extra", "vergi", "rosse", "rossa",
                    "rossi", "verdi", "dolce", "dolci", "artig", "class", "natur", "olio", "filet", "trito", "crema"}


def _radici_nome(r: dict) -> set:
    """Radici (5 lettere) delle parole significative del nome: servono a non ripetere lo stesso ingrediente."""
    nome = prima_riga(r.get("document", "")).lower().replace("prodotto:", "")
    return {w[:5] for w in re.findall(r"[a-zàèéìòù]{5,}", nome)} - _NON_INGREDIENTE


def _per_nome_a_catalogo(radici, indice_testuale) -> list:
    """Prodotti di dispensa il cui nome contiene una delle radici, nel rispetto di dieta ed esclusioni del cliente."""
    dieta = _DIETA_CORRENTE.get()
    out = []
    for p in indice_testuale or []:
        m = p["metadata"]
        nome = prima_riga(p.get("document", "")).lower()
        if str(m.get("reparto") or "").upper() != "DISPENSA" or not any(r in nome for r in radici) or "oliv" in nome:
            continue
        if (dieta and not prodotto_compatibile_con_dieta(m, dieta)) or ontologia.prodotto_escluso_da_cliente(m, p.get("document", "")):
            continue
        out.append({"id": p["id"], "metadata": m, "document": p["document"], "match_esatto": False})
    return out


def extra_richiesti(testo: str) -> list:
    """[(ruolo, quantita', parole preferite)] per gli extra nominati ("un paio di sottoli e una marmellatina" ->
    sottoli x2, confetture x1 con preferenza marmellata/confettura). La quantita' e' l'ultimo numero nelle 25
    lettere prima della parola; senza numero, 1."""
    t = (testo or "").lower()
    out = []
    for ruolo, radici in _EXTRA_TAGLIERE.items():
        pos = [(t.find(r), r) for r in radici if r in t]
        if not pos:
            continue
        inizio, radice = min(pos)
        # il numero deve stare subito prima ("un paio di sottoli", "2 vasetti di sottoli"): in "formaggi 4 e 5;
        # aggiungi sottoli" il 5 e' dei formaggi (chat reale: 5 sottoli)
        m = _RE_NUMERO_PRIMA.search(t[max(0, inizio - 40):inizio])
        n = (_NUMERI.get(m.group(1).lower()) or (int(m.group(1)) if m.group(1).isdigit() else 1)) if m else _DEFAULT_EXTRA[ruolo]
        # "una marmellatina piccola": il formato piccolo vale per l'extra a cui si riferisce
        piccolo = bool(re.search(r"piccol|mini|monoporzion|vasett", t[inizio:inizio + 30])) or bool(_RE_DIMINUTIVO.match(t, inizio))
        out.append((ruolo, max(1, min(n, 5)), list(radici[radice]), piccolo))
    return out


_NOTA_FUORI_REGIONE = (" - NON e' della regione richiesta: aggiunto per variare le tipologie del tagliere, "
                       "dillo al cliente e non presentarlo come regionale")


def _fornitori_ammessi_per_slot(ruolo: str, regione: str) -> "list | None":
    """None = nessun vincolo (qualsiasi fornitore col ruolo giusto va bene).
    Lista (anche vuota) = solo i fornitori taggati per quel ruolo/regione in
    fornitori_config.py. Lista vuota = nessun fornitore disponibile: lo slot
    verrà saltato invece di proporre qualcosa fuori tema."""
    slugs = elenco_fornitori_per_ruolo_regione(ruolo, regione)
    if regione == "generico" and not slugs:
        return None
    nomi = []
    for slug in slugs:
        nomi.extend(FORNITORI[slug]["nomi_match"])
    # piu' tutti i produttori della regione in sofood/regioni_produttori.csv (core/territorio.py): il reparto del
    # componente (salumi, formaggi...) filtra gia' i prodotti, qui conta solo l'origine
    from core import territorio
    nomi += [n for n in territorio.produttori_della_regione(regione) if n not in nomi]
    return nomi


def _seleziona_componente_tagliere(ruolo: str, regione: str, collezione_prodotti, indice_codici,
                                    embedder, indice_fornitori, indice_testuale,
                                    filtro_reparto: "str | None" = None,
                                    filtro_categoria: "str | None" = None,
                                    filtro_sottocategoria: "str | None" = None,
                                    n_target: int = 1,
                                    query_utente: str = "",
                                    prodotti_esclusi: "set | None" = None,
                                    max_per_fornitore: int = 1,
                                    esclusioni_dinamiche: "list | None" = None,
                                    sottocategoria_forzata: "str | None" = None) -> list:
    """Motore GENERICO di selezione di una componente del tagliere (salumi,
    formaggi, mare, pane, olive, sottoli, mostarde_confetture, snack_secco,
    contorni), valido per QUALSIASI regione e QUALSIASI fornitore configurato
    in fornitori_config.py.

    Prima della riorganizzazione, ognuna di queste componenti aveva un blocco
    if/elif scritto a mano PER OGNI SINGOLA REGIONE (5-6 blocchi quasi
    identici, ripetuti per salumi, formaggi, pane, olive...): aggiungere un
    fornitore significava trovare e modificare il blocco giusto in mezzo a
    centinaia di righe. Ora basta aggiungere una entry in fornitori_config.py:
    questa funzione la trova e la usa automaticamente, per qualunque ruolo e
    qualunque regione, comprese quelle aggiunte in futuro."""
    if ruolo == "olive" and ontologia.indice_ha_attributi(indice_testuale):
        spec_olive = ontologia.risolvi_slot("olive da tavola", _QUERY_ORIGINALE.get() or query_utente)
        if spec_olive:
            return ontologia.scegli(spec_olive, indice_testuale, canale=_CANALE_CORRENTE.get(),
                                    esclusi=prodotti_esclusi, dieta=_DIETA_CORRENTE.get(), n=n_target)

    if ruolo == "pane" and ontologia.indice_ha_attributi(indice_testuale):
        # "aggiungi taralli": il croccante nominato dal cliente, non uno qualsiasi (chat reale: grissini al posto dei taralli)
        q_orig = _QUERY_ORIGINALE.get() or query_utente
        for fam in ("grissini_pane_croccante", "taralli_o_grissini"):
            spec_pane = ontologia.spec_famiglia(fam, q_orig) if ontologia.famiglia_nominata(fam, q_orig) else None
            sel_pane = ontologia.scegli(spec_pane, indice_testuale, canale=_CANALE_CORRENTE.get(), esclusi=prodotti_esclusi,
                                        dieta=_DIETA_CORRENTE.get(), n=n_target) if spec_pane else []
            if sel_pane:
                return sel_pane

    n_pool = max(n_target * 5, 15)

    tema_cfg = TEMI_REGIONALI.get(regione) or TEMI_REGIONALI["generico"]
    testo_tema = tema_cfg.get(ruolo) or TEMI_REGIONALI["generico"].get(ruolo, ruolo)
    if not testo_tema:
        return []

    fornitori_ammessi = _fornitori_ammessi_per_slot(ruolo, regione)
    # "solo salumi pugliesi" = vincolo rigido; "tagliere pugliese" = la regione viene prima, ma le famiglie che la
    # regione non ha (es. nessun crudo pugliese) si completano dal resto del catalogo per diversificare
    rigorosa = solo_regione(_QUERY_ORIGINALE.get() or query_utente)
    estendi = fornitori_ammessi is not None and not rigorosa and ruolo in ("salumi", "formaggi") and bool(indice_testuale)
    if fornitori_ammessi == [] and not estendi:
        return []

    testo_tema = f"{testo_tema} {query_utente}"

    candidati = cerca_prodotti(
        collezione_prodotti, indice_codici, embedder,
        testo_tema,
        n_risultati=n_pool,
        indice_fornitori=indice_fornitori,
        indice_testuale=indice_testuale,
        filtro_reparto=filtro_reparto,
        filtro_categoria=filtro_categoria,
        filtro_sottocategoria=filtro_sottocategoria,
        esclusioni=esclusioni_dinamiche
    )

    esclusioni = _QUALITA_ESCLUSIONI_TAGLIERE.get(ruolo, ())
    permetti_parole_esplicite = query_utente.lower()
    filtrati = []
    for r in candidati:
        doc_p = prima_riga(r["document"]).lower() if r.get("document") else ""
        forn_p = str((r["metadata"].get("nome_fornitore") or "")).lower()
        if esclusioni and any(w in doc_p for w in esclusioni) and not any(
            exp in permetti_parole_esplicite for exp in ("spalmabil", "crostin", "crema", "mozzarella", "burrata", "treccia", "stracciatella", "ricotta", "bufala")
        ):
            continue
        if ruolo in ("salumi", "formaggi") and r["metadata"].get("attributi_ok") and not r["metadata"].get("uso_tagliere"):
            continue
        if ruolo == "salumi":
            rep_p = str(r["metadata"].get("reparto", "")).upper()
            if "sfilaccio" in doc_p and "sfilaccio" not in permetti_parole_esplicite:
                continue
            if rep_p == "CARNE" and not any(w in doc_p for w in ["carpaccio", "salada", "bresaola"]):
                continue
        if ruolo == "pane" and not any(k in doc_p for k in ["tarall", "grissin", "carasau", "guttiau", "frisell", "pane", "picos", "cracker"]):
            continue
        if ruolo == "olive" and "oliv" not in doc_p:
            continue
        if fornitori_ammessi is not None and not any(fa in forn_p for fa in fornitori_ammessi):
            continue
        filtrati.append(r)

    if prodotti_esclusi:
        non_mostrati = [r for r in filtrati if r["id"] not in prodotti_esclusi]
        if len(non_mostrati) >= n_target:
            filtrati = non_mostrati
        else:
            gia_mostrati = [r for r in filtrati if r["id"] in prodotti_esclusi]
            filtrati = non_mostrati + gia_mostrati

    if ruolo in ("salumi", "formaggi") and indice_testuale:
        # Il pool della ricerca viene prima; in coda i prodotti da tagliere del catalogo che passano gli stessi filtri.
        # Cosi' ogni famiglia preferita ha un candidato (chat reale: tagliere senza nessun crudo perche' la ricerca
        # non ne aveva restituiti) e "degustazione di 5 formaggi" non si ferma a 2.
        filtrati = filtrati + _complemento_tagliere(ruolo, indice_testuale, {r["id"] for r in filtrati},
                                                    esclusioni, permetti_parole_esplicite, fornitori_ammessi,
                                                    prodotti_esclusi)
        if estendi:
            fuori = _complemento_tagliere(ruolo, indice_testuale, {r["id"] for r in filtrati}, esclusioni,
                                          permetti_parole_esplicite, None, prodotti_esclusi)
            filtrati = filtrati + [dict(r, fuori_regione=True) for r in fuori]

    n_forn_ruolo = len(elenco_fornitori_per_ruolo_regione(ruolo, regione))
    cap_forn = max(3, max_per_fornitore) if n_forn_ruolo <= 1 else max_per_fornitore
    forzata = bool(sottocategoria_forzata)
    return _scegli_vari(filtrati, n_target, ruolo, query_utente, cap_forn, forzata)


def _chiave_matrice(r: dict) -> str:
    """Chiave di diversita' storica (INCOMPATIBILITY_MATRIX o sottocategoria) per i prodotti senza famiglia."""
    for rule_name in INCOMPATIBILITY_MATRIX:
        if rule_name != "TERRA_MARE_MIX" and prodotto_appartiene_a_famiglia(r, rule_name):
            return rule_name
    return str(r["metadata"].get("sottocategoria", "")).lower()


def _scegli_vari(filtrati: list, n_target: int, ruolo: str, query_utente: str, cap_forn: int, forzata: bool) -> list:
    """Sceglie n_target prodotti VARI (core/famiglie_tagliere.py, studio in docs/STUDIO_ABBINAMENTI.md):
    1) una famiglia per volta (crudo, insaccato, muscolo... / duro, semiduro, erborinato...), prima quelle nominate
       dal cliente, poi l'ordine preferito; nella famiglia vince il piu' rilevante, preferendo un latte non ancora usato;
    2) se non bastano, si ripete una famiglia ma mai lo stesso tipo di formaggio ("due pecorini").
    Con una sottocategoria forzata dal cliente ("3 prosciutti di Parma") i vincoli di varieta' non si applicano."""
    from core import famiglie_tagliere as ft

    conteggio_forn: dict = {}
    selezionati: list = []

    def forn(r):
        return (r["metadata"].get("nome_fornitore") or "").lower()

    def prendi(r):
        selezionati.append(r)
        conteggio_forn[forn(r)] = conteggio_forn.get(forn(r), 0) + 1

    def ok_forn(r):
        return conteggio_forn.get(forn(r), 0) < cap_forn and r not in selezionati

    if forzata or ruolo not in ("salumi", "formaggi"):
        chiavi = {}
        for r in filtrati:
            k = _chiave_matrice(r)
            if ok_forn(r) and (forzata or chiavi.get(k, 0) < 1):
                prendi(r)
                chiavi[k] = chiavi.get(k, 0) + 1
            if len(selezionati) >= n_target:
                break
        return selezionati

    info = {r["id"]: (ft.famiglia_prodotto(r["metadata"], r.get("document", "")) or "_" + _chiave_matrice(r),
                      ft.tipo_base(r["metadata"], r.get("document", ""))[:6] if ruolo == "formaggi" else "",
                      ft.latte(r["metadata"], r.get("document", ""))) for r in filtrati}
    q = (query_utente or "").lower()
    ordine = ft.ordine_famiglie(ruolo)
    nominate = [f for f in ordine if any(p.strip() and p.strip() in q
                                         for p in (ft._cfg().get(ruolo, {}).get("famiglie", {}).get(f) or []))]
    ordine = nominate + [f for f in ordine if f not in nominate]
    ordine += sorted({v[0] for v in info.values() if v[0] not in ordine})  # famiglie non riconosciute, in coda

    # tratti che stancano se ripetuti (due piccanti, due affumicati: abbinamenti_horeca.yaml), salvo richiesta del cliente
    tratti_cfg = {t: p for t, p in (ft._cfg().get("tratti_da_non_ripetere") or {}).items() if not any(w in q for w in p)}

    def tratti(r):
        nome = prima_riga(r.get("document", "")).lower()
        return {t for t, parole in tratti_cfg.items() if any(w in nome for w in parole)}

    fam_usate, basi_usate, latti_usati, tratti_usati = set(), set(), set(), set()
    # giro 1: una famiglia per volta
    for fam in ordine:
        if len(selezionati) >= n_target:
            break
        cand = [r for r in filtrati if info[r["id"]][0] == fam and ok_forn(r)
                and not (info[r["id"]][1] and info[r["id"]][1] in basi_usate)]
        if not cand:
            continue
        # prima i prodotti della regione richiesta, poi un tratto non ancora presente, poi un latte non ancora usato
        r = min(cand, key=lambda x: (bool(x.get("fuori_regione")), bool(tratti(x) & tratti_usati),
                                     info[x["id"]][2] in latti_usati))
        prendi(r)
        fam_usate.add(fam)
        basi_usate.add(info[r["id"]][1])
        latti_usati.add(info[r["id"]][2])
        tratti_usati |= tratti(r)
    # giro 2: famiglie ripetute (piu' prodotti che famiglie), mai lo stesso tipo di formaggio
    for r in sorted(filtrati, key=lambda x: bool(tratti(x) & tratti_usati)):
        if len(selezionati) >= n_target:
            break
        b = info[r["id"]][1]
        if ok_forn(r) and not (b and b in basi_usate):
            prendi(r)
            basi_usate.add(b)
            tratti_usati |= tratti(r)
    # i prodotti scelti tornano nell'ordine delle famiglie (struttura leggibile: crudo, insaccato, ... / duro, ...)
    return selezionati


def _complemento_tagliere(ruolo: str, indice_testuale: list, gia: set, esclusioni_qualita, parole_esplicite: str,
                          fornitori_ammessi, prodotti_esclusi) -> list:
    """Prodotti da tagliere del reparto (salumi/formaggi) che rispettano dieta, esclusioni del cliente, zona di
    consegna, esclusioni di qualita' e fornitori ammessi. Ordine: non ancora proposti, poi id (deterministico)."""
    rep = "SALUMI" if ruolo == "salumi" else "FORMAGGI"
    dieta = _DIETA_CORRENTE.get()
    out = []
    for p in indice_testuale:
        m = p["metadata"]
        if p["id"] in gia or str(m.get("reparto") or "").upper() != rep:
            continue
        if m.get("attributi_ok") and not m.get("uso_tagliere"):
            continue
        if m.get("richiede_cottura"):
            continue
        nome = prima_riga(p["document"]).lower() if p.get("document") else ""
        if esclusioni_qualita and any(w in nome for w in esclusioni_qualita) and not any(
                e in parole_esplicite for e in ("spalmabil", "crema", "mozzarella", "burrata", "ricotta", "bufala")):
            continue
        if fornitori_ammessi is not None and not any(fa in str(m.get("nome_fornitore") or "").lower() for fa in fornitori_ammessi):
            continue
        if dieta and not prodotto_compatibile_con_dieta(m, dieta):
            continue
        if ontologia.prodotto_escluso_da_cliente(m, p.get("document", "")):
            continue
        out.append({"id": p["id"], "metadata": m, "document": p["document"], "match_esatto": False})
    canale = (_CANALE_CORRENTE.get() or "").lower()
    out.sort(key=lambda r: (r["id"] in (prodotti_esclusi or ()),
                            bool(canale in ("horeca", "retail") and r["metadata"].get("canale_formato") != canale),
                            "souvenir" in r["document"].lower() or "espositore" in r["document"].lower(),
                            r["id"]))
    return out


def _riempi_slot_ricetta_base(template: dict, collezione_prodotti, indice_codici: dict, embedder,
                         indice_fornitori: "dict | None" = None,
                         indice_testuale: "list | None" = None,
                         target_salumi: int = 3,
                         target_formaggi: int = 3,
                         query_utente: str = "",
                         prodotti_esclusi: "set | None" = None,
                         piano_ricerca: "dict | None" = None) -> list:
    """Step 2: per ogni ingrediente generico del template, cerca il prodotto
    reale a catalogo con la ricerca ibrida (cerca_prodotti).
    Se il template è un tagliere o aperitivo, espande dinamicamente il numero
    di salumi e formaggi a target_salumi/target_formaggi, rispettando la
    territorialità tramite fornitori_config.py: qui NON compare mai un nome
    di fornitore hardcoded. Aggiungere/togliere un fornitore o una regione si
    fa esclusivamente in fornitori_config.py."""
    id_ricetta = template.get("id_ricetta") or template.get("id", "")
    is_tagliere = (
        template.get("categoria") == "tagliere"
        or "tagliere" in str(template.get("nome_piatto", "")).lower()
        or any(w in query_utente.lower() for w in ["tagliere", "tapas"])
    ) and id_ricetta not in ["TAGLIERE_MARE_ITALFISH"]
    if (_DIETA_CORRENTE.get() or "") in ("vegano", "vegetariano"):
        # Il ramo tagliere espande sempre salumi + formaggi: con una dieta
        # vegetale si usano gli ingredienti del template (test reale R6).
        is_tagliere = False

    if is_tagliere:
        slot_riempiti = []
        # regioni con un tema (fornitori_config.TEMI_REGIONALI) o, per le altre ("tagliere lucano"), quella nominata
        # dal cliente: i produttori arrivano da sofood/regioni_produttori.csv
        from core import territorio
        regione = rileva_cluster_regionale(
            f"{query_utente} {template.get('nome_piatto', '')} {template.get('STILE_CUCINA', template.get('stile_cucina', ''))}"
        ) or territorio.regione_richiesta(_QUERY_ORIGINALE.get() or query_utente) or "generico"
        is_spagnolo = regione == "spagna"
        is_mare = any(w in query_utente.lower() for w in ["mare", "pesce", "ittic", "polpo", "tonno", "salmone"]) or (id_ricetta == "TAGLIERE_MARE_ITALFISH")

        tema_regione = TEMI_REGIONALI.get(regione) or TEMI_REGIONALI["generico"]
        def get_comps_piano(ruolo_target: str) -> list:
            comps = []
            if not piano_ricerca or "componenti" not in piano_ricerca:
                return comps
            for c in piano_ricerca.get("componenti", []):
                if c.get("ruolo") == ruolo_target:
                    comps.append(c)
            return comps
            
        def get_comp_piano(ruolo_target: str) -> dict:
            comps = get_comps_piano(ruolo_target)
            return comps[0] if comps else {}

        # 1. Salumi
        comps_salumi = get_comps_piano("salumi")
        if not comps_salumi:
            comps_salumi = [{"ruolo": "salumi", "quantita_target": target_salumi, "query_pulita": query_utente}]
        for idx_comp, comp_salumi in enumerate(comps_salumi):
            ts = target_salumi if idx_comp == 0 else 0
            if comp_salumi.get("quantita_target") is not None:
                ts = int(comp_salumi.get("quantita_target"))
                
            if ts > 0:
                if is_mare:
                    sel_salumi = _seleziona_componente_tagliere(
                        "mare", "generico", collezione_prodotti, indice_codici, embedder,
                        indice_fornitori, indice_testuale, filtro_reparto="MARE",
                        n_target=ts, query_utente=comp_salumi.get("query_pulita", query_utente),
                        prodotti_esclusi=prodotti_esclusi,
                        esclusioni_dinamiche=comp_salumi.get("esclusioni"),
                        sottocategoria_forzata=comp_salumi.get("sottocategoria_forzata"),
                        max_per_fornitore=max(3, ts)
                    )
                    etichetta = "Salumi o affettati di mare"
                else:
                    sel_salumi = _seleziona_componente_tagliere(
                        "salumi", regione, collezione_prodotti, indice_codici, embedder,
                        indice_fornitori, indice_testuale, filtro_reparto="SALUMI",
                        n_target=ts, query_utente=comp_salumi.get("query_pulita", query_utente),
                        prodotti_esclusi=prodotti_esclusi,
                        esclusioni_dinamiche=comp_salumi.get("esclusioni"),
                        sottocategoria_forzata=comp_salumi.get("sottocategoria_forzata"),
                        max_per_fornitore=max(3, ts)
                    )
                    etichetta = "Salumi artigianali"
                if not sel_salumi:
                    slot_riempiti.append({
                        "ingrediente_richiesto": f"{etichetta} (nessun match per {regione or 'generico'})",
                        "categoria_attesa": "Salumi" if not is_mare else "Mare",
                        "ruolo": "protagonista",
                        "note_ingrediente": "affettati o da morsa",
                        "esito": "NON_TROVATO",
                        "prodotto_trovato": None,
                    })
                else:
                    for idx, prod in enumerate(sel_salumi):
                        slot_riempiti.append({
                            "ingrediente_richiesto": f"{etichetta} ({idx_comp+1}.{idx+1})" + _NOTA_FUORI_REGIONE * bool(prod.get("fuori_regione")),
                            "categoria_attesa": "Salumi" if not is_mare else "Mare",
                            "ruolo": "protagonista",
                            "note_ingrediente": "affettati o da morsa",
                            "esito": "TROVATO",
                            "prodotto_trovato": prod,
                        })

        # 2. Formaggi
        comps_formaggi = get_comps_piano("formaggi")
        if not comps_formaggi:
            comps_formaggi = [{"ruolo": "formaggi", "quantita_target": target_formaggi, "query_pulita": query_utente}]
        for idx_comp, comp_formaggi in enumerate(comps_formaggi):
            tf = target_formaggi if idx_comp == 0 else 0
            if comp_formaggi.get("quantita_target") is not None:
                tf = int(comp_formaggi.get("quantita_target"))
                
            vuole_formaggi_esplicito = any(w in query_utente.lower() for w in ["formagg", "cacio", "pecorin"]) or bool(comp_formaggi)
            formaggi_di_default = tema_regione.get("formaggi_di_default", True) and not is_mare
            abilita_formaggi = tf > 0 and (formaggi_di_default or vuole_formaggi_esplicito)
    
            if abilita_formaggi:
                sel_formaggi = _seleziona_componente_tagliere(
                    "formaggi", regione, collezione_prodotti, indice_codici, embedder,
                    indice_fornitori, indice_testuale, filtro_categoria="Formaggi",
                    n_target=tf, query_utente=comp_formaggi.get("query_pulita", query_utente),
                    prodotti_esclusi=prodotti_esclusi,
                    esclusioni_dinamiche=comp_formaggi.get("esclusioni"),
                    sottocategoria_forzata=comp_formaggi.get("sottocategoria_forzata"),
                    max_per_fornitore=max(3, tf)
                )
                if not sel_formaggi:
                    slot_riempiti.append({
                        "ingrediente_richiesto": f"Formaggi da degustazione (nessun match per {regione or 'generico'})",
                        "categoria_attesa": "Formaggi",
                        "ruolo": "protagonista",
                        "note_ingrediente": "pasta dura o semidura da tavola",
                        "esito": "NON_TROVATO",
                        "prodotto_trovato": None,
                    })
                else:
                    for idx, prod in enumerate(sel_formaggi):
                        slot_riempiti.append({
                            "ingrediente_richiesto": f"Formaggi da degustazione ({idx_comp+1}.{idx+1})" + _NOTA_FUORI_REGIONE * bool(prod.get("fuori_regione")),
                            "categoria_attesa": "Formaggi",
                            "ruolo": "protagonista",
                            "note_ingrediente": "pasta dura o semidura da tavola",
                            "esito": "TROVATO",
                            "prodotto_trovato": prod,
                        })

        # 3. Componente aggiuntiva di mare/tapas
        if is_spagnolo:
            comp_mare = get_comp_piano("mare")
            sel_tapas = _seleziona_componente_tagliere(
                "mare", "spagna", collezione_prodotti, indice_codici, embedder,
                indice_fornitori, indice_testuale, filtro_reparto="MARE",
                n_target=1, query_utente=comp_mare.get("query_pulita", query_utente), 
                prodotti_esclusi=prodotti_esclusi,
                esclusioni_dinamiche=comp_mare.get("esclusioni"),
                sottocategoria_forzata=comp_mare.get("sottocategoria_forzata")
            )
            if sel_tapas:
                slot_riempiti.append({
                    "ingrediente_richiesto": "Acciughe o conserve di mare da tapas",
                    "categoria_attesa": "Mare",
                    "ruolo": "secondario",
                    "note_ingrediente": "stile spagnolo",
                    "esito": "TROVATO",
                    "prodotto_trovato": sel_tapas[0],
                })
        elif is_mare:
            candidati_tartare = cerca_prodotti(
                collezione_prodotti, indice_codici, embedder,
                "tartare o carpaccio di pesce crudo da degustazione", n_risultati=8,
                indice_fornitori=indice_fornitori, indice_testuale=indice_testuale,
                filtro_reparto="MARE",
            )
            sel_tartare = [
                r for r in candidati_tartare
                if any(w in (prima_riga(r["document"]).lower() if r.get("document") else "")
                       for w in ("tartare", "carpaccio", "crud"))
            ][:1]
            if sel_tartare:
                slot_riempiti.append({
                    "ingrediente_richiesto": "Tartare o carpaccio di mare",
                    "categoria_attesa": "Mare",
                    "ruolo": "protagonista",
                    "note_ingrediente": "in ciotolina da degustazione con filo d'olio a crudo",
                    "esito": "TROVATO",
                    "prodotto_trovato": sel_tartare[0],
                })

        # 4. Accompagnamento croccante (pane/taralli/grissini)
        comp_pane = get_comp_piano("pane")
        sel_pane = _seleziona_componente_tagliere(
            "pane", regione, collezione_prodotti, indice_codici, embedder,
            indice_fornitori, indice_testuale, filtro_categoria="Dispensa",
            n_target=1, query_utente=comp_pane.get("query_pulita", query_utente),
            prodotti_esclusi=prodotti_esclusi,
            esclusioni_dinamiche=comp_pane.get("esclusioni"),
            sottocategoria_forzata=comp_pane.get("sottocategoria_forzata")
        )
        if sel_pane:
            slot_riempiti.append({
                "ingrediente_richiesto": "Accompagnamento croccante",
                "categoria_attesa": "Dispensa",
                "ruolo": "secondario",
                "note_ingrediente": "pane, taralli o grissini di accompagnamento",
                "esito": "TROVATO",
                "prodotto_trovato": sel_pane[0],
            })

        # 5. Olive da tavola
        comp_olive = get_comp_piano("olive")
        vuole_olive = (
            "oliv" in query_utente.lower()
            or bool(comp_olive)
            or (tema_regione.get("olive_di_default", False))
            or (regione == "spagna" or "spagn" in query_utente.lower())
        )
        if vuole_olive:
            sel_olive = _seleziona_componente_tagliere(
                "olive", regione, collezione_prodotti, indice_codici, embedder,
                indice_fornitori, indice_testuale, filtro_reparto="DISPENSA",
                n_target=1, query_utente=comp_olive.get("query_pulita", query_utente),
                prodotti_esclusi=prodotti_esclusi,
                esclusioni_dinamiche=comp_olive.get("esclusioni"),
                sottocategoria_forzata=comp_olive.get("sottocategoria_forzata")
            )
            if sel_olive:
                slot_riempiti.append({
                    "ingrediente_richiesto": "Olive da tavola per aperitivo",
                    "categoria_attesa": "Dispensa",
                    "ruolo": "secondario",
                    "note_ingrediente": "olive verdi o nere, da servire in ciotolina",
                    "esito": "TROVATO",
                    "prodotto_trovato": sel_olive[0],
                })

        # 6. Extra chiesti dal cliente ("un paio di sottoli e una marmellatina"): ognuno con la sua quantita', fra
        # tutti i produttori configurati per quel ruolo (chat reale: se ne prendeva uno solo e i sottoli sparivano).
        # Se il cliente non ne chiede, una specialita' territoriale come complemento.
        extra = extra_richiesti(_QUERY_ORIGINALE.get() or query_utente)
        for ruolo_extra, n_extra, preferite, piccolo in extra:
            scelti = []
            for reg_prova in [regione] + [r for r in TEMI_REGIONALI if r != regione]:
                if len(scelti) >= n_extra + 6:
                    break
                if not elenco_fornitori_per_ruolo_regione(ruolo_extra, reg_prova):
                    continue
                gia = (prodotti_esclusi or set()) | {r["id"] for r in scelti}
                scelti += [r for r in _seleziona_componente_tagliere(
                    ruolo_extra, reg_prova, collezione_prodotti, indice_codici, embedder,
                    indice_fornitori, indice_testuale, filtro_categoria="Dispensa", n_target=n_extra + 4,
                    query_utente=" ".join(preferite), prodotti_esclusi=gia) if r["id"] not in gia]
            # prima quelli del tipo nominato (marmellata/confettura prima della mostarda), poi ingredienti non ancora
            # presenti nel tagliere (chat reale: cipolla in agrodolce + confettura di cipolle)
            # in coda i prodotti del catalogo che si chiamano proprio cosi' ("marmellata", "confettura"): i produttori
            # configurati per il ruolo sono pochi (chat reale: solo la confettura di cipolle, nessuna marmellata di frutta)
            gia = (prodotti_esclusi or set()) | {r["id"] for r in scelti}
            scelti += [r for r in _per_nome_a_catalogo(preferite, indice_testuale) if r["id"] not in gia]
            # condimenti (capperi, aglio, pesto...): non si servono da soli sul tagliere (chat reale: capperi in olio)
            from core import famiglie_tagliere as _ft
            condimenti = _ft._cfg().get("condimenti_non_da_tagliere") or []
            scelti = [r for r in scelti if not any(c in prima_riga(r.get("document", "")).lower() for c in condimenti)]
            usate = set().union(*[_radici_nome(x["prodotto_trovato"]) for x in slot_riempiti if x.get("prodotto_trovato")])
            presi, forn_presi = [], set()
            while scelti and len(presi) < n_extra:
                r = min(scelti, key=lambda x: (not any(w in prima_riga(x.get("document", "")).lower() for w in preferite),
                                               bool(_radici_nome(x) & usate),
                                               str(x["metadata"].get("nome_fornitore")) in forn_presi,
                                               (formato_prodotto(x["metadata"], x.get("document", "")).get("valore") or 9e9) if piccolo else 0))
                scelti.remove(r)
                presi.append(r)
                usate |= _radici_nome(r)
                forn_presi.add(str(r["metadata"].get("nome_fornitore")))
            etichetta = ETICHETTE_EXTRA[ruolo_extra]
            for k in range(n_extra):
                prod = presi[k] if k < len(presi) else None
                slot_riempiti.append({
                    "ingrediente_richiesto": f"{etichetta} chiesti dal cliente ({k + 1} di {n_extra})",
                    "categoria_attesa": "Dispensa", "ruolo": "protagonista",
                    "esito": "TROVATO" if prod else "NON_TROVATO", "prodotto_trovato": prod,
                })
        if not extra:
            for ruolo_extra in ("sottoli", "mostarde_confetture", "snack_secco"):
                if not elenco_fornitori_per_ruolo_regione(ruolo_extra, regione):
                    continue
                sel_extra = _seleziona_componente_tagliere(
                    ruolo_extra, regione, collezione_prodotti, indice_codici, embedder,
                    indice_fornitori, indice_testuale, filtro_categoria="Dispensa",
                    n_target=1, query_utente=query_utente, prodotti_esclusi=prodotti_esclusi,
                )
                if sel_extra:
                    slot_riempiti.append({
                        "ingrediente_richiesto": "Specialità territoriale",
                        "categoria_attesa": "Dispensa",
                        "ruolo": "secondario",
                        "note_ingrediente": ruolo_extra.replace("_", " "),
                        "esito": "TROVATO",
                        "prodotto_trovato": sel_extra[0],
                    })
                    break

        return slot_riempiti


    # Flusso standard per ricette diverse dal tagliere (primi, secondi, pizze, ecc.)
    query_u_lower = query_utente.lower()
    from core.anagrafica_fornitori import ANAGRAFICA
    fornitori_in_query = [f for f in ANAGRAFICA.fornitori_in_testo(query_utente) if f in (indice_fornitori or {})]

    slot_riempiti = []
    _forn_usati_ricetta = set()
    ingredienti_template = template.get("ingredienti") or template.get("slot", [])
    if isinstance(ingredienti_template, str):
        ingredienti_template = parse_db_json_field(ingredienti_template, [])
    for ingr in ingredienti_template:
        if isinstance(ingr, str):
            ingr = {"INGREDIENTE_GENERICO": ingr, "CATEGORIA_ATTESA": "", "RUOLO": "opzionale", "NOTE_INGREDIENTE": ""}
        nome_ingr = ingr.get("INGREDIENTE_GENERICO") or ingr.get("ingrediente_generico", str(ingr))
        categoria_attesa = str(ingr.get("CATEGORIA_ATTESA", "")).lower()
        ruolo = str(ingr.get("RUOLO", "opzionale")).lower()
        note = str(ingr.get("NOTE_INGREDIENTE") or "")

        # Regole di dominio sugli attributi (catalogo_v2): se lo slot e' una famiglia nota
        # (olive da tavola, taralli, frutta secca...) si scelgono SOLO prodotti che la
        # rispettano; se non ce ne sono lo slot resta NON_TROVATO (niente ripiego vettoriale
        # che porterebbe un patè di olive al posto delle olive).
        # Formato di pasta (core/formati_pasta.py): spaghetti = spaghetti, il formato nominato dal cliente vince, un
        # formato vicino si dichiara; gnocchi di patate o pasta ripiena che non ci sono restano "non a catalogo"
        esito_pasta = formati_pasta.scegli(nome_ingr, _QUERY_ORIGINALE.get() or query_utente, indice_testuale or [],
                                           prodotti_esclusi, _CANALE_CORRENTE.get())
        if esito_pasta is not None:
            prod_pasta, esatto = esito_pasta
            if prod_pasta and prodotti_esclusi is not None:
                prodotti_esclusi.add(prod_pasta["id"])
            richiesto = formati_pasta.formato_di(_QUERY_ORIGINALE.get() or "") if not formati_pasta.formato_di(nome_ingr) else None
            etichetta = nome_ingr + (f" (formato chiesto dal cliente: {richiesto})" if richiesto else "")
            if prod_pasta and not esatto:
                etichetta += (f" - questo formato non e' a catalogo: proposto un formato vicino "
                              f"({formati_pasta.formato_di(prima_riga(prod_pasta['document']))}), dillo al cliente")
            slot_riempiti.append({
                "ingrediente_richiesto": etichetta, "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""), "ruolo": ruolo,
                "note_ingrediente": note, "esito": "TROVATO" if prod_pasta else "NON_TROVATO", "prodotto_trovato": prod_pasta,
                "senza_alternative": not prod_pasta,  # niente ripiego per parole simili ("preparato per patate")
            })
            continue

        spec_ont = ontologia.risolvi_slot(nome_ingr, _QUERY_ORIGINALE.get() or query_utente)
        if spec_ont and ontologia.indice_ha_attributi(indice_testuale):
            sel_ont = ontologia.scegli(spec_ont, indice_testuale, canale=_CANALE_CORRENTE.get(),
                                       esclusi=prodotti_esclusi, dieta=_DIETA_CORRENTE.get(), n=1,
                                       fornitori_usati=_forn_usati_ricetta)
            if sel_ont and prodotti_esclusi is not None:
                prodotti_esclusi.add(sel_ont[0]["id"])  # due slot della stessa ricetta non ripetono il prodotto
            if sel_ont:
                _forn_usati_ricetta.add(str(sel_ont[0]["metadata"].get("nome_fornitore") or ""))
            slot_riempiti.append({
                "ingrediente_richiesto": nome_ingr,
                "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""),
                "ruolo": ruolo,
                "note_ingrediente": note,
                "esito": "TROVATO" if sel_ont else "NON_TROVATO",
                "prodotto_trovato": sel_ont[0] if sel_ont else None,
                "motivo_scelta": sel_ont[0]["motivo_scelta"] if sel_ont else spec_ont["motivo"] + ": nessun prodotto a catalogo",
            })
            continue

        q_cerca = nome_ingr
        # Adattamento con brand esplicito: se il cliente nomina un fornitore
        # nella richiesta, e quel fornitore è pertinente per questo
        # ingrediente, la query di ricerca combina ingrediente + nome del
        # brand. Niente più liste manuali "hamburger oberto", "pasta
        # gentile"...: il legame ingrediente<->fornitore è generico (il
        # brand è pertinente se il suo ruolo dichiarato in
        # fornitori_config.py copre la categoria attesa, o se il fornitore
        # non ha un ruolo da tagliere dichiarato ma il nome dell'ingrediente
        # compare comunque vicino al brand nella richiesta del cliente).
        # Vale automaticamente anche per un fornitore aggiunto oggi in
        # fornitori_config.py, senza dover toccare questo file.
        _RUOLO_PER_CATEGORIA_ATTESA = {
            "salumi": "salumi", "carne": "salumi", "mare": "mare", "pesce": "mare",
            "formaggi": "formaggi", "dispensa": "pane",
        }
        for forn_k in fornitori_in_query:
            slug = match_fornitore(forn_k)
            ruoli_forn = FORNITORI.get(slug, {}).get("ruoli", []) if slug else []
            ruolo_atteso = _RUOLO_PER_CATEGORIA_ATTESA.get(categoria_attesa)
            pertinente = (ruolo_atteso and ruolo_atteso in ruoli_forn) or (
                not ruoli_forn and any(w in nome_ingr.lower() for w in forn_k.split())
            ) or (not ruoli_forn and forn_k in query_u_lower and categoria_attesa in ("dispensa", "carne", "mare"))
            if pertinente:
                q_cerca = f"{nome_ingr} {forn_k}"
                break

        # Se la salsa è richiesta esplicitamente (ketchup / maionese)
        if any(w in nome_ingr.lower() for w in ["salsa", "salse"]):
            if any(w in query_u_lower for w in ["ketchup", "kechup", "chechup"]):
                q_cerca = "ketchup biologico Biobontà"
            elif any(w in query_u_lower for w in ["maionese", "mayo"]):
                q_cerca = "maionese Biobontà"
            elif "senape" in query_u_lower:
                q_cerca = "senape Biobontà"

        is_plurale = any(p in nome_ingr.lower() for p in PAROLE_PLURALI_SLOT)

        if is_plurale:
            risultati = cerca_prodotti(
                collezione_prodotti, indice_codici, embedder,
                q_cerca, n_risultati=N_ESPANSIONE_PLURALE * 4,
                indice_fornitori=indice_fornitori,
                indice_testuale=indice_testuale,
                filtro_categoria=ingr.get("CATEGORIA_ATTESA"),
            )
            if categoria_attesa:
                filtrati = [r for r in risultati
                            if categoria_attesa in str(r["metadata"].get("categoria_prodotto", "")).lower()
                            or categoria_attesa in str(r["metadata"].get("reparto", "")).lower()
                            or categoria_attesa in str(r["metadata"].get("categoria_tassonomia", "")).lower()]
                risultati = filtrati

            visti_fornitori = set()
            selezionati = []
            for r in risultati:
                forn = (r["metadata"].get("nome_fornitore") or "").lower()
                if forn not in visti_fornitori:
                    visti_fornitori.add(forn)
                    selezionati.append(r)
                if len(selezionati) >= N_ESPANSIONE_PLURALE:
                    break

            for idx, prod in enumerate(selezionati):
                slot_riempiti.append({
                    "ingrediente_richiesto": f"{nome_ingr} ({idx+1})",
                    "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""),
                    "ruolo": ruolo,
                    "note_ingrediente": note,
                    "esito": "TROVATO",
                    "prodotto_trovato": prod,
                })
            if not selezionati:
                slot_riempiti.append({
                    "ingrediente_richiesto": nome_ingr,
                    "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""),
                    "ruolo": ruolo,
                    "note_ingrediente": note,
                    "esito": "NON_TROVATO",
                    "prodotto_trovato": None,
                })
        else:
            risultati = cerca_prodotti(
                collezione_prodotti, indice_codici, embedder,
                q_cerca, n_risultati=5,
                indice_fornitori=indice_fornitori,
                indice_testuale=indice_testuale,
                filtro_categoria=ingr.get("CATEGORIA_ATTESA"),
            )
            candidati = [
                r for r in risultati
                if categoria_attesa in str(r["metadata"].get("categoria_prodotto", "")).lower()
                or categoria_attesa in str(r["metadata"].get("reparto", "")).lower()
                or categoria_attesa in str(r["metadata"].get("categoria_tassonomia", "")).lower()
            ] if categoria_attesa else risultati
            # il reparto atteso dalla ricetta e' solo un indizio (le uova Scudellaro stanno in "Carne > Altre carni", la
            # carbonara le cerca in "Dispensa" e Nino diceva che non c'erano): prima i prodotti che si CHIAMANO come
            # l'ingrediente, ovunque siano; poi i pertinenti della ricerca ("tuorli d'uovo" non sono i savoiardi all'uovo)
            per_nome = [r for r in _per_parola_principale(nome_ingr, indice_testuale) if _prodotto_pertinente(nome_ingr, r)]
            ids_nome = {r["id"] for r in per_nome}
            pertinenti = [r for r in candidati if _prodotto_pertinente(nome_ingr, r)]
            candidati = [r for r in pertinenti if r["id"] in ids_nome] or per_nome or pertinenti or candidati

            if "salsiccia" in nome_ingr.lower() and "fegato" not in (query_u_lower + " " + nome_ingr.lower() + " " + note.lower()):
                senza_fegato = [r for r in candidati if "fegato" not in str(r.get("document", "")).lower()]
                if senza_fegato:
                    candidati = senza_fegato

            if "fungh" in nome_ingr.lower() and not any(k in nome_ingr.lower() for k in ["risotto", "pasta"]):
                senza_carboidrati = [r for r in candidati if not any(k in str(r.get("document", "")).lower() for k in ["risotto", "riso ", "pasta "])]
                if senza_carboidrati:
                    candidati = senza_carboidrati

            is_pizza_ricetta = any(k in str(template.get("categoria", "")).lower() for k in ["pizza", "pinsa"]) or any(k in str(template.get("nome_piatto", "")).lower() for k in ["pizza", "padellino", "pinsa"])
            is_dolce_ricetta = any(k in str(template.get("categoria", "")).lower() for k in ["dolce", "dessert"]) or any(k in str(template.get("nome_piatto", "")).lower() for k in ["gelato", "dolce", "sorbetto", "dessert"])

            if is_pizza_ricetta:
                # Per pizze e pinse: MAI sughi pronti da pasta per gli slot pomodoro/passata/pelati o erbe
                if any(w in nome_ingr.lower() for w in ["pomodoro", "passata", "pelati", "polpa", "datterini", "salsa"]):
                    senza_sughi_pasta = [r for r in candidati if not any(k in str(r.get("document", "")).lower() for k in ["sugo pronto", "ragù", "ragu", "pasta ", "calamarata"])]
                    if senza_sughi_pasta:
                        candidati = senza_sughi_pasta
                if any(w in nome_ingr.lower() for w in ["basilico", "origano"]):
                    senza_sughi = [r for r in candidati if not any(k in str(r.get("document", "")).lower() for k in ["sugo ", "ragù", "ragu"])]
                    if senza_sughi:
                        candidati = senza_sughi

            if is_dolce_ricetta:
                # Per dolci e gelati: MAI ingredienti salati, oli, aceti, conserve sott'olio o piccanti
                candidati_dolci = [r for r in candidati if not any(k in str(r.get("document", "")).lower() for k in ["olio extravergine", "olio evo", "peperoncino", "piccante", "sottolio", "sott'olio", "aceto", "capperi", "olive ", "sale "])]
                candidati = candidati_dolci

            slot_riempiti.append({
                "ingrediente_richiesto": nome_ingr,
                "categoria_attesa": ingr.get("CATEGORIA_ATTESA", ""),
                "ruolo": ruolo,
                "note_ingrediente": note,
                "esito": "NON_TROVATO",
                "prodotto_trovato": candidati[0] if candidati else None,
            })

    # Aggiunta dinamica per ingredienti specifici espliciti richiesti dall'utente
    INGREDIENTI_EXTRA_CHECK = [
        ("capperi", "capperi di Pantelleria IGP al sale La Nicchia"),
        ("cucunci", "cucunci La Nicchia"),
        ("ketchup", "ketchup biologico Biobontà"),
        ("maionese", "maionese Biobontà"),
        ("senape", "senape Biobontà"),
        ("bacon", "pancetta tesa in conca di marmo Adò bacon"),
        ("pancetta", "pancetta tesa in conca di marmo Adò"),
        ("acciughe", "filetti di acciughe del Cantabrico Medimer"),
        ("alici", "alici marinate o deliscate"),
        ("olive", "olive da tavola in salamoia"),
        ("pomodorini", "datterini pomodorini Così Com'è"),
        ("datterini", "datterini Così Com'è"),
        ("burrata", "burrata pugliese"),
        ("stracciatella", "stracciatella"),
        ("scamorza", "scamorza affumicata"),
    ]
    # "richiesto esplicitamente" = scritto dal CLIENTE: la query espansa dall'analisi ("carbonara guanciale pancetta...")
    # aggiungeva ingredienti mai chiesti (E2E reale: pancetta in piu' nella carbonara)
    richiesta_cliente_lower = (_QUERY_ORIGINALE.get() or query_utente).lower()
    for ingr_kw, query_cerca in INGREDIENTI_EXTRA_CHECK:
        if re.search(r"\b" + re.escape(ingr_kw) + r"\b", richiesta_cliente_lower):
            gia_coperto = any(
                ingr_kw in str(s.get("ingrediente_richiesto", "")).lower()
                or (s.get("prodotto_trovato") and ingr_kw in s["prodotto_trovato"]["document"].lower())
                for s in slot_riempiti
            )
            if not gia_coperto:
                res_extra = cerca_prodotti(
                    collezione_prodotti, indice_codici, embedder,
                    query_cerca, n_risultati=3,
                    indice_fornitori=indice_fornitori,
                    indice_testuale=indice_testuale,
                )
                if res_extra:
                    slot_riempiti.append({
                        "ingrediente_richiesto": f"{ingr_kw.capitalize()} (richiesto esplicitamente dal cliente)",
                        "categoria_attesa": res_extra[0]["metadata"].get("categoria_prodotto", "Dispensa"),
                        "ruolo": "secondario",
                        "note_ingrediente": "arricchimento richiesto dal cliente da integrare nella preparazione",
                        "esito": "TROVATO",
                        "prodotto_trovato": res_extra[0],
                    })

    # Se la richiesta è per un panino o burger (non al piatto) ma nessuno slot contiene pane/buns, inserisci Buns Farino
    is_burger_o_panino = any(k in query_u_lower for k in ["panino", "burger", "hamburger"]) and not any(k in query_u_lower for k in ["al piatto", "senza pane", "senza bun"])
    if is_burger_o_panino:
        ha_pane = any(
            any(w in str(s.get("ingrediente_richiesto", "")).lower() or (s.get("prodotto_trovato") and w in str(s["prodotto_trovato"].get("document", "")).lower())
                for w in ["bun", "pane", "ciabatta", "focaccia"])
            for s in slot_riempiti
        )
        if not ha_pane:
            buns_match = cerca_prodotti(collezione_prodotti, indice_codici, embedder, "Buns Classico Farino burger", n_risultati=2, indice_fornitori=indice_fornitori)
            if buns_match:
                slot_riempiti.insert(0, {
                    "ingrediente_richiesto": "Buns Classico per burger Farino",
                    "categoria_attesa": "Dispensa",
                    "ruolo": "protagonista",
                    "note_ingrediente": "Buns artigianali Farino per burger",
                    "esito": "TROVATO",
                    "prodotto_trovato": buns_match[0],
                })

    return slot_riempiti


def riempi_slot_ricetta(template: dict, collezione_prodotti, indice_codici: dict, embedder,
                         indice_fornitori: "dict | None" = None,
                         indice_testuale: "list | None" = None,
                         target_salumi: int = 3,
                         target_formaggi: int = 3,
                         query_utente: str = "",
                         prodotti_esclusi: "set | None" = None,
                         piano_ricerca: "dict | None" = None,
                         filtro_dieta: "str | None" = None,
                         canale_locale: "str | None" = None,
                         query_originale: "str | None" = None) -> list:
    """Come _riempi_slot_ricetta_base, ma con la dieta del cliente imposta a
    TUTTE le ricerche annidate (via _DIETA_CORRENTE) e verificata a posteriori
    su ogni prodotto scelto: se uno non e' compatibile lo slot torna NON_TROVATO."""
    dieta = (filtro_dieta or "").lower() or None
    token = _DIETA_CORRENTE.set(dieta)
    token_canale = _CANALE_CORRENTE.set((canale_locale or "").lower() or None)
    token_q = _QUERY_ORIGINALE.set(query_originale or None)
    try:
        slot_riempiti = _riempi_slot_ricetta_base(
            template, collezione_prodotti, indice_codici, embedder,
            indice_fornitori, indice_testuale, target_salumi, target_formaggi,
            query_utente, prodotti_esclusi, piano_ricerca,
        )
    finally:
        _DIETA_CORRENTE.reset(token)
        _CANALE_CORRENTE.reset(token_canale)
        _QUERY_ORIGINALE.reset(token_q)
    if dieta:
        for s in slot_riempiti:
            p = s.get("prodotto_trovato")
            if p and not prodotto_compatibile_con_dieta(p.get("metadata", {}), dieta):
                print(f"[DIETA] scartato {p.get('id')} per dieta {dieta}")
                s["prodotto_trovato"] = None
                s["esito"] = "NON_TROVATO"
    # Il flusso standard (primi, secondi, pizze, panini) lascia l'esito a NON_TROVATO anche quando ha un candidato:
    # prima nessuno lo valutava e il modello riceveva "NON TROVATO IN CATALOGO" per prodotti presenti. Qui il
    # candidato diventa TROVATO solo se corrisponde davvero all'ingrediente, altrimenti si scarta.
    for s in slot_riempiti:
        p = s.get("prodotto_trovato")
        if p and s.get("esito") == "NON_TROVATO":
            if _prodotto_pertinente(str(s.get("ingrediente_richiesto") or ""), p):
                s["esito"] = "TROVATO"
            else:
                s["prodotto_trovato"] = None
                s["motivo_scelta"] = "candidato scartato: non corrisponde all'ingrediente"
    _togli_doppioni_tra_slot(slot_riempiti)
    return slot_riempiti


def proponibile(slot_riempiti: list) -> bool:
    """Regola documentata anche nella Legenda del ricettario: proponibile se
    TUTTI i 'protagonista' sono risolti E almeno metà dei 'secondario' lo è.
    Gli 'opzionale' non condizionano l'esito."""
    protagonisti = [s for s in slot_riempiti if s.get("ruolo") == "protagonista"]
    if any(s.get("esito") not in ("TROVATO", "SOSTITUITO") for s in protagonisti):
        return False

    secondari = [s for s in slot_riempiti if s.get("ruolo") == "secondario"]
    if secondari:
        trovati = sum(1 for s in secondari if s.get("esito") in ("TROVATO", "SOSTITUITO"))
        if trovati < len(secondari) / 2:
            return False
    return True
