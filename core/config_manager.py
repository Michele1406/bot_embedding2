import yaml
import os

CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "regole_cliente.yaml")

class ConfigManager:
    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance.reload()
        return cls._instance

    def reload(self):
        """Ricarica il file YAML (utile se viene modificato senza riavviare il server)"""
        try:
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"[ERRORE] Impossibile leggere {CONFIG_FILE_PATH}: {e}")
            self._config = {}

    @property
    def config(self):
        return self._config

# Istanza globale esportata (Singleton)
app_config = ConfigManager()

def get_azienda_info():
    return app_config.config.get("azienda", {})

def get_regole_tagliere():
    return app_config.config.get("regole_composizione", {}).get("tagliere", {})

def get_regole_dieta(dieta_nome: str):
    return app_config.config.get("regole_dieta", {}).get(dieta_nome.lower(), {})

def get_regole_territoriali():
    return app_config.config.get("regole_territoriali", {})

def is_reparto_escluso_da_rag(reparto: str) -> bool:
    esclusi = app_config.config.get("catalogo", {}).get("reparti_esclusi_da_piatti", [])
    doc = app_config.config.get("catalogo", {}).get("categorie_documentali", [])
    return (reparto.strip().upper() in [x.upper() for x in esclusi]) or (reparto.strip().upper() in [x.upper() for x in doc])

def is_categoria_documentale(categoria: str) -> bool:
    doc = app_config.config.get("catalogo", {}).get("categorie_documentali", [])
    return categoria.strip().upper() in [x.upper() for x in doc]
