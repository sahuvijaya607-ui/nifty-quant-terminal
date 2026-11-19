import os, sys, shutil, hashlib

def hash_file(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

ROOT = os.path.abspath(os.path.dirname(__file__))
RENDER_DIR = os.path.join(ROOT, "RENDER_DEPLOY_FILES")

SYNC_TARGETS = [
    ("app.py", "app.py"),
    (os.path.join("templates", "index.html"), os.path.join("templates", "index.html")),
    (os.path.join("results", "paper_engine_state.json"), os.path.join("results", "paper_engine_state.json")),
]

print("=== RUNNING RENDER AUTO-SYNC ENGINE ===")
all_match = True

for src_rel, dest_rel in SYNC_TARGETS:
    src = os.path.join(ROOT, src_rel)
    dest = os.path.join(RENDER_DIR, dest_rel)
    
    if not os.path.exists(src):
        print(f"Skipping {src_rel} (Source does not exist)")
        continue
        
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    
    h_src = hash_file(src)
    h_dest = hash_file(dest)
    
    if h_src == h_dest:
        print(f"  [OK] {src_rel} -> {dest_rel} (Hash: {h_src[:8]})")
    else:
        print(f"  [FAIL] Hash mismatch on {src_rel}")
        all_match = False

if all_match:
    print("SUCCESS: RENDER_DEPLOY_FILES is 100% synchronized with Root directory!")
else:
    print("ERROR: Synchronization failed!")
    sys.exit(1)
