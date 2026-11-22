import re

with open('templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

script_idx = html.find('<script>')
body = html[:script_idx]
script = html[script_idx:]

all_ids = set(re.findall(r"document\.getElementById\(['\"]([^'\"]+)['\"]\)", script))
print(f"Total distinct getElementById in script: {len(all_ids)}")
missing = []
for el_id in sorted(all_ids):
    pattern = f'id=[\'"]{re.escape(el_id)}[\'"]'
    if not re.search(pattern, body):
        missing.append(el_id)

print(f"Missing element IDs in HTML body: {len(missing)}")
for m in missing:
    print(f"  [MISSING DOM ID] {m}")

if not missing:
    print("SUCCESS: 100% of getElementById elements exist in HTML body!")
