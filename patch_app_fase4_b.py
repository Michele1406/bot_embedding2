import sys

with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

nuove_regole = '''
REGOLE PER IL TIPO DI RICHIESTA (tipo_richiesta):
- Imposta 'chiusura_ordine' SE E SOLO SE l'utente conferma ESPLICITAMENTE che vuole procedere all'ordine, ad esempio fornendo la Partita IVA, dicendo "ok procediamo con l'ordine", "confermo questi", "aggiungi tutto al carrello ed emetti fattura".
- Altrimenti usa 'panoramica_catalogo', 'ricerca_specifica', 'composizione_piatto' o 'conversazione_generica' a seconda del contesto.
'''

if 'REGOLE PER IL RIFERIMENTO PRECEDENTE' in text:
    text = text.replace('REGOLE PER IL RIFERIMENTO PRECEDENTE:', nuove_regole + '\nREGOLE PER IL RIFERIMENTO PRECEDENTE:')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Regola tipo_richiesta inserita in app.py!")
