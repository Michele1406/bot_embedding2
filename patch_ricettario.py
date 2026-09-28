import sys

with open('core/retrieval_utils.py', 'r', encoding='utf-8') as f:
    text = f.read()

helper_function = '''
def parse_db_json_field(val, default=None):
    if default is None:
        default = []
    if not val or str(val).lower() == 'nan':
        return default
    if isinstance(val, list) or isinstance(val, dict):
        return val
    try:
        import json
        return json.loads(val)
    except Exception:
        return [x.strip() for x in str(val).split(',') if x.strip()]
'''

# Aggiungiamo dopo il primo import
text = text.replace('import json', 'import json' + helper_function, 1)

text = text.replace('"slot": json.loads(meta.get("slot", "[]")),', '"slot": parse_db_json_field(meta.get("ingredienti_json", meta.get("slot", "[]"))),')
text = text.replace('"canali_sconsigliati": json.loads(meta.get("canali_sconsigliati", "[]"))', '"canali_sconsigliati": parse_db_json_field(meta.get("canali_sconsigliati", "[]"))')
text = text.replace('"ingredienti": json.loads(meta["ingredienti_json"]),', '"ingredienti": parse_db_json_field(meta.get("ingredienti_json", "[]")),')

with open('core/retrieval_utils.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Patch ricettario applicata!")
