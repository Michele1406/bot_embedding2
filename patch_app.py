import sys

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

nuovo_blocco = '''                from core.config_manager import get_regole_dieta
                regole_veg = get_regole_dieta("vegano")
                rep_vietati = [r.upper() for r in regole_veg.get("esclude_reparti", [])]
                sottocat_vietate = [s.upper() for s in regole_veg.get("esclude_sottocategorie", [])]
                flag_assoluto = regole_veg.get("richiede_flag_assoluto", "SI")
                
                filtrati_veg = []
                for r in record_prodotti:
                    meta = r.get("metadata", {})
                    rep_r = str(meta.get("reparto", "")).upper()
                    sc_r = str(meta.get("sottocategoria", "")).upper()
                    flag_v = str(meta.get("vegano", "")).strip().upper()
                    
                    if rep_r in rep_vietati:
                        continue
                    if sc_r in sottocat_vietate:
                        continue
                    if flag_v and flag_v != flag_assoluto:
                        continue
                        
                    filtrati_veg.append(r)\n'''

del lines[970:995]
lines.insert(970, nuovo_blocco)

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("App.py aggiornato!")
