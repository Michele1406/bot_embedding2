import chromadb
import json

c = chromadb.PersistentClient(r'C:\Users\baron\Documents\bot embedding 2\database_vettoriale')

# 1. Analisi Catalogo Prodotti
coll_prod = c.get_collection('catalogo_sofood')
res_prod = coll_prod.get(include=['metadatas'])
metas_prod = res_prod['metadatas']

reparti = set()
categorie = set()
sottocategorie = set()
vegano_values = set()

for m in metas_prod:
    if m:
        reparti.add(str(m.get('reparto', '')))
        categorie.add(str(m.get('categoria', '')))
        sottocategorie.add(str(m.get('sottocategoria', '')))
        vegano_values.add(str(m.get('vegano', '')))

# 2. Analisi Ricettario
coll_ric = c.get_collection('ricette_sofood')
res_ric = coll_ric.get(include=['metadatas'])
metas_ric = res_ric['metadatas']

categorie_piatti = set()
stili_cucina = set()
tag_dieta = set()

for m in metas_ric:
    if m:
        categorie_piatti.add(str(m.get('categoria', '')))
        stili_cucina.add(str(m.get('stile_cucina', '')))
        tag_dieta.add(str(m.get('tag_dieta', '')))

out = {
    'prodotti': {
        'reparti': sorted(list(reparti)),
        'categorie': sorted(list(categorie)),
        'sottocategorie': sorted(list(sottocategorie)),
        'vegano_flags': sorted(list(vegano_values))
    },
    'ricette': {
        'categorie': sorted(list(categorie_piatti)),
        'stili_cucina': sorted(list(stili_cucina)),
        'tag_dieta': sorted(list(tag_dieta))
    }
}

with open('taxonomy_report.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print('Report salvato in taxonomy_report.json')
