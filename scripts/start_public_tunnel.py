"""
Starts Cloudflare Tunnel for localhost:5000, captures public HTTPS link,
and saves it to results/public_url.txt for instant remote mobile access.
"""

import os
import sys
import re
import time
import subprocess
from pathlib import Path

def start_tunnel():
    cf_path = Path("cloudflared.exe")
    if not cf_path.exists():
        print("cloudflared.exe not found!")
        return None

    out_file = Path("results/public_url.txt")
    out_file.parent.mkdir(parents=True, exist_ok=True)

    print("Starting Cloudflare secure tunnel to http://localhost:5000 ...")
    cmd = [str(cf_path.resolve()), "tunnel", "--url", "http://localhost:5000"]

    # Start process and pipe stderr (cloudflared prints URL to stderr)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    url = None
    start_time = time.time()
    
    # Read stderr lines looking for trycloudflare.com
    for line in proc.stderr:
        print(line, end="")
        m = re.search(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)", line)
        if m:
            url = m.group(1)
            print("\n" + "="*80)
            print(f"  >>> PUBLIC LIVE MOBILE ACCESS LINK: {url}")
            print("="*80 + "\n")
            with open(out_file, "w") as f:
                f.write(url)
            break
        if time.time() - start_time > 20:
            print("Tunnel startup timeout.")
            break

    return url

if __name__ == "__main__":
    start_tunnel()
