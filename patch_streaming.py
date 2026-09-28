import sys
import re

with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Trovo l'intero elabora_messaggio_nino per clonarlo
match = re.search(r'def elabora_messaggio_nino\(user_query: str, stato: dict, sid: str\) -> dict:.*?(?=def chat\(\):)', text, re.DOTALL)
if match:
    elabora_str = match.group(0)
    
    # Rinominiamo la funzione clona
    stream_str = elabora_str.replace('def elabora_messaggio_nino(user_query: str, stato: dict, sid: str) -> dict:', 'def stream_messaggio_nino(user_query: str, stato: dict, sid: str):')
    
    # Sostituiamo i print e l'esecuzione bloccante con yield
    stream_str = stream_str.replace('analisi = analizza_richiesta_unificata(client_genai, user_query_clean, contesto_conversazione, stato)', 'yield \'data: {"status": "Analizzo la richiesta..."}\\n\\n\'\n    analisi = analizza_richiesta_unificata(client_genai, user_query_clean, contesto_conversazione, stato)')
    
    stream_str = stream_str.replace('risultati_parziali = cerca_prodotti(', 'yield \'data: {"status": "Cerco a catalogo..."}\\n\\n\'\n                risultati_parziali = cerca_prodotti(')
    
    # Rimuoviamo il blocco reflection dal generatore per semplicita
    # Troviamo la parte della generazione:
    # response = chat_session.send_message(prompt_finale)
    # testo_pulito = (response.text or "").strip()
    stream_str = re.sub(
        r'response = chat_session\.send_message\(prompt_finale\).*?break',
        '''yield 'data: {"status": "Scrivo la risposta..."}\\n\\n'
        response_stream = chat_session.send_message_stream(prompt_finale)
        testo_pulito = ""
        for chunk in response_stream:
            if chunk.text:
                testo_pulito += chunk.text
                chunk_json = json.dumps({"chunk": chunk.text})
                yield f"data: {chunk_json}\\n\\n"
        break''',
        stream_str,
        flags=re.DOTALL
    )
    
    # Alla fine invece di return {"reply": testo_pulito}, yield done
    stream_str = stream_str.replace('return {"reply": testo_pulito}', 'yield \'data: {"done": true}\\n\\n\'')
    
    # Aggiungiamo la nuova funzione prima di def chat()
    text = text.replace('def chat():', stream_str + '\n@app.route("/api/v1/chat/stream", methods=["POST"])\ndef chat_stream():\n    user_query = request.json.get("message", "") if request.json else ""\n    if not user_query:\n        return jsonify({"error": "Empty message"}), 400\n    stato, sid = ottieni_sessione()\n    from flask import Response\n    return Response(stream_messaggio_nino(user_query, stato, sid), mimetype="text/event-stream")\n\ndef chat():')
    
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("Patch SSE Streaming applicata!")
else:
    print("Non trovato elabora_messaggio_nino")
