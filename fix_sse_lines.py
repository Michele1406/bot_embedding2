import sys

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
in_elabora = False
elabora_lines = []

for line in lines:
    if line.startswith('def elabora_messaggio_nino'):
        in_elabora = True
    if in_elabora:
        if line.startswith('def chat():') or line.startswith('@app.route("/chat"'):
            in_elabora = False
        else:
            elabora_lines.append(line)

stream_lines = []
for line in elabora_lines:
    if line.startswith('def elabora_messaggio_nino'):
        stream_lines.append(line.replace('elabora_messaggio_nino', 'stream_messaggio_nino').replace('-> dict:', ':'))
        continue
    
    if 'analisi = analizza_richiesta_unificata' in line:
        stream_lines.append('    yield \'data: {"status": "Analizzo la richiesta..."}\\n\\n\'\n')
        stream_lines.append(line)
        continue
        
    if 'risultati_parziali = cerca_prodotti(' in line:
        indent = line.split('risultati_parziali')[0]
        stream_lines.append(indent + 'yield \'data: {"status": "Ricerco a catalogo..."}\\n\\n\'\n')
        stream_lines.append(line)
        continue
        
    if 'response = chat_session.send_message(prompt_finale)' in line:
        indent = line.split('response')[0]
        stream_lines.append(indent + 'yield \'data: {"status": "Scrivo la risposta..."}\\n\\n\'\n')
        stream_lines.append(indent + 'response_stream = chat_session.send_message_stream(prompt_finale)\n')
        stream_lines.append(indent + 'testo_pulito = ""\n')
        stream_lines.append(indent + 'for chunk in response_stream:\n')
        stream_lines.append(indent + '    if chunk.text:\n')
        stream_lines.append(indent + '        testo_pulito += chunk.text\n')
        stream_lines.append(indent + '        import json\n')
        stream_lines.append(indent + '        chunk_json = json.dumps({"chunk": chunk.text})\n')
        stream_lines.append(indent + '        yield f"data: {chunk_json}\\n\\n"\n')
        continue
        
    if 'testo_pulito = (response.text or "").strip()' in line:
        continue
        
    if 'return {"reply": testo_pulito}' in line:
        indent = line.split('return')[0]
        stream_lines.append(indent + 'yield \'data: {"done": true}\\n\\n\'\n')
        continue
        
    stream_lines.append(line)

final_lines = []
inserted = False
for line in lines:
    if line.startswith('@app.route("/chat"') and not inserted:
        final_lines.extend(stream_lines)
        final_lines.append('@app.route("/api/v1/chat/stream", methods=["POST"])\n')
        final_lines.append('def chat_stream():\n')
        final_lines.append('    user_query = request.json.get("message", "") if request.json else ""\n')
        final_lines.append('    if not user_query:\n')
        final_lines.append('        from flask import jsonify\n')
        final_lines.append('        return jsonify({"error": "Empty message"}), 400\n')
        final_lines.append('    stato, sid = ottieni_sessione()\n')
        final_lines.append('    from flask import Response\n')
        final_lines.append('    return Response(stream_messaggio_nino(user_query, stato, sid), mimetype="text/event-stream")\n\n')
        final_lines.append(line)
        inserted = True
    else:
        final_lines.append(line)

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(final_lines)

print("Patch SSE completata!")
