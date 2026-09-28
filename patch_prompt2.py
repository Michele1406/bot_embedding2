import sys

with open('core/system_prompt_v2.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Trova "SYSTEM_PROMPT_NINO = \"\"\""
text = text.replace('SYSTEM_PROMPT_NINO = \"\"\"', '''from core.config_manager import get_azienda_info

def get_system_prompt() -> str:
    azienda = get_azienda_info()
    nome = azienda.get("nome", "So Food")
    settore = azienda.get("settore", "Distribuzione Gastronomica B2B")
    tono = azienda.get("tono_di_voce", "")

    return f\"\"\"''', 1)

text = text.replace('So Food', '{nome}')
# dobbiamo gestire le doppie graffe
# Invece di fare macelli con f-string, usiamo il .format o la concatenazione
