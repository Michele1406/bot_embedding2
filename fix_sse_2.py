import sys

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line == '                yield \'data: {"status": "Scrivo la risposta..."}\\n\':
        continue
    if line == '\\\':
        continue
    if line == '                yield f"data: {chunk_json}\\n\':
        continue
    if line == '                yield \\'data: {"status": "Scrivo la risposta..."}\\n':
        continue
    new_lines.append(line)

# Questo e' troppo prono ad errori manuali, usiamo espressioni regolari per ri-generare app.py senza stream_messaggio_nino, e po ri-applicarlo pulito.
