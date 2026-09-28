import yaml
import json

with open('taxonomy_report.json', 'r', encoding='utf-8') as f:
    tax = json.load(f)

with open('regole_cliente.yaml', 'r', encoding='utf-8') as f:
    regole = yaml.safe_load(f)

reparti_db = set(tax['prodotti']['reparti'])
reparti_vietati_tagliere = set(regole['regole_composizione']['tagliere']['reparti_vietati'])

errori_reparto = reparti_vietati_tagliere - reparti_db
print('Reparti vietati nel YAML ma inesistenti nel DB:', errori_reparto)
