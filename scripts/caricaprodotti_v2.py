import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import os
import re
import json
import time
import mimetypes
import datetime
from pathlib import Path

import pandas as pd
import chromadb
# NOTA FLUSSO TASSONOMIA: questo script indicizza l'embedding e i metadati di
# base del prodotto (allergeni, dietetica, immagini...), ma NON assegna
# reparto/categoria_tassonomia/sottocategoria: quei campi arrivano in un
# secondo momento dalla pipeline ECR + rigenera_tassonomia_db.py (vedi la
# nota in cima a arricchisci_catalogo.py). Un prodotto appena caricato da
# questo script è quindi "senza tassonomia" finché non gira quella pipeline:
# è normale e atteso, non un errore di questo script.
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# ====================================================================
# CONFIGURAZIONE
# ====================================================================
MODELLO_EMBEDDING = "models/gemini-embedding-2"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError(
        "ATTENZIONE: GEMINI_API_KEY non trovata. Impostala nel file .env "
        "(non lasciarla mai scritta nel codice: se questo file finisce online, "
        "la chiave è compromessa)."
    )

FOGLI_EXCEL_DA_IGNORARE = {"LEGENDA", "RIEPILOGO PER FORNITORE"}
NOME_FILE_LOG_ANOMALIE = "anomalies_log.json"
PATTERN_CARTELLA_FORNITORE = re.compile(r"^19\d{6}$")
PATTERN_CARTELLA_COLLASSATA = re.compile(r"^(19\d{6})[\\/](.+)$")
ESTENSIONI_IMMAGINE_SUPPORTATE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

PAUSA_TRA_CHIAMATE_SEC = 4.0
TENTATIVI_MASSIMI_PER_CHIAMATA = 3


class EmbedderMultimodaleGemini:
    """Calcola vettori con gemini-embedding-2 combinando testo e immagine."""

    def __init__(self, api_key: str, model_name: str):
        if not api_key:
            raise ValueError("GEMINI_API_KEY mancante. Impostala come variabile d'ambiente.")
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def _chiamata_con_retry(self, contents: list, task_type: str) -> list:
        ultimo_errore = None
        for tentativo in range(1, TENTATIVI_MASSIMI_PER_CHIAMATA + 1):
            try:
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=contents,
                    config=types.EmbedContentConfig(task_type=task_type),
                )
                vettore = response.embeddings[0]
                return vettore.values if hasattr(vettore, "values") else list(vettore)
            except Exception as errore:
                ultimo_errore = errore
                attesa = PAUSA_TRA_CHIAMATE_SEC * tentativo
                print(f"      [ATTENZIONE] Tentativo {tentativo} fallito ({errore}); riprovo tra {attesa:.1f}s")
                time.sleep(attesa)
        raise RuntimeError(f"Embedding fallito dopo {TENTATIVI_MASSIMI_PER_CHIAMATA} tentativi: {ultimo_errore}")

    def embed_documento(self, testo: str, percorso_immagine: "Path | None" = None) -> list:
        contents = [testo]
        if percorso_immagine is not None and percorso_immagine.exists():
            mime_type = ESTENSIONI_IMMAGINE_SUPPORTATE.get(percorso_immagine.suffix.lower())
            if mime_type:
                try:
                    image_bytes = percorso_immagine.read_bytes()
                    contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
                except Exception as errore:
                    print(f"      [ATTENZIONE] Impossibile leggere l'immagine {percorso_immagine.name}: {errore}")
        return self._chiamata_con_retry(contents, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, testo: str) -> list:
        return self._chiamata_con_retry([testo], task_type="RETRIEVAL_QUERY")


class CaricatoreCatalogoSofood:
    def __init__(self, cartella_radice: str, percorso_database_vettoriale: str = "./database_vettoriale",
                 cartella_output_log: str = "."):
        self.root = Path(cartella_radice)
        self.chroma_db_path = Path(percorso_database_vettoriale)
        self.cartella_output_log = Path(cartella_output_log)

        self.fornitori_dict = {}
        self.nome_azienda_to_an_forn = {}
        self.varianti_dict = {}
        self.riassunto_dict = {}
        self.disclaimer_text = ""
        self.elenco_anomalie = []

        print(f"[INFO] Connessione a ChromaDB in corso (percorso: {self.chroma_db_path.resolve()})")
        self.client = chromadb.PersistentClient(path=str(self.chroma_db_path))

        print(f"[INFO] Attivazione embedder multimodale: {MODELLO_EMBEDDING}")
        self.embedder = EmbedderMultimodaleGemini(api_key=GEMINI_API_KEY, model_name=MODELLO_EMBEDDING)
        self.collezione = self.client.get_or_create_collection(name="catalogo_sofood")

        # Recupera tutti gli ID già memorizzati su disco per saltarli all'avvio
        dati_esistenti = self.collezione.get()
        self.id_memorizzati = set(dati_esistenti.get("ids", []))
        print(f"[INFO] Record già presenti su ChromaDB: {len(self.id_memorizzati)}")

    def _trova_colonna(self, tabella: "pd.DataFrame", nomi_possibili: list) -> "str | None":
        for nome_da_cercare in nomi_possibili:
            for colonna_reale in tabella.columns:
                if str(colonna_reale).strip().lower() == nome_da_cercare.strip().lower():
                    return colonna_reale
        return None

    def _varianti_normalizzazione_codice(self, codice: str) -> list:
        codice = codice.strip().upper()
        varianti = {codice}
        varianti.add(codice.replace("_", "."))
        varianti.add(codice.replace(".", "_"))
        varianti.add(codice.replace("__", "/"))
        varianti.add(codice.replace("/", "__"))
        return list(varianti)

    def _registra_anomalia(self, fornitore: str, prodotto: str, file: str, tipo_errore: str) -> None:
        self.elenco_anomalie.append({
            "supplier": fornitore if fornitore else "UNKNOWN",
            "product": prodotto if prodotto else "UNKNOWN",
            "file": file,
            "error_type": tipo_errore,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    def carica_file_globali(self) -> None:
        print("\n[FASE 1] Caricamento file di anagrafica globali...")

        percorso_disclaimer = self.root / "FINE SINGOLO PRODOTTO.txt"
        if percorso_disclaimer.exists():
            try:
                self.disclaimer_text = percorso_disclaimer.read_text(encoding="utf-8").strip()
            except Exception as errore:
                print(f"   [ATTENZIONE] Impossibile leggere il disclaimer globale: {errore}")

        percorso_fornitori = self.root / "fornitori.csv"
        if percorso_fornitori.exists():
            try:
                tabella_fornitori = pd.read_csv(percorso_fornitori, dtype=str)
                tabella_fornitori.columns = [str(c).strip() for c in tabella_fornitori.columns]
                colonna_id = self._trova_colonna(tabella_fornitori, ["an_forn", "id_fornitore", "codice", "fornitore"]) or tabella_fornitori.columns[0]
                colonna_nome = self._trova_colonna(tabella_fornitori, ["nome_azienda", "ragione_sociale", "nome", "fornitore_nome", "descrizione"]) or (
                    tabella_fornitori.columns[1] if len(tabella_fornitori.columns) > 1 else tabella_fornitori.columns[0])

                for _, riga in tabella_fornitori.iterrows():
                    codice = str(riga[colonna_id]).strip()
                    nome_azienda = str(riga[colonna_nome]).strip()
                    if codice and codice != "nan":
                        self.fornitori_dict[codice] = nome_azienda
                        self.nome_azienda_to_an_forn[nome_azienda.strip().upper()] = codice
                        for singolo_marchio in nome_azienda.split(" | "):
                            self.nome_azienda_to_an_forn[singolo_marchio.strip().upper()] = codice
                print(f"   [OK] Anagrafica fornitori caricata ({len(self.fornitori_dict)} record).")
            except Exception as errore:
                print(f"   [ERRORE] Impossibile caricare fornitori.csv: {errore}")

        percorso_varianti = self.root / "varianti_prodotto.csv"
        if percorso_varianti.exists():
            try:
                tabella_varianti = pd.read_csv(percorso_varianti, dtype=str)
                tabella_varianti.columns = [str(c).strip() for c in tabella_varianti.columns]
                colonna_forn = self._trova_colonna(tabella_varianti, ["an_forn", "codice_fornitore"])
                colonna_art = self._trova_colonna(tabella_varianti, ["ar_codart", "codice_articolo", "articolo", "id"]) or tabella_varianti.columns[0]
                for _, riga in tabella_varianti.iterrows():
                    codice_prodotto = str(riga[colonna_art]).strip().upper()
                    codice_fornitore = str(riga[colonna_forn]).strip() if colonna_forn else ""
                    if codice_prodotto and codice_prodotto != "NAN":
                        varianti_trovate = []
                        for nome_colonna in tabella_varianti.columns:
                            if nome_colonna not in (colonna_art, colonna_forn) and pd.notna(riga[nome_colonna]):
                                valore = str(riga[nome_colonna]).strip().upper()
                                if valore and valore != "NAN":
                                    varianti_trovate.append(valore)
                        if varianti_trovate:
                            testo_varianti = ", ".join(varianti_trovate)
                            if codice_fornitore and codice_fornitore != "NAN":
                                self.varianti_dict[f"{codice_fornitore}|{codice_prodotto}"] = testo_varianti
                            self.varianti_dict[codice_prodotto] = testo_varianti
                print(f"   [OK] Varianti prodotto caricate ({len(self.varianti_dict)} chiavi indicizzate).")
            except Exception as errore:
                print(f"   [ATTENZIONE] Impossibile caricare varianti_prodotto.csv: {errore}")

        percorso_riassunto = self.root / "riassunto_prodotti.xlsx"
        if percorso_riassunto.exists():
            try:
                file_excel = pd.ExcelFile(percorso_riassunto)
                fogli_da_leggere = [f for f in file_excel.sheet_names if f.strip().upper() not in FOGLI_EXCEL_DA_IGNORARE]
                for nome_foglio in fogli_da_leggere:
                    tabella_foglio = pd.read_excel(percorso_riassunto, sheet_name=nome_foglio, dtype=str)
                    tabella_foglio.columns = [str(c).strip() for c in tabella_foglio.columns]
                    colonna_prodotto = self._trova_colonna(tabella_foglio, ["codice prodotto", "ar_codart", "codice_articolo", "articolo", "codice"]) or (
                        tabella_foglio.columns[2] if len(tabella_foglio.columns) > 2 else tabella_foglio.columns[0])
                    colonna_fornitore = self._trova_colonna(tabella_foglio, ["codice fornitore", "an_forn"])
                    colonna_nome_azienda = self._trova_colonna(tabella_foglio, ["nome azienda", "nome_azienda", "fornitore"])
                    colonna_allergeni = self._trova_colonna(tabella_foglio, ["allergeni", "allergene"])
                    colonna_tracce = self._trova_colonna(tabella_foglio, ["tracce di", "tracce_di", "tracce"])
                    colonna_biologico = self._trova_colonna(tabella_foglio, ["biologico", "bio"])
                    colonna_vegano = self._trova_colonna(tabella_foglio, ["vegano", "vegan"])
                    colonna_vegetariano = self._trova_colonna(tabella_foglio, ["vegetariano", "veg"])
                    colonna_senza_glutine = self._trova_colonna(tabella_foglio, ["senza glutine", "gluten free", "gluten_free"])
                    colonna_senza_lattosio = self._trova_colonna(tabella_foglio, ["senza lattosio", "lactose free", "lactose_free"])
                    colonna_milk_free = self._trova_colonna(tabella_foglio, ["milk free", "milk_free", "senza latte"])
                    colonna_kosher = self._trova_colonna(tabella_foglio, ["kosher"])

                    for _, riga in tabella_foglio.iterrows():
                        valore_grezzo = riga.get(colonna_prodotto)
                        if pd.notna(valore_grezzo):
                            codice_pulito = str(valore_grezzo).strip().upper()
                            codice_fornitore_riga = ""
                            if colonna_fornitore and pd.notna(riga.get(colonna_fornitore)):
                                codice_fornitore_riga = str(riga[colonna_fornitore]).strip()
                            elif colonna_nome_azienda and pd.notna(riga.get(colonna_nome_azienda)):
                                nome_azienda_riga = str(riga[colonna_nome_azienda]).strip().upper()
                                codice_fornitore_riga = self.nome_azienda_to_an_forn.get(nome_azienda_riga, "")

                            dettagli = {
                                "categoria": nome_foglio,
                                "allergeni": str(riga.get(colonna_allergeni)).strip() if colonna_allergeni and pd.notna(riga.get(colonna_allergeni)) else "Non specificato",
                                "tracce_di": str(riga.get(colonna_tracce)).strip() if colonna_tracce and pd.notna(riga.get(colonna_tracce)) else "Nessuna",
                                "biologico": str(riga.get(colonna_biologico)).strip().upper() if colonna_biologico and pd.notna(riga.get(colonna_biologico)) else "NO",
                                "vegano": str(riga.get(colonna_vegano)).strip().upper() if colonna_vegano and pd.notna(riga.get(colonna_vegano)) else "NO",
                                "vegetariano": str(riga.get(colonna_vegetariano)).strip().upper() if colonna_vegetariano and pd.notna(riga.get(colonna_vegetariano)) else "NO",
                                "senza_glutine": str(riga.get(colonna_senza_glutine)).strip().upper() if colonna_senza_glutine and pd.notna(riga.get(colonna_senza_glutine)) else "NO",
                                "senza_lattosio": str(riga.get(colonna_senza_lattosio)).strip().upper() if colonna_senza_lattosio and pd.notna(riga.get(colonna_senza_lattosio)) else "NO",
                                "milk_free": str(riga.get(colonna_milk_free)).strip().upper() if colonna_milk_free and pd.notna(riga.get(colonna_milk_free)) else "NO",
                                "kosher": str(riga.get(colonna_kosher)).strip().upper() if colonna_kosher and pd.notna(riga.get(colonna_kosher)) else "NO",
                            }
                            for variante_codice in self._varianti_normalizzazione_codice(codice_pulito):
                                self.riassunto_dict[variante_codice] = dettagli
                                if codice_fornitore_riga:
                                    self.riassunto_dict[f"{codice_fornitore_riga}|{variante_codice}"] = dettagli
                print(f"   [OK] Dettagli estesi caricati da Excel ({len(self.riassunto_dict)} chiavi).")
            except Exception as errore:
                print(f"   [ERRORE] Impossibile leggere riassunto_prodotti.xlsx: {errore}")

    def _cerca_dettagli_estesi(self, codice_fornitore: str, codice_prodotto: str) -> dict:
        codice_prodotto = codice_prodotto.strip().upper()
        for variante in self._varianti_normalizzazione_codice(codice_prodotto):
            chiave = f"{codice_fornitore}|{variante}"
            if chiave in self.riassunto_dict:
                return self.riassunto_dict[chiave]
        for variante in self._varianti_normalizzazione_codice(codice_prodotto):
            if variante in self.riassunto_dict:
                return self.riassunto_dict[variante]
        return {"categoria": "Non specificato", "allergeni": "Non specificato", "tracce_di": "Nessuna",
                "biologico": "NO", "vegano": "NO", "vegetariano": "NO", "senza_glutine": "NO",
                "senza_lattosio": "NO", "milk_free": "NO", "kosher": "NO"}

    def _cerca_varianti_prodotto(self, codice_fornitore: str, codice_prodotto: str) -> str:
        codice_prodotto = codice_prodotto.strip().upper()
        for variante in self._varianti_normalizzazione_codice(codice_prodotto):
            chiave = f"{codice_fornitore}|{variante}"
            if chiave in self.varianti_dict:
                return self.varianti_dict[chiave]
        for variante in self._varianti_normalizzazione_codice(codice_prodotto):
            if variante in self.varianti_dict:
                return self.varianti_dict[variante]
        return "Nessuna variante"

    def scansiona_e_carica(self) -> None:
        print("\n[FASE 2] Scansione e inserimento incrementale...")
        contatore_elaborati = 0

        for cartella_livello1 in self.root.iterdir():
            if not cartella_livello1.is_dir():
                continue
            nome_cartella = cartella_livello1.name
            corrispondenza_collassata = PATTERN_CARTELLA_COLLASSATA.match(nome_cartella)
            if corrispondenza_collassata:
                an_forn = corrispondenza_collassata.group(1)
                codice_prodotto = corrispondenza_collassata.group(2)
                nome_fornitore = self.fornitori_dict.get(an_forn, "Fornitore Sconosciuto")
                self._elabora_singolo_prodotto(cartella_livello1, an_forn, nome_fornitore, codice_prodotto)
                contatore_elaborati += 1
                continue

            if PATTERN_CARTELLA_FORNITORE.match(nome_cartella):
                an_forn = nome_cartella
                nome_fornitore = self.fornitori_dict.get(an_forn, "Fornitore Sconosciuto")
                for cartella_prodotto in cartella_livello1.iterdir():
                    if not cartella_prodotto.is_dir():
                        continue
                    self._elabora_singolo_prodotto(cartella_prodotto, an_forn, nome_fornitore, cartella_prodotto.name)
                    contatore_elaborati += 1
                continue

        print(f"\n[RIEPILOGO] Prodotti totali scansionati: {contatore_elaborati}")
        self._scrivi_log_anomalie()

    def _elabora_singolo_prodotto(self, cartella_prodotto: Path, an_forn: str, nome_fornitore: str, product_code: str) -> None:
        product_code_normalizzato = product_code.strip().upper()
        id_record = f"{an_forn}_{product_code_normalizzato}"

        # Se il prodotto è già stato salvato su ChromaDB, lo salta direttamente
        if id_record in self.id_memorizzati:
            print(f"   [SKIP] Già presente nel database: {id_record}")
            return

        file_txt = cartella_prodotto / f"{product_code}.txt"
        file_jpg = cartella_prodotto / f"{product_code}.jpg"
        file_pdf = cartella_prodotto / f"{product_code}.pdf"

        if not file_txt.exists():
            self._registra_anomalia(an_forn, product_code, f"{product_code}.txt", "NAMING_VIOLATION")
            return

        try:
            testo_prodotto = file_txt.read_text(encoding="utf-8").strip()
        except Exception:
            self._registra_anomalia(an_forn, product_code, file_txt.name, "NAMING_VIOLATION")
            return

        if not testo_prodotto:
            self._registra_anomalia(an_forn, product_code, file_txt.name, "INCOMPLETE_TEXT")
            return

        testo_finale = (f"{testo_prodotto}\n\n[DISCLAIMER LEGALE SOFOOD]\n{self.disclaimer_text}"
                         if self.disclaimer_text and self.disclaimer_text not in testo_prodotto else testo_prodotto)

        ha_immagine = file_jpg.exists()
        print(f"   [...] Embedding {'multimodale (testo+foto)' if ha_immagine else 'solo testo'}: {an_forn}/{product_code}")
        try:
            embedding = self.embedder.embed_documento(testo_finale, file_jpg if ha_immagine else None)
        except Exception as errore:
            print(f"      [ERRORE] Embedding fallito per {an_forn}/{product_code}: {errore}")
            self._registra_anomalia(an_forn, product_code, file_txt.name, "EMBEDDING_FALLITO")
            return

        varianti = self._cerca_varianti_prodotto(an_forn, product_code_normalizzato)
        dettagli = self._cerca_dettagli_estesi(an_forn, product_code_normalizzato)

        metadati = {
            "codice_prodotto": product_code,
            "codice_fornitore": an_forn,
            "nome_fornitore": nome_fornitore,
            "categoria_prodotto": dettagli["categoria"],
            "varianti_prodotto": varianti,
            "allergeni": dettagli["allergeni"],
            "tracce_di": dettagli["tracce_di"],
            "biologico": dettagli["biologico"],
            "vegano": dettagli["vegano"],
            "vegetariano": dettagli["vegetariano"],
            "senza_glutine": dettagli["senza_glutine"],
            "senza_lattosio": dettagli["senza_lattosio"],
            "milk_free": dettagli["milk_free"],
            "kosher": dettagli["kosher"],
            "ha_immagine_primaria": ha_immagine,
            "percorso_immagine": str(file_jpg.resolve()) if ha_immagine else "",
            "ha_pdf_tecnico": file_pdf.exists(),
            "percorso_cartella_locale": str(cartella_prodotto.resolve()),
        }

        # Salvataggio immediato sul disco (persistenza in tempo reale)
        self.collezione.upsert(
            documents=[testo_finale],
            metadatas=[metadati],
            ids=[id_record],
            embeddings=[embedding],
        )
        self.id_memorizzati.add(id_record)
        print(f"   [SALVATO] {id_record} salvato su database locale.")

        time.sleep(PAUSA_TRA_CHIAMATE_SEC)

    def _scrivi_log_anomalie(self) -> None:
        if not self.elenco_anomalie:
            return
        percorso_log = self.cartella_output_log / NOME_FILE_LOG_ANOMALIE
        anomalie_precedenti = []
        if percorso_log.exists():
            try:
                with open(percorso_log, "r", encoding="utf-8") as f:
                    anomalie_precedenti = json.load(f)
            except Exception:
                pass
        with open(percorso_log, "w", encoding="utf-8") as f:
            json.dump(anomalie_precedenti + self.elenco_anomalie, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    # NOTA: ispettore_v2.py mostra un confronto tra metadati e "coda" interna
    # dell'indice vettoriale — è solo informativo, non indica un problema di
    # ricerca (Chroma include comunque quegli elementi nelle query). Questo
    # script va usato per il caricamento iniziale o per aggiungere nuovi
    # prodotti al catalogo (quelli non ancora presenti nei metadati).
    CARTELLA_RADICE_CATALOGO = r"C:\Users\baron\LAVORO\PRODOTTI SOFOOD"
    PERCORSO_DATABASE_VETTORIALE = "./database_vettoriale"
    CARTELLA_LOG_ANOMALIE = "./log"

    if not Path(CARTELLA_RADICE_CATALOGO).exists():
        print(f"[ERRORE] La cartella '{CARTELLA_RADICE_CATALOGO}' non esiste.")
    else:
        caricatore = CaricatoreCatalogoSofood(CARTELLA_RADICE_CATALOGO, PERCORSO_DATABASE_VETTORIALE, CARTELLA_LOG_ANOMALIE)
        caricatore.carica_file_globali()
        caricatore.scansiona_e_carica()
        print("\n[FINE] Operazione completata.")
        