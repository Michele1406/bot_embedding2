# -*- coding: utf-8 -*-
import os
import re

print('--- TEST 1: Regex Canale Locale ---')
from core.profilazione_locale import rileva_canale_locale
res1 = rileva_canale_locale('ristorante barese')
print(f'ristorante barese -> {res1} (Atteso: horeca)')
res2 = rileva_canale_locale('alimentari barese')
print(f'alimentari barese -> {res2} (Atteso: retail)')
res3 = rileva_canale_locale('un bar in centro')
print(f'un bar in centro -> {res3} (Atteso: horeca)')
res4 = rileva_canale_locale('il re del barese')
print(f'il re del barese -> {res4} (Atteso: misto, perche barese non deve matchare bar)')

print('\n--- TEST 2: Regex Cluster Regionale ---')
from core.retrieval_utils import rileva_cluster_regionale
res5 = rileva_cluster_regionale('voglio delle sardine in scatola')
print(f'sardine -> {res5} (Atteso: None, non sardegna)')
res6 = rileva_cluster_regionale('voglio dei salumi dalla sardegna')
print(f'sardegna -> {res6} (Atteso: sardegna)')

print('\n--- TEST 3: estrai_q fallback ---')
q_lower = 'fammi un tagliere misto'
kws = ['salum', 'prosciutt']
numeri_liberi = []
def test_estrai_q(kws, txt, numeri_liberi):
    for k in kws:
        m = re.search(r'(\d+)\s+(?:\w+\s+){0,2}' + k, txt)
        if m: return int(m.group(1))
    for k in kws:
        m = re.search(k + r'\s+(?:\w+\s+){0,3}(\d+)', txt)
        if m: return int(m.group(1))
    if numeri_liberi: return numeri_liberi.pop(0)
    return None

res7 = test_estrai_q(kws, q_lower, numeri_liberi)
print(f'Quantita estratta -> {res7} (Atteso: None)')
