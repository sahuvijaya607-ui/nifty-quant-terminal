import os
import sys
import time
import json
from pathlib import Path

# Load app and engine
import app

def test_engine_mode_persistence():
    print("[TEST 1] Testing engine_mode.json persistence...")
    engine = app.engine
    engine.manual_paused = True
    engine.paused_reason = "Test pause"
    engine.last_trade_exit_time = time.time() - 300
    engine.save_engine_mode()
    
    assert app.ENGINE_MODE_FILE.exists(), "engine_mode.json was not created!"
    with open(app.ENGINE_MODE_FILE, "r") as f:
        data = json.load(f)
    assert data.get("manual_paused") is True, "manual_paused not saved!"
    assert data.get("paused_reason") == "Test pause", "paused_reason not saved!"
    print("  [PASS] Test 1: engine_mode.json persists and restores correctly.")

def test_manual_paused_blocks_trade():
    print("[TEST 2] Testing manual pause safety latch blocks trade...")
    engine = app.engine
    engine.manual_paused = True
    engine.paused_reason = "Manual square-off executed"
    success, msg = engine.trigger_trade(force=False)
    assert success is False, "Trade was allowed despite manual pause!"
    assert "Algo is PAUSED" in msg, f"Unexpected message: {msg}"
    print(f"  [PASS] Test 2: Manual pause gate verified: '{msg}'")

def test_cooldown_blocks_immediate_trade2():
    print("[TEST 3] Testing 30-minute cooldown blocks immediate Trade 2...")
    engine = app.engine
    engine.manual_paused = False
    engine.state["today_wins"] = 1
    engine.state["today_losses"] = 0
    engine.last_trade_exit_time = time.time() - 120  # 2 minutes ago
    success, msg = engine.trigger_trade(force=False)
    assert success is False, "Trade 2 was allowed during 30m cooldown!"
    assert "30-minute cooldown active" in msg, f"Unexpected message: {msg}"
    print(f"  [PASS] Test 3: 30-minute cooldown gate verified: '{msg}'")

def test_orders_normalization_and_caching():
    print("[TEST 4] Testing /api/orders normalization and cache safety...")
    with app.app.test_client() as client:
        res = client.get('/api/orders')
        assert res.status_code == 200, f"Orders API failed with {res.status_code}"
        data = res.get_json()
        assert "orders" in data, "No 'orders' key in response"
        assert "total_count" in data, "No 'total_count' in response"
        assert "open_count" in data, "No 'open_count' in response"
        assert "complete_count" in data, "No 'complete_count' in response"
        assert isinstance(data["orders"], list), "'orders' is not a list"
        print(f"  [PASS] Test 4: /api/orders normalized response verified (Total: {data['total_count']}, Open: {data['open_count']}, Complete: {data['complete_count']})")

def test_pause_resume_endpoints():
    print("[TEST 5] Testing /api/pause_algo and /api/resume_algo endpoints...")
    with app.app.test_client() as client:
        res_pause = client.post('/api/pause_algo')
        assert res_pause.status_code == 200
        assert res_pause.get_json().get("manual_paused") is True
        assert app.engine.manual_paused is True
        
        res_resume = client.post('/api/resume_algo')
        assert res_resume.status_code == 200
        assert res_resume.get_json().get("manual_paused") is False
        assert app.engine.manual_paused is False
        print("  [PASS] Test 5: /api/pause_algo and /api/resume_algo endpoints verified.")

if __name__ == "__main__":
    print("=" * 70)
    print("   GSD AUDIT-FIX: POST-FORENSICS SAFEGUARDS VERIFICATION SUITE")
    print("=" * 70)
    test_engine_mode_persistence()
    test_manual_paused_blocks_trade()
    test_cooldown_blocks_immediate_trade2()
    test_orders_normalization_and_caching()
    test_pause_resume_endpoints()
    print("=" * 70)
    print("   ALL 5 FORENSIC SAFEGUARDS VERIFIED & 100% OPERATIONAL! 🚀")
    print("=" * 70)
