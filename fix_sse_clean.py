import sys
import re

with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

match = re.search(r'(def elabora_messaggio_nino\(user_query: str, stato: dict, sid: str\) -> dict:.*?)(?=def chat\(\):)', text, re.DOTALL)
if not match:
    print("Non trovato elabora_messaggio_nino")
    sys.exit(1)

elabora_str = match.group(1)

stream_str = elabora_str.replace(
    'def elabora_messaggio_nino(user_query: str, stato: dict, sid: str) -> dict:',
    'def stream_messaggio_nino(user_query: str, stato: dict, sid: str):'
)

# Aggiungiamo i yield di status (senza usare backslash n letterali problematici in python string replace)
status_analisi = "yield 'data: {\"status\": \"Analizzo la richiesta...\"}\\n\\n'\\n    analisi = analizza_richiesta_unificata"
stream_str = stream_str.replace('analisi = analizza_richiesta_unificata', status_analisi)

status_ricerca = "yield 'data: {\"status\": \"Cerco a catalogo...\"}\\n\\n'\\n                risultati_parziali = cerca_prodotti("
stream_str = stream_str.replace('risultati_parziali = cerca_prodotti(', status_ricerca)

# Sostituiamo la chat vera e propria
blocco_gen_vecchio = '''response = chat_session.send_message(prompt_finale)
                            testo_pulito = (response.text or "").strip()'''

blocco_gen_nuovo = '''yield 'data: {"status": "Scrivo la risposta..."}\\n\\n'
                            response_stream = chat_session.send_message_stream(prompt_finale)
                            testo_pulito = ""
                            for chunk in response_stream:
                                if chunk.text:
                                    testo_pulito += chunk.text
                                    import json
                                    chunk_json = json.dumps({"chunk": chunk.text})
                                    yield f"data: {chunk_json}\\n\\n"'''

stream_str = stream_str.replace(blocco_gen_vecchio, blocco_gen_nuovo)

blocco_gen_vecchio2 = '''response = chat_session.send_message(prompt_finale)
                testo_pulito = (response.text or "").strip()'''

blocco_gen_nuovo2 = '''yield 'data: {"status": "Scrivo la risposta..."}\\n\\n'
                response_stream = chat_session.send_message_stream(prompt_finale)
                testo_pulito = ""
                for chunk in response_stream:
                    if chunk.text:
                        testo_pulito += chunk.text
                        import json
                        chunk_json = json.dumps({"chunk": chunk.text})
                        yield f"data: {chunk_json}\\n\\n"'''

stream_str = stream_str.replace(blocco_gen_vecchio2, blocco_gen_nuovo2)

# Rimuovo la logic di reflection dal generatore per semplicita perche l'output e gia in stream.
# Trovo e distruggo if domini_mancanti and tentativo_riflessione == 0:
import re
stream_str = re.sub(r'if domini_mancanti and tentativo_riflessione == 0:.*?continue', 'pass', stream_str, flags=re.DOTALL)

stream_str = stream_str.replace('return {"reply": testo_pulito}', "yield 'data: {\"done\": true}\\n\\n'")

# Inserisco in app.py
route_stream = '''
@app.route("/api/v1/chat/stream", methods=["POST"])
def chat_stream():
    user_query = request.json.get("message", "") if request.json else ""
    if not user_query:
        return jsonify({"error": "Empty message"}), 400
    stato, sid = ottieni_sessione()
    from flask import Response
    return Response(stream_messaggio_nino(user_query, stato, sid), mimetype="text/event-stream")

def chat():'''

text = text.replace('def chat():', stream_str + '\n' + route_stream)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Patch pulita applicata!")
