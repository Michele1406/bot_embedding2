import sys

with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Fix import 
if 'from core.system_prompt_v2 import get_system_prompt' in text:
    text = text.replace('from core.system_prompt_v2 import get_system_prompt', 'from core.system_prompt_v2 import build_modular_prompt')
if 'from core.system_prompt_v2 import SYSTEM_PROMPT_NINO' in text:
    text = text.replace('from core.system_prompt_v2 import SYSTEM_PROMPT_NINO', '')

# Sostituire l'uso di SYSTEM_PROMPT_NINO con build_modular_prompt(...)
old_prompt_call = 'prompt_di_sistema_completo = SYSTEM_PROMPT_NINO + carica_memoria_dinamica()'
new_prompt_call = '''prompt_di_sistema_completo = build_modular_prompt(
        analisi.tipo_richiesta,
        stato.get("canale_locale", ""),
        stato.get("filtro_dieta", "")
    ) + "\\n\\n" + carica_memoria_dinamica()'''

if old_prompt_call in text:
    text = text.replace(old_prompt_call, new_prompt_call)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("App patched for Phase 5!")
