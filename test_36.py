
import os
from dotenv import load_dotenv
load_dotenv()
from google import genai
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
from app import analizza_richiesta_unificata
res = analizza_richiesta_unificata(client, 'fammi un tagliere con 3 salumi e formaggi, fai 2 , e dammi anche dei sottoli', '', {})
for e in res.elementi_richiesti:
    print(f'{e.dominio} | quantita={e.quantita} | query={e.query_ricerca} | sotto={e.sottocategoria}')

