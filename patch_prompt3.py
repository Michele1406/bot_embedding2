import sys

with open('core/system_prompt_v2.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Noi cambieremo solo questo: rimpiazziamo "SYSTEM_PROMPT_NINO = \"\"\"..." con una funzione
text = text.replace('SYSTEM_PROMPT_NINO = \"\"\"', '''from core.config_manager import get_azienda_info

def get_system_prompt() -> str:
    azienda = get_azienda_info()
    nome = azienda.get("nome", "So Food")
    settore = azienda.get("settore", "Distribuzione Gastronomica B2B")
    tono = azienda.get("tono_di_voce", "")

    prompt_grezzo = \"\"\"''', 1)

# e alla fine del file:
text += '''
    
    # Sostituzioni dinamiche
    prompt_dinamico = prompt_grezzo.replace("So Food", nome)
    prompt_dinamico = prompt_dinamico.replace("Sei Nino, l'assistente virtuale commerciale B2B per la ristorazione.", f"Sei Nino, l'assistente virtuale commerciale B2B di {nome} ({settore}).\\n{tono}")
    return prompt_dinamico
'''

with open('core/system_prompt_v2.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open('app.py', 'r', encoding='utf-8') as f:
    app_text = f.read()
app_text = app_text.replace('from core.system_prompt_v2 import SYSTEM_PROMPT_NINO', 'from core.system_prompt_v2 import get_system_prompt')
app_text = app_text.replace('system_instruction=SYSTEM_PROMPT_NINO', 'system_instruction=get_system_prompt()')
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(app_text)
print("Fatto!")
