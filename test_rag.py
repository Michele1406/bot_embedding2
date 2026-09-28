import chromadb
from app import cerca_prodotti, EmbedderMultimodaleGemini
import os
from dotenv import load_dotenv
load_dotenv()
embedder = EmbedderMultimodaleGemini(os.getenv('GEMINI_API_KEY'), 'models/gemini-embedding-2')
c = chromadb.PersistentClient(r'C:\Users\baron\Documents\bot embedding 2\database_vettoriale').get_collection('catalogo_sofood')
res = cerca_prodotti(c, None, embedder, "sottoli olive carciofini antipasto verdure sott'olio", 10)
for r in res:
    print(r['document'].splitlines()[0])
