import sys

with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Modifica AnalisiUnificata enum
text = text.replace("'panoramica_catalogo', 'ricerca_specifica', 'composizione_piatto', 'conversazione_generica'", "'panoramica_catalogo', 'ricerca_specifica', 'composizione_piatto', 'conversazione_generica', 'chiusura_ordine'")

# 2. Aggiungiamo il cart_manager al vertice
if 'from core.order_extractor import estrai_ordine_da_chat' not in text:
    text = text.replace('from core.config_manager import', 'from core.cart_manager import app_cart\nfrom core.order_extractor import estrai_ordine_da_chat\nfrom core.config_manager import')

# 3. Intercettiamo "chiusura_ordine" in chat()
blocco_checkout = '''
    # GESTIONE CHECKOUT / CHIUSURA ORDINE
    if analisi.tipo_richiesta == "chiusura_ordine":
        print(f"[CHECKOUT] Rilevata chiusura ordine per sessione {session_id}")
        
        # Inizializza il carrello (assumiamo tenant 'so_food' di default per il webhook test)
        app_cart.init_cart(session_id, "so_food")
        
        # Estrai l'ordine dalla cronologia
        ordine_estratto = estrai_ordine_da_chat(client_genai, stato["storico"], MODELLO_FALLBACK)
        
        if ordine_estratto.prodotti:
            dati_cliente = {}
            if ordine_estratto.ragione_sociale: dati_cliente["ragione_sociale"] = ordine_estratto.ragione_sociale
            if ordine_estratto.partita_iva: dati_cliente["partita_iva"] = ordine_estratto.partita_iva
            
            app_cart.update_cart(session_id, [p.model_dump() for p in ordine_estratto.prodotti], dati_cliente)
            successo, payload = app_cart.inoltra_ordine_erp(session_id)
            
            if successo:
                stato["storico"].append({"role": "model", "parts": ["Ordine inviato al gestionale. Preparo la conferma per il cliente."]})
'''

# Troviamo il punto dove l'analisi  finita: "analisi = analizza_richiesta_unificata(...)"
if 'analisi = analizza_richiesta_unificata' in text:
    parts = text.split('analisi = analizza_richiesta_unificata(client_genai, user_query_clean, contesto_conversazione, stato)')
    # Inseriamo il blocco subito dopo
    text = parts[0] + 'analisi = analizza_richiesta_unificata(client_genai, user_query_clean, contesto_conversazione, stato)\n' + blocco_checkout + parts[1]

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Patch Fase 4 applicata in app.py!")
