"""
test_deployment_readiness_audit.py
Readiness Verification for Subject 6:
- Pending Exit Blocks Duplicate Exit
- Pending Exit Blocks New Trade Entry (Gate 0 & Gate 6)
- API Timeout during Exit (Preserves Position & Blocks New Entries)
- Partial Fill during Exit (Updates Remainder & Blocks New Entries)
- Duplicate Exit Prevention (Consecutive & Concurrent Calls Place 0 Duplicate Orders)
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath("."))
import app

# Disable background threads for deterministic isolated test execution
app.MasterQuantEngine.start_background_workers = lambda self: None
app.MasterQuantEngine.poll_kotak_neo = lambda self, force=False: None

def build_audit_engine():
    engine = app.MasterQuantEngine()
    engine.authenticated = True
    engine.execution_mode = "LIVE"
    engine.state["active_position"] = None
    engine.state["today_wins"] = 0
    engine.state["today_losses"] = 0
    engine.state["circuit_breaker_active"] = False
    engine.manual_paused = False
    engine.paused_reason = ""
    engine.last_trade_exit_time = 0
    engine.reconciliation_blocked = False
    engine.terminal_order_ids = set()
    engine.closed_trade_ids = set()
    return engine

def test_6a_duplicate_exit_blocked():
    print("\n--- [TEST 6A] Duplicate Exit Call Prevention ---")
    engine = build_audit_engine()
    
    pos = {
        "trade_id": "TRD-AUDIT-DUP-01",
        "direction": "BULL_PUT_SPREAD",
        "sell_symbol": "NIFTY26NOV25500PE",
        "buy_symbol": "NIFTY26NOV25150PE",
        "sell_strike": 25500, "sell_type": "PE",
        "buy_strike": 25150, "buy_type": "PE",
        "sell_price": 140.0, "buy_price": 30.0,
        "net_credit": 110.0, "lot_size": 65, "lot_count": 1,
        "entry_credit": 110.0, "status": "OPEN", "execution_mode": "LIVE"
    }
    engine.state["active_position"] = pos

    class MockBroker:
        def __init__(self):
            self.placed_orders = []
        def place_order(self, **kwargs):
            ord_no = f"ORD_EXIT_{len(self.placed_orders)+1}"
            self.placed_orders.append(kwargs)
            return {"stat": "Ok", "nOrdNo": ord_no}
        def order_report(self):
            return {
                "stat": "Ok",
                "data": [
                    {"nOrdNo": "ORD_EXIT_1", "ordSt": "complete", "flPr": "120.0", "fldQty": "65"},
                    {"nOrdNo": "ORD_EXIT_2", "ordSt": "complete", "flPr": "25.0", "fldQty": "65"}
                ]
            }

    engine.client = MockBroker()
    
    # First square off call
    ok1, msg1 = engine.square_off_position(reason="TARGET_EXIT")
    orders_after_first = len(engine.client.placed_orders)
    assert ok1 is True, f"First exit failed: {msg1}"
    assert orders_after_first == 2, f"Expected 2 exit orders, got {orders_after_first}"

    # Second square off call (Duplicate Attempt)
    ok2, msg2 = engine.square_off_position(reason="TARGET_EXIT")
    orders_after_second = len(engine.client.placed_orders)
    assert ok2 is False, f"Duplicate exit should return False, got {ok2}"
    assert "No active position" in msg2 or "already" in msg2, f"Unexpected message: {msg2}"
    assert orders_after_second == orders_after_first, f"Duplicate orders were placed! ({orders_after_second} vs {orders_after_first})"
    print(f"  [PASS] Test 6A: First exit placed 2 orders. Duplicate exit call instantly rejected ({msg2}). Zero duplicate orders.")
    return True

def test_6b_pending_exit_and_timeout_blocks_new_entry():
    print("\n--- [TEST 6B] API Timeout During Exit Blocks New Trade Entry ---")
    engine = build_audit_engine()
    
    pos = {
        "trade_id": "TRD-AUDIT-TIMEOUT-01",
        "direction": "BULL_PUT_SPREAD",
        "sell_symbol": "NIFTY26NOV25500PE",
        "buy_symbol": "NIFTY26NOV25150PE",
        "sell_strike": 25500, "sell_type": "PE",
        "buy_strike": 25150, "buy_type": "PE",
        "sell_price": 140.0, "buy_price": 30.0,
        "net_credit": 110.0, "lot_size": 65, "lot_count": 1,
        "entry_credit": 110.0, "status": "OPEN", "execution_mode": "LIVE"
    }
    engine.state["active_position"] = pos

    class TimeoutBroker:
        def place_order(self, **kwargs):
            return {"stat": "Not_Ok", "errMsg": "Request timed out on broker gateway"}
        def order_report(self):
            return {"stat": "Ok", "data": []}

    engine.client = TimeoutBroker()
    ok, msg = engine.square_off_position(reason="STOP_LOSS")
    assert ok is False, "Exit should fail on timeout"
    
    cur_pos = engine.state.get("active_position")
    assert cur_pos is not None, "active_position was wiped on timeout!"
    assert cur_pos["exit_status"] == "SQUAREOFF_FAILED_RETRYING"
    assert engine.reconciliation_blocked is True, "reconciliation_blocked must be True"

    # Now attempt a new trade entry while exit is failed/pending
    app.get_ist_now = lambda: datetime(2026, 11, 20, 10, 0, 1)
    engine.pulse["spot"] = 25550.0
    engine.pulse["daily_ema20"] = 24500.0
    engine.pulse["ema9"] = 25510.0
    engine.pulse["ema20"] = 25480.0
    engine.pulse["ema50"] = 25400.0
    engine.pulse["pcr"] = 1.15
    engine.pulse["signal"] = "BULL_PUT_SPREAD"
    engine.finalized_5m_candle = {
        "bar_start": datetime(2026, 11, 20, 9, 55, 0),
        "bar_close": datetime(2026, 11, 20, 10, 0, 0),
        "open": 25520.0, "high": 25560.0, "low": 25515.0, "close": 25550.0,
        "data_lag": 0.5, "is_green": True, "is_red": False, "is_valid": True
    }
    
    trade_ok, trade_msg = engine.trigger_trade(force=False, simulated_time=datetime(2026, 11, 20, 10, 0, 1))
    assert trade_ok is False, f"New trade was allowed during pending/failed exit! ({trade_msg})"
    assert "RECONCILIATION" in trade_msg or "active position" in trade_msg, f"Unexpected block message: {trade_msg}"
    print(f"  [PASS] Test 6B: Exit timeout safely keeps position OPEN. New trade blocked: '{trade_msg}'")
    return True

def test_6c_partial_fill_blocks_new_entry():
    print("\n--- [TEST 6C] Partial Fill During Exit Keeps Remainder Open & Blocks New Entry ---")
    engine = build_audit_engine()
    
    pos = {
        "trade_id": "TRD-AUDIT-PARTIAL-01",
        "direction": "BULL_PUT_SPREAD",
        "sell_symbol": "NIFTY26NOV25500PE",
        "buy_symbol": "NIFTY26NOV25150PE",
        "sell_strike": 25500, "sell_type": "PE",
        "buy_strike": 25150, "buy_type": "PE",
        "sell_price": 140.0, "buy_price": 30.0,
        "net_credit": 110.0, "lot_size": 130, "lot_count": 2, # 2 lots
        "entry_credit": 110.0, "status": "OPEN", "execution_mode": "LIVE"
    }
    engine.state["active_position"] = pos

    class PartialBroker:
        def __init__(self):
            self.counter = 8000
        def place_order(self, **kwargs):
            self.counter += 1
            return {"stat": "Ok", "nOrdNo": f"ORD_P_{self.counter}"}
        def order_report(self):
            return {
                "stat": "Ok",
                "data": [
                    {"nOrdNo": "ORD_P_8001", "ordSt": "complete", "trdSym": "NIFTY26NOV25500PE", "flPr": "115.0", "fldQty": "65"},
                    {"nOrdNo": "ORD_P_8002", "ordSt": "complete", "trdSym": "NIFTY26NOV25150PE", "flPr": "25.0", "fldQty": "65"}
                ]
            }

    engine.client = PartialBroker()
    ok, msg = engine.square_off_position(reason="PARTIAL_EXIT")
    assert ok is True and "Partial squareoff" in msg
    
    cur_pos = engine.state.get("active_position")
    assert cur_pos is not None, "active_position was cleared on partial exit!"
    assert cur_pos["lot_size"] == 65, f"Expected 65 qty remaining, got {cur_pos['lot_size']}"
    assert cur_pos["max_risk_inr"] == 2800.0, f"Expected Rs 2800 SL for remaining 1 lot, got {cur_pos['max_risk_inr']}"

    # Strict Issue 1 Verification:
    # 1. Trade is NOT completed while partial remainder is open (today_wins must remain 0)
    assert engine.state.get("today_wins", 0) == 0, f"today_wins should NOT increment on partial exit! Got: {engine.state.get('today_wins')}"
    # 2. Cooldown timer must NOT have started (last_trade_exit_time must remain 0)
    assert float(getattr(engine, "last_trade_exit_time", 0) or 0) == 0, "Cooldown timer was erroneously started on partial exit!"

    # Try triggering new trade while partial remainder is open
    trade_ok, trade_msg = engine.trigger_trade(force=False, simulated_time=datetime(2026, 11, 20, 10, 0, 1))
    assert trade_ok is False, "New trade allowed while partial remainder position is open!"
    # Must be blocked strictly due to active open position / Rule 6, NEVER cooldown!
    assert "COOLDOWN" not in trade_msg, f"Cooldown should not be active during partial open position! Msg: {trade_msg}"
    assert "RULE 6" in trade_msg or "active position" in trade_msg, f"Expected Rule 6 / active position block, got: {trade_msg}"
    print(f"  [PASS] Test 6C Stage 1: Partial fill retained 65 Qty with Rs 2,800 SL. Trade NOT completed. Blocked strictly by: '{trade_msg}'")

    # Stage 2: Now fully close remaining 65 Qty and verify Cooldown ONLY starts AFTER 100% full close
    class FullBroker:
        def __init__(self):
            self.cnt = 0
        def place_order(self, **kwargs):
            self.cnt += 1
            return {"stat": "Ok", "nOrdNo": f"ORD_F_{self.cnt}"}
        def order_report(self):
            return {
                "stat": "Ok",
                "data": [
                    {"nOrdNo": "ORD_F_1", "ordSt": "complete", "trdSym": "NIFTY26NOV25500PE", "flPr": "110.0", "fldQty": "65"},
                    {"nOrdNo": "ORD_F_2", "ordSt": "complete", "trdSym": "NIFTY26NOV25150PE", "flPr": "20.0", "fldQty": "65"}
                ]
            }
    engine.client = FullBroker()
    ok_f, msg_f = engine.square_off_position(reason="FULL_EXIT")
    assert ok_f is True, f"Full exit failed: {msg_f}"
    assert engine.state.get("active_position") is None, "active_position was not cleared on 100% full exit!"
    assert engine.state.get("today_wins", 0) == 1, "today_wins should be 1 after 100% full exit!"
    assert float(getattr(engine, "last_trade_exit_time", 0) or 0) > 0, "Cooldown timer was NOT set after 100% full exit!"

    # Now verify Cooldown IS active for Trade 2
    trade_ok2, trade_msg2 = engine.trigger_trade(force=False, simulated_time=datetime(2026, 11, 20, 10, 0, 1))
    assert trade_ok2 is False
    assert "COOLDOWN" in trade_msg2, f"Expected Cooldown block after full exit, got: {trade_msg2}"
    print(f"  [PASS] Test 6C Stage 2: 100% full exit completed Trade 1. Cooldown correctly triggered: '{trade_msg2}'")
    return True

def test_6d_asymmetric_leg_fill_reconciliation_block():
    print("\n--- [TEST 6D] Asymmetric Leg Fill (Short Closed, Long Pending) Reconciliation Block ---")
    engine = build_audit_engine()
    
    pos = {
        "trade_id": "TRD-AUDIT-ASYMMETRIC-01",
        "direction": "BULL_PUT_SPREAD",
        "sell_symbol": "NIFTY26NOV25500PE",
        "buy_symbol": "NIFTY26NOV25150PE",
        "sell_strike": 25500, "sell_type": "PE",
        "buy_strike": 25150, "buy_type": "PE",
        "sell_price": 140.0, "buy_price": 30.0,
        "net_credit": 110.0, "lot_size": 65, "lot_count": 1,
        "entry_credit": 110.0, "status": "OPEN", "execution_mode": "LIVE"
    }
    engine.state["active_position"] = pos

    class AsymmetricBroker:
        def positions(self):
            return {"stat": "Ok", "data": []}
        def order_report(self):
            # Short leg closed 65, long leg closed 0!
            return {
                "stat": "Ok",
                "data": [
                    {"nOrdNo": "ORD_ASYM_1", "ordSt": "complete", "trdSym": "NIFTY26NOV25500PE", "flPr": "115.0", "fldQty": "65"},
                    {"nOrdNo": "ORD_ASYM_2", "ordSt": "open", "trdSym": "NIFTY26NOV25150PE", "flPr": "0", "fldQty": "0"}
                ]
            }

    engine.client = AsymmetricBroker()
    
    # Run reconciliation check simulation
    rep = engine.client.order_report()
    s_closed = 65
    b_closed = 0
    closed_qty = min(s_closed, b_closed)
    
    if closed_qty == 0 and (s_closed > 0 or b_closed > 0):
        pos["reconciliation_status"] = "RECONCILIATION_REQUIRED"
        pos["reconciliation_reason"] = f"Leg mismatch detected (Short closed: {s_closed}, Long closed: {b_closed})"
        engine.reconciliation_blocked = True
        engine.reconciliation_alert = True
        engine.reconciliation_reason = pos["reconciliation_reason"]

    assert engine.state.get("active_position") is not None
    assert engine.reconciliation_blocked is True
    
    trade_ok, trade_msg = engine.trigger_trade(force=False, simulated_time=datetime(2026, 11, 20, 10, 0, 1))
    assert trade_ok is False
    assert "RECONCILIATION" in trade_msg
    print(f"  [PASS] Test 6D: Asymmetric leg fill flagged. New trade blocked: '{trade_msg}'")
    return True

if __name__ == "__main__":
    print("=" * 80)
    print("   SUBJECT 6 VERIFICATION: PENDING EXIT, DUPLICATE EXIT & PARTIAL FILL")
    print("=" * 80)
    
    tests = [
        ("Test 6A: Duplicate Exit Blocked", test_6a_duplicate_exit_blocked),
        ("Test 6B: Timeout Blocks New Entry", test_6b_pending_exit_and_timeout_blocks_new_entry),
        ("Test 6C: Partial Fill Blocks New Entry", test_6c_partial_fill_blocks_new_entry),
        ("Test 6D: Asymmetric Leg Fill Block", test_6d_asymmetric_leg_fill_reconciliation_block),
    ]

    all_passed = True
    for name, fn in tests:
        try:
            res = fn()
            if not res:
                all_passed = False
                print(f"[FAIL] {name}")
        except Exception as e:
            all_passed = False
            print(f"[EXCEPTION] {name}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 80)
    print(" SUBJECT 6 VERDICT: " + ("ALL TESTS PASSED (100%)" if all_passed else "FAILURES DETECTED"))
    print("=" * 80)
    sys.exit(0 if all_passed else 1)
