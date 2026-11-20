"""
scripts/run_all_final_audits.py
Executes all 4 Master Regression Suites Sequentially:
1. test_deployment_readiness_audit.py (Partial Exit, Cooldown, Reconcile, Duplicate Order)
2. test_candle_close_confirmation_production.py (5m Candle Close, Lag, Tick Age, 09:55:01)
3. test_post_audit_all_7_fixes.py (All 7 Multi-Agent Fixes)
4. test_persistent_storage_recovery.py (SQLite Dual-Mirror, Render Disk)
"""

import subprocess
import sys
import time

suites = [
    ("Deployment Readiness Suite", "test_deployment_readiness_audit.py"),
    ("Candle Close Confirmation Suite", "test_candle_close_confirmation_production.py"),
    ("All 7 Multi-Agent Fixes Suite", "test_post_audit_all_7_fixes.py"),
    ("Persistent Storage Recovery Suite", "test_persistent_storage_recovery.py"),
]

print("=" * 100)
print("              MASTER VERIFICATION RUNNER: ALL 4 SUITES EXECUTION")
print("=" * 100)

all_passed = True
results = []

for name, script in suites:
    t0 = time.time()
    print(f"\n>>> Running: {name} ({script})...")
    res = subprocess.run([sys.executable, script], capture_output=True, text=True)
    dur = time.time() - t0
    if res.returncode == 0:
        print(f"    [PASS] {name} completed successfully in {dur:.1f}s.")
        results.append((name, "PASS", dur))
    else:
        print(f"    [FAIL] {name} failed with return code {res.returncode} in {dur:.1f}s:")
        print(res.stdout[-1000:])
        print(res.stderr[-1000:])
        results.append((name, "FAIL", dur))
        all_passed = False

print("\n" + "=" * 100)
print("                               FINAL VERIFICATION SUMMARY")
print("=" * 100)
for name, status, dur in results:
    print(f"• {name:<45}: {status} ({dur:.1f}s)")
print("=" * 100)
print("OVERALL VERDICT: " + ("ALL SUITES PASSED (100%)" if all_passed else "FAILURES DETECTED"))
print("=" * 100)

sys.exit(0 if all_passed else 1)
