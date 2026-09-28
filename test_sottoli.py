import chromadb
c = chromadb.PersistentClient(r'C:\Users\baron\Documents\bot embedding 2\database_vettoriale').get_collection('catalogo_sofood')
res = c.get(limit=1227, include=['documents', 'metadatas'])
count = 0
for d, m in zip(res['documents'], res['metadatas']):
    cat = str(m.get('categoria_prodotto', '')).lower()
    sotto = str(m.get('sottocategoria', '')).lower()
    spec = str(m.get('specifiche_liv4', '')).lower()
    txt = d.lower()
    if 'sottol' in txt or 'sottol' in sotto or 'sottol' in cat or 'carciof' in txt or 'fung' in txt:
        print(f"MATCH: {d.splitlines()[0]} | C:{cat} S:{sotto} SP:{spec}")
        count += 1
print(f'Total matches: {count}')
