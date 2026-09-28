import sys

# FIX 1: Pulizia blocco Markdown in order_extractor.py
with open('core/order_extractor.py', 'r', encoding='utf-8') as f:
    oe = f.read()

oe = oe.replace('return CheckoutOrdine.model_validate_json(risposta.text)', 
'''testo_json = risposta.text.strip()
        if testo_json.startswith('`json'):
            testo_json = testo_json[7:]
        if testo_json.startswith('`'):
            testo_json = testo_json[3:]
        if testo_json.endswith('`'):
            testo_json = testo_json[:-3]
        testo_json = testo_json.strip()
        return CheckoutOrdine.model_validate_json(testo_json)''')
with open('core/order_extractor.py', 'w', encoding='utf-8') as f:
    f.write(oe)

# FIX 2 & 3: Variabili d'ambiente LLM e Cart tenant in app.py
with open('app.py', 'r', encoding='utf-8') as f:
    app_text = f.read()

# Sostituiamo il blocco dei modelli
old_models = '''MODELLO_EMBEDDING = "models/gemini-embedding-2"
MODELLO_PRINCIPALE = os.getenv("MODELLO_RISPOSTA", "models/gemini-3.6-flash")
MODELLO_GEMINI = "models/gemini-3.6-flash"
MODELLO_FALLBACK = "models/gemini-3.6-flash"
MODELLO_AUDIO = "models/gemini-2.5-flash"
MODELLO_AUDIO_FALLBACK = "models/gemini-3.6-flash"'''

new_models = '''import os
MODELLO_EMBEDDING = os.getenv("LLM_EMBEDDING", "models/gemini-embedding-2")
MODELLO_PRINCIPALE = os.getenv("LLM_PRINCIPALE", "models/gemini-3.6-flash")
MODELLO_FALLBACK = os.getenv("LLM_FALLBACK", "models/gemini-3.6-flash")
MODELLO_AUDIO = os.getenv("LLM_AUDIO", "models/gemini-2.5-flash")'''

if old_models in app_text:
    app_text = app_text.replace(old_models, new_models)

# Sostituiamo anche MODELLO_AUDIO_FALLBACK che non serve pi o lo uniformiamo a MODELLO_FALLBACK
app_text = app_text.replace('for modello in [MODELLO_AUDIO, MODELLO_AUDIO_FALLBACK]:', 'for modello in [MODELLO_AUDIO, MODELLO_FALLBACK]:')

# Dinamizziamo il tenant nel checkout
if 'app_cart.init_cart(session_id, "so_food")' in app_text:
    app_text = app_text.replace('app_cart.init_cart(session_id, "so_food")', '''from core.config_manager import get_azienda_info
        tenant_name = get_azienda_info().get("nome", "azienda_ignota")
        app_cart.init_cart(session_id, tenant_name)''')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(app_text)

print("Fix applicati con successo!")
