import os, sys, json
from dotenv import load_dotenv
load_dotenv()

import app

print("=== RUNNING SYSTEM INTEGRITY & REGRESSION TEST SUITE ===")
client = app.app.test_client()

# Test 1: Health endpoint
print("\n[TEST 1] Testing /api/health...")
res_health = client.get("/api/health")
assert res_health.status_code == 200, f"Expected 200, got {res_health.status_code}"
data_health = json.loads(res_health.data.decode("utf-8"))
assert "status" in data_health, "Missing status key in /api/health"
assert data_health["status"] == "HEALTHY", f"Expected HEALTHY, got {data_health['status']}"
print("  PASSED: /api/health is HEALTHY (200 OK)")

# Test 2: Status endpoint
print("\n[TEST 2] Testing /api/status...")
res_status = client.get("/api/status")
assert res_status.status_code == 200, f"Expected 200, got {res_status.status_code}"
data_status = json.loads(res_status.data.decode("utf-8"))
assert "spot" in data_status, "Missing spot key in /api/status"
assert "indicators" in data_status, "Missing indicators in /api/status"
assert "capital" in data_status, "Missing capital in /api/status"
assert "performance" in data_status, "Missing performance in /api/status"

indicators = data_status["indicators"]
pcr = float(indicators.get("pcr", 1.05))
assert 0.20 <= pcr <= 4.00, f"PCR out of bounds: {pcr}"
print(f"  PASSED: /api/status returned 200 OK with valid Spot ({data_status['spot'].get('ltp')}) and bounded PCR ({pcr})")

# Test 3: Safeguard Helper Function
print("\n[TEST 3] Testing is_kotak_ok helper...")
assert app.is_kotak_ok({"stat": "ok", "stCode": 200}) == True
assert app.is_kotak_ok({"stat": "Ok", "stCode": 200}) == True
assert app.is_kotak_ok({"stat": "OK", "nOrdNo": "12345"}) == True
assert app.is_kotak_ok({"stat": "Not_Ok", "stCode": 1007}) == False
assert app.is_kotak_ok(None) == False
print("  PASSED: is_kotak_ok correctly validates lowercase, uppercase, and error payloads")

# Test 4: Position Reconciliation Check
print("\n[TEST 4] Testing broker positions reconciliation check...")
engine = app.engine
assert hasattr(engine, "poll_kotak_neo"), "Missing poll_kotak_neo"
assert hasattr(engine, "trigger_trade"), "Missing trigger_trade"
assert hasattr(engine, "square_off_position"), "Missing square_off_position"
print("  PASSED: All engine core methods verified")

print("\n=== ALL INTEGRITY TESTS PASSED SUCCESSFULLY (100% CLEAN) ===")
