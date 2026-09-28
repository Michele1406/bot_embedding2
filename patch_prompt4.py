import sys

with open('core/system_prompt_v2.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Noi cambiamo la riga "Sei Nino, consulente virtuale B2B di So Food (Bari). Rifornisci la ristorazione in Puglia e Basilicata con consegne dirette con mezzi refrigerati per merce secca, fresca e surgelata. Nel resto d'Italia le spedizioni avvengono TASSATIVAMENTE ed ESCLUSIVAMENTE per prodotti a temperatura ambiente / secco."
# Sostituendola con un placeholder

nuovo_inizio = '''from core.config_manager import get_azienda_info

def get_system_prompt() -> str:
    azienda = get_azienda_info()
    nome = azienda.get("nome", "So Food")
    settore = azienda.get("settore", "Distribuzione Gastronomica B2B")
    tono = azienda.get("tono_di_voce", "")

    prompt_grezzo = r\"\"\"
[IDENTITA_AZIENDA_PLACEHOLDER]

OBIETTIVO E STILE'''

if 'OBIETTIVO E STILE' in text:
    parts = text.split('OBIETTIVO E STILE')
    # scartiamo la parte prima di OBIETTIVO E STILE (la prima parte parts[0]) e la sostituiamo con nuovo_inizio
    text = nuovo_inizio + parts[1]

# modifichiamo le sostituzioni finali
# cerchiamo la parte finale "prompt_dinamico = prompt_grezzo.replace("
final_part = '''    
    # Sostituzioni dinamiche
    prompt_dinamico = prompt_grezzo.replace("So Food", nome)
    intestazione = f"Sei Nino, l'assistente virtuale commerciale di {nome} ({settore}).\\n{tono}\\n"
    prompt_dinamico = prompt_dinamico.replace("[IDENTITA_AZIENDA_PLACEHOLDER]", intestazione)
    return prompt_dinamico
'''

# Tolgo la vecchia fine e metto la nuova
text = text.split('    # Sostituzioni dinamiche')[0] + final_part

with open('core/system_prompt_v2.py', 'w', encoding='utf-8') as f:
    f.write(text)

