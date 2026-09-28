import sys

with open('core/retrieval_utils.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Sostituiamo il parametro in firma
text = text.replace('esclusioni: "list | None" = None) -> list:', 'esclusioni: "list | None" = None, intento: str = None) -> list:')

# Inseriamo il Garbage Collector e fixiamo la dieta
nuovo_blocco = '''
    # ==========================================
    # FASE 3: IL MOTORE A REGOLE (Garbage Collector)
    # ==========================================
    from core.config_manager import get_regole_tagliere, get_regole_dieta, is_reparto_escluso_da_rag, is_categoria_documentale
    
    # 1. Filtro Assoluto Documentale/Aziendale (Vale SEMPRE)
    filtrati_base = []
    for r in combinati:
        rep = str(r["metadata"].get("reparto", "")).upper()
        cat = str(r["metadata"].get("categoria", "")).upper()
        if is_reparto_escluso_da_rag(rep) or is_categoria_documentale(cat):
            continue
        filtrati_base.append(r)
    combinati = filtrati_base

    # 2. Filtro Tagliere (Solo se l'intento lo richiede)
    if intento == "tagliere_o_ricetta":
        regole_tagliere = get_regole_tagliere()
        rep_vietati = [r.upper() for r in regole_tagliere.get("reparti_vietati", [])]
        sc_vietate = [s.upper() for s in regole_tagliere.get("sottocategorie_vietate", [])]
        
        filtrati_tagliere = []
        for r in combinati:
            rep = str(r["metadata"].get("reparto", "")).upper()
            sc = str(r["metadata"].get("sottocategoria", "")).upper()
            if rep in rep_vietati or sc in sc_vietate:
                continue
            filtrati_tagliere.append(r)
        combinati = filtrati_tagliere

    # 3. Filtro Dietetico Dinamico (YAML)
    if filtro_dieta:
        regole_dieta = get_regole_dieta(filtro_dieta.lower())
        rep_vietati_dieta = [r.upper() for r in regole_dieta.get("esclude_reparti", [])]
        sc_vietate_dieta = [s.upper() for s in regole_dieta.get("esclude_sottocategorie", [])]
        flag_assoluto = regole_dieta.get("richiede_flag_assoluto", "")
        
        filtrati_dieta = []
        for r in combinati:
            rep = str(r["metadata"].get("reparto", "")).upper()
            sc = str(r["metadata"].get("sottocategoria", "")).upper()
            flag_v = str(r["metadata"].get("vegano" if filtro_dieta.lower() == "vegano" else "vegetariano", "")).strip().upper()
            
            if rep in rep_vietati_dieta:
                continue
            if sc in sc_vietate_dieta:
                continue
            if flag_assoluto and flag_v and flag_v != flag_assoluto:
                continue
                
            filtrati_dieta.append(r)
        combinati = filtrati_dieta
'''

# Cerchiamo "# FILTRO DIETETICO STRUTTURATO SU REPARTO (VEGANO / VEGETARIANO)" per sostituire quel pezzo
start_idx = text.find('# FILTRO DIETETICO STRUTTURATO SU REPARTO')
end_idx = text.find('if esclusioni:', start_idx)

text = text[:start_idx] + nuovo_blocco + '\n    ' + text[end_idx:]

with open('core/retrieval_utils.py', 'w', encoding='utf-8') as f:
    f.write(text)

# Aggiorniamo le chiamate in app.py
with open('app.py', 'r', encoding='utf-8') as f:
    app_text = f.read()

app_text = app_text.replace('canale_locale=stato.get("canale_locale"),', 'canale_locale=stato.get("canale_locale"), intento=analisi.tipo_richiesta,')
app_text = app_text.replace('return max(elemento.quantita * 6, 18)', 'return max(elemento.quantita * 12, 40)')
app_text = app_text.replace('return 18', 'return 40')
app_text = app_text.replace('return 25', 'return 60')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(app_text)

print("Patch RAG e Garbage Collector applicati!")
