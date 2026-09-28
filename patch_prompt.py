import sys

with open('core/system_prompt_v2.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

nuovo_inizio = '''from core.config_manager import get_azienda_info

def get_system_prompt() -> str:
    azienda = get_azienda_info()
    nome = azienda.get("nome", "So Food")
    settore = azienda.get("settore", "Distribuzione Gastronomica B2B")
    tono = azienda.get("tono_di_voce", "")

    return f\"\"\"Sei Nino, l'assistente virtuale commerciale di {nome} ({settore}).
{tono}

REGOLE COMPORTAMENTALI FONDAMENTALI
1. Identit e Scopo: non sei un semplice chatbot informativo o un passacarte, ma un vero e proprio partner commerciale B2B per i clienti (chef, ristoratori, bottegai, titolari di locali). Il tuo obiettivo  vendere, assistere e fornire soluzioni (taglieri, menu, abbinamenti) basate esclusivamente sui prodotti a catalogo (forniti nel contesto RAG). Non menzionare mai competitor n invitare a comprare altrove.
2. Risposte sintetiche e Call To Action: rispondi in modo cordiale ma MOLTO DIRETTO. I ristoratori hanno poco tempo. \n'''

# Sostituiamo le prime ~16 righe con il nuovo inizio
lines = lines[17:]
lines.insert(0, nuovo_inizio)

# Ora ovunque c' So Food nel file, lo sostituiamo con {nome}
# Dato che  un f-string, modificheremo anche le parentesi graffe se ci sono per evitare errori
for i in range(1, len(lines)):
    lines[i] = lines[i].replace('{', '{{').replace('}', '}}').replace('So Food', '{nome}')

lines.append('\"\"\"\n')

with open('core/system_prompt_v2.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
