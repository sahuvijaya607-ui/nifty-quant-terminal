import re

with open('templates/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

unsafe_accesses = []
for i, line in enumerate(lines, 1):
    # Matches document.getElementById('...').property or document.getElementById('...').method(
    matches = re.finditer(r"document\.getElementById\(['\"]([^'\"]+)['\"]\)\.([a-zA-Z]+)", line)
    for m in matches:
        el_id = m.group(1)
        prop = m.group(2)
        unsafe_accesses.append((i, el_id, prop, line.strip()))

print(f"Total direct (chained) accesses: {len(unsafe_accesses)}")

# Check each one against the HTML body
html_body = "".join(lines[:1600]) # HTML body is roughly lines 1-1600
missing_unsafe = []
for line_no, el_id, prop, line_text in unsafe_accesses:
    if f'id="{el_id}"' not in html_body and f"id='{el_id}'" not in html_body:
        missing_unsafe.append((line_no, el_id, prop, line_text))

print(f"Unsafe accesses on potentially MISSING elements: {len(missing_unsafe)}")
for line_no, el_id, prop, line_text in missing_unsafe:
    print(f"  Line {line_no}: ID '{el_id}' -> .{prop} | Code: {line_text[:80]}")
