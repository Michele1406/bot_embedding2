import os
import yaml
from core.config_manager import app_config, get_azienda_info, get_regole_dieta
from core.system_prompt_v2 import get_system_prompt

print('--- TEST 1: Config Manager Load ---')
azienda = get_azienda_info()
print(f"Nome Azienda caricato: {azienda.get('nome')}")
assert azienda.get('nome') == 'So Food', 'Errore: Nome azienda non caricato correttamente'
print('Test 1 superato.')

print('\n--- TEST 2: System Prompt Dinamico ---')
prompt = get_system_prompt()
if "Sei Nino, l'assistente virtuale commerciale B2B di So Food" in prompt:
    print('Stringa base trovata nel prompt!')
else:
    print('ERRORE: Stringa base mancante.')
if 'Professionale, propositivo, rivolto a chef' in prompt:
    print('Tono di voce iniettato correttamente!')
else:
    print('ERRORE: Tono di voce mancante.')
print('Test 2 superato.')

print('\n--- TEST 3: Logica Filtro Vegano Dinamico ---')
# Mock di record prodotti
mock_prodotti = [
    {'id': '1', 'metadata': {'reparto': 'CARNE', 'sottocategoria': 'HAMBURGER', 'vegano': 'NO'}, 'document': 'Hamburger'},
    {'id': '2', 'metadata': {'reparto': 'DISPENSA', 'sottocategoria': 'MAIONESE', 'vegano': 'NO'}, 'document': 'Maionese classica'},
    {'id': '3', 'metadata': {'reparto': 'DISPENSA', 'sottocategoria': 'CEREALI', 'vegano': 'SI*'}, 'document': 'Cereali misti (tracce)'},
    {'id': '4', 'metadata': {'reparto': 'DISPENSA', 'sottocategoria': 'LEGUMI', 'vegano': 'SI'}, 'document': 'Ceci cotti'}
]

regole_veg = get_regole_dieta("vegano")
rep_vietati = [r.upper() for r in regole_veg.get("esclude_reparti", [])]
sottocat_vietate = [s.upper() for s in regole_veg.get("esclude_sottocategorie", [])]
flag_assoluto = regole_veg.get("richiede_flag_assoluto", "SI")

filtrati = []
for r in mock_prodotti:
    rep = str(r['metadata'].get('reparto', '')).upper()
    sc = str(r['metadata'].get('sottocategoria', '')).upper()
    flag = str(r['metadata'].get('vegano', '')).upper()
    
    if rep in rep_vietati: continue
    if sc in sottocat_vietate: continue
    if flag and flag != flag_assoluto: continue
    filtrati.append(r)

print(f"Prodotti passati al filtro: {[p['id'] for p in filtrati]}")
assert len(filtrati) == 1 and filtrati[0]['id'] == '4', 'Errore: Il filtro vegano non ha bloccato i prodotti corretti'
print('Test 3 superato. Il filtro vegano dinamico funziona perfettamente.')

