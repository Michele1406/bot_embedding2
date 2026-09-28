import sys

with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Trovo l'errore unterminated string literal
bad_string = "yield 'data: {\"status\": \"Scrivo la risposta...\"}\\n"
good_string = "yield 'data: {\"status\": \"Scrivo la risposta...\"}\\\\n\\\\n'\\n"

text = text.replace(bad_string, good_string)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(text)
