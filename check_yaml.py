import yaml
import json

with open('taxonomy_report.json', 'r', encoding='utf-8') as f:
    tax = json.load(f)

with open('regole_cliente.yaml', 'r', encoding='utf-8') as f:
    regole = yaml.safe_load(f)

sottocat_db = set(tax['prodotti']['sottocategorie'])
sottocat_vietate = set(regole['regole_composizione']['tagliere']['sottocategorie_vietate'])

# Controllo 1: Ci sono sottocategorie vietate scritte male nel YAML?
errori_nome = sottocat_vietate - sottocat_db
print('Sottocategorie nel YAML non presenti nel DB:', errori_nome)

# Controllo 2: Sottocategorie GELO che ci siamo scordati di vietare?
dimenticate_gelo = [s for s in sottocat_db if 'SURG' in s and s not in sottocat_vietate]
print('Sottocategorie surgelate nel DB ma non vietate nel YAML:', dimenticate_gelo)

