"""
test_persistent_storage_recovery.py
Rigorous Verification of Issue 3: Persistent Storage Recovery via SQLite Dual-Mirror & Render Disk
"""

import os
import sys
import json
import sqlite3
from pathlib import Path
import yaml

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath("."))
import app

def test_sqlite_dual_mirror_recovery():
    print("=" * 80)
    print(" [ISSUE 3 TEST] Persistent Storage: SQLite Dual-Mirror Recovery & Render Disk")
    print("=" * 80)

    # Disable background threads
    app.MasterQuantEngine.start_background_workers = lambda self: None
    app.MasterQuantEngine.poll_kotak_neo = lambda self, force=False: None

    engine = app.MasterQuantEngine()
    engine.authenticated = True
    engine.execution_mode = "LIVE"
    engine.terminal_order_ids = {"ORD_LIVE_9001", "ORD_LIVE_9002"}
    engine.closed_trade_ids = {"TRD_LIVE_001"}
    engine.last_trade_exit_time = 1763700000.0
    engine.manual_paused = True
    engine.paused_reason = "Manual safety pause test"

    pos = {
        "trade_id": "TRD-PERSIST-TEST-01",
        "direction": "BULL_PUT_SPREAD",
        "sell_symbol": "NIFTY26NOV25500PE",
        "buy_symbol": "NIFTY26NOV25150PE",
        "sell_strike": 25500, "sell_type": "PE",
        "buy_strike": 25150, "buy_type": "PE",
        "sell_price": 140.0, "buy_price": 30.0,
        "net_credit": 110.0, "lot_size": 65, "lot_count": 1,
        "entry_credit": 110.0, "target_profit_inr": 2860.0,
        "max_risk_inr": 2800.0, "trail_trigger_inr": 1787.5,
        "status": "OPEN", "execution_mode": "LIVE"
    }
    engine.state["active_position"] = pos
    engine.state["today_wins"] = 1
    engine.state["today_losses"] = 0
    engine.state["today_realized_net_pnl"] = 2860.0

    # 1. Save state and engine mode (both JSON and SQLite)
    engine.save_state()
    engine.save_engine_mode()

    # Verify SQLite DB exists and holds data
    db_file = app.RESULTS_DIR / "quant_state.db"
    assert db_file.exists(), f"quant_state.db was not created at {db_file}!"
    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        cur.execute("SELECT val_json FROM engine_kv WHERE key = 'state'")
        row = cur.fetchone()
        assert row is not None and row[0], "State was not written to SQLite engine_kv!"
        db_state = json.loads(row[0])
        assert db_state.get("active_position", {}).get("trade_id") == "TRD-PERSIST-TEST-01"

        cur.execute("SELECT order_id FROM order_registry")
        db_orders = {r[0] for r in cur.fetchall()}
        assert "ORD_LIVE_9001" in db_orders and "ORD_LIVE_9002" in db_orders, "Order IDs not synced to SQLite order_registry!"
    print("  [PASS] Subtest 3A: State and Order IDs successfully dual-mirrored to SQLite DB.")

    # 2. Simulate Catastrophic Ephemeral Container Rebuild:
    # Delete all JSON files from disk
    for f in [app.STATE_FILE, app.STATE_BACKUP_FILE, app.ENGINE_MODE_FILE]:
        if f.exists():
            f.unlink()
    assert not app.STATE_FILE.exists(), "STATE_FILE should be unlinked for test"
    assert not app.ENGINE_MODE_FILE.exists(), "ENGINE_MODE_FILE should be unlinked for test"

    # 3. Instantiate fresh engine from scratch with ONLY SQLite DB present on persistent disk
    restored_engine = app.MasterQuantEngine()
    
    # Verify that load_state and load_engine_mode restored everything from SQLite
    assert restored_engine.state.get("active_position") is not None, "active_position was NOT restored from SQLite!"
    assert restored_engine.state["active_position"]["trade_id"] == "TRD-PERSIST-TEST-01"
    assert restored_engine.state["active_position"]["max_risk_inr"] == 2800.0
    assert restored_engine.state["today_wins"] == 1
    assert restored_engine.state["today_realized_net_pnl"] == 2860.0
    assert "ORD_LIVE_9001" in restored_engine.terminal_order_ids, "terminal_order_ids was NOT restored from SQLite!"
    assert restored_engine.last_trade_exit_time == 1763700000.0, "last_trade_exit_time was NOT restored from SQLite!"
    assert restored_engine.manual_paused is True, "manual_paused was NOT restored from SQLite!"
    assert restored_engine.paused_reason == "Manual safety pause test"
    print("  [PASS] Subtest 3B: Complete State, Active Position, Orders & Engine Mode restored from SQLite.")

    # 4. Verify Render FREE Plan Configuration & Zero Paid Persistent Disks
    for path in [Path("render.yaml"), Path("RENDER_DEPLOY_FILES/render.yaml")]:
        assert path.exists(), f"{path} is missing!"
        with open(path, "r", encoding="utf-8") as yf:
            cfg = yaml.safe_load(yf)
        srv = cfg["services"][0]
        assert srv.get("plan") == "free", f"Render plan in {path} must be free (was: {srv.get('plan')})!"
        assert "disk" not in srv, f"Paid persistent disk must NOT be present on Free Plan in {path}!"
        print(f"  [PASS] Subtest 3C: Verified Render FREE Plan in {path} with 0 Paid Disks.")

    # 5. Verify Restart Safety: LIVE mode armed before reboot does NOT stay armed on reboot!
    engine.manual_paused = False
    engine.paused_reason = ""
    engine.save_engine_mode()

    boot_engine = app.MasterQuantEngine()
    assert boot_engine.execution_mode == "LIVE", "Execution mode should be LIVE"
    assert boot_engine.manual_paused is True, "LIVE mode must NOT auto-arm on restart!"
    assert "RESTART_SAFETY" in boot_engine.paused_reason, f"Unexpected paused reason: {boot_engine.paused_reason}"
    assert boot_engine.reconciliation_blocked is True, "Reconciliation must block trades on startup until verified!"
    print("  [PASS] Subtest 3D: Verified Restart Safety - LIVE mode strictly disarmed and pending reconciliation.")

    print("\n>>> ALL RENDER FREE PLAN PERSISTENCE & SAFETY TESTS PASSED (100%)!\n")
    return True

if __name__ == "__main__":
    test_sqlite_dual_mirror_recovery()
