import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import json

print("=== DEEP DEBUGGING BACKEND ROUTE INTEGRITY ===")
client = app.app.test_client()

routes_to_test = [
    ('/', 200),
    ('/api/ping', 200),
    ('/api/health', 200),
    ('/api/status', 200),
    ('/api/orders', 200),
    ('/api/trades', 200),
    ('/api/chart_data', 200),
    ('/api/daily_report', 200),
    ('/api/day_by_day_journal', 200)
]

failed = 0
for route, expected_code in routes_to_test:
    try:
        res = client.get(route)
        if res.status_code == expected_code:
            print(f"  [PASS] GET {route} -> HTTP {res.status_code}")
        else:
            print(f"  [FAIL] GET {route} -> Expected {expected_code}, got {res.status_code}")
            failed += 1
    except Exception as e:
        print(f"  [CRASH] GET {route} -> Exception: {e}")
        failed += 1

print("\n=== TESTING POST ACTIONS INTEGRITY ===")
post_tests = [
    ('/api/pause_algo', {}, 200),
    ('/api/resume_algo', {}, 200),
    ('/api/set_lot_size', {"lots": 1}, 200),
    ('/api/set_lot_size', {"lots": 0}, 400), # Validation check
    ('/api/set_lot_size', {"lots": 105}, 400), # Max cap check
]

for route, payload, expected_code in post_tests:
    try:
        res = client.post(route, json=payload)
        if res.status_code == expected_code:
            print(f"  [PASS] POST {route} with {payload} -> HTTP {res.status_code}")
        else:
            print(f"  [FAIL] POST {route} with {payload} -> Expected {expected_code}, got {res.status_code}")
            failed += 1
    except Exception as e:
        print(f"  [CRASH] POST {route} -> Exception: {e}")
        failed += 1

print(f"\nFinal Route Integrity Audit: {failed} failures.")
assert failed == 0, f"Route integrity audit failed with {failed} failures!"
print("SUCCESS: 100% of tested routes passed perfectly!")
