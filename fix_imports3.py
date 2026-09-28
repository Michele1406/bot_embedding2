import sys

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith('from core.cart_manager import') or line.startswith('from core.order_extractor import'):
        continue
    new_lines.append(line)

new_lines.insert(1, 'from core.cart_manager import app_cart\nfrom core.order_extractor import estrai_ordine_da_chat\n')

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
