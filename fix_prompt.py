import sys

with open('core/system_prompt_v2.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Trova "SYSTEM_PROMPT_NINO = r\"\"\"" e lo cambia
if 'SYSTEM_PROMPT_NINO = r\"\"\"' in text:
    text = text.replace('SYSTEM_PROMPT_NINO = r\"\"\"', '''from core.config_manager import get_azienda_info

def get_system_prompt() -> str:
    azienda = get_azienda_info()
    nome = azienda.get("nome", "So Food")
    settore = azienda.get("settore", "Distribuzione Gastronomica B2B")
    tono = azienda.get("tono_di_voce", "")

    prompt_grezzo = r\"\"\"''', 1)

    text += '''    
    # Sostituzioni dinamiche
    prompt_dinamico = prompt_grezzo.replace("So Food", nome)
    prompt_dinamico = prompt_dinamico.replace("Sei Nino, l'assistente virtuale commerciale B2B per la ristorazione.", f"Sei Nino, l'assistente virtuale commerciale B2B di {nome} ({settore}).\\n{tono}")
    return prompt_dinamico
'''

with open('core/system_prompt_v2.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("Fatto 1!")
