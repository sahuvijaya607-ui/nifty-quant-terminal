"""
Render Anti-Sleep External Pinger & Health Monitor.
Pings the public Render deployment every 3 minutes to keep the container permanently awake
and ensures option chain and PCR data remain fresh.
"""

import os
import sys
import time
import requests
from datetime import datetime

RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://nifty-quant-terminal.onrender.com").rstrip("/")
PING_ENDPOINT = f"{RENDER_URL}/api/ping"
STATUS_ENDPOINT = f"{RENDER_URL}/api/status"

def ping_once():
    t0 = time.time()
    try:
        r = requests.get(PING_ENDPOINT, timeout=15, headers={"User-Agent": "Antigravity-Watchdog/1.0"})
        elapsed = time.time() - t0
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if r.status_code == 200:
            data = r.json()
            print(f"[{now_str}] PING OK ({elapsed:.2f}s) | Server: {data.get('server_time')} | Mode: {data.get('execution_mode')} | Armed: {data.get('is_armed')} | PCR: {data.get('pcr')} | Spot: {data.get('spot')}")
            return True
        else:
            print(f"[{now_str}] PING NON-200 ({r.status_code}) in {elapsed:.2f}s")
            return False
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] PING ERROR: {e}")
        return False

def run_loop(interval_sec=180):
    print(f"Starting Render Anti-Sleep Watchdog for {RENDER_URL} (Interval: {interval_sec}s)...")
    while True:
        ping_once()
        time.sleep(interval_sec)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        ok = ping_once()
        sys.exit(0 if ok else 1)
    else:
        run_loop(180)
