import os
from dotenv import load_dotenv
load_dotenv()
from google import genai
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
import chromadb
from core.retrieval_utils import componi_proposta_da_ricettario
from app import EmbedderMultimodaleGemini
c_ric = chromadb.PersistentClient(r'C:\Users\baron\Documents\bot embedding 2\database_vettoriale').get_collection('ricette_sofood')
c_prod = chromadb.PersistentClient(r'C:\Users\baron\Documents\bot embedding 2\database_vettoriale').get_collection('catalogo_sofood')
embedder = EmbedderMultimodaleGemini(os.getenv('GEMINI_API_KEY'), 'models/gemini-embedding-2')
res = componi_proposta_da_ricettario('fammi un tagliere con 3 salumi e formaggi, fai 2 , e dammi anche dei sottoli', None, c_ric, c_prod, {}, embedder, target_salumi=3, target_formaggi=2)
for slot in res['slot']:
    nome = slot.get('prodotto_trovato', {}).get('document', '').splitlines()[0] if slot.get('prodotto_trovato') else 'NON TROVATO'
    print(f"{slot.get('ingrediente_richiesto')} | {slot.get('categoria_attesa')} | {nome}")
