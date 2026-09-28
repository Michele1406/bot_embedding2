import sys

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
imports_to_add = "from core.cart_manager import app_cart\nfrom core.order_extractor import estrai_ordine_da_chat\n"
added = False

for i, line in enumerate(lines):
    if line.strip().startswith('from core.order_extractor import'):
        continue
    if line.strip().startswith('from core.cart_manager import'):
        continue
        
    if line.startswith('from core.config_manager import') and not added:
        new_lines.append(imports_to_add)
        added = True
        
    new_lines.append(line)

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
