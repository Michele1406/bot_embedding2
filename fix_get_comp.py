import re

with open(r"C:\Users\baron\Documents\bot embedding 2\core\retrieval_utils.py", "r", encoding="utf-8") as f:
    content = f.read()

old_block = """        def get_comps_piano(ruolo_target: str) -> list:
            comps = []
            if not piano_ricerca or "componenti" not in piano_ricerca:
                return comps
            for c in piano_ricerca.get("componenti", []):
                if c.get("ruolo") == ruolo_target:
                    comps.append(c)
            return comps"""

new_block = """        def get_comps_piano(ruolo_target: str) -> list:
            comps = []
            if not piano_ricerca or "componenti" not in piano_ricerca:
                return comps
            for c in piano_ricerca.get("componenti", []):
                if c.get("ruolo") == ruolo_target:
                    comps.append(c)
            return comps
            
        def get_comp_piano(ruolo_target: str) -> dict:
            comps = get_comps_piano(ruolo_target)
            return comps[0] if comps else {}"""

if old_block in content:
    content = content.replace(old_block, new_block)
    with open(r"C:\Users\baron\Documents\bot embedding 2\core\retrieval_utils.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("get_comp_piano fixed.")
else:
    print("Block not found!")
