import os
import sys
import unittest

# Import engine from app.py
from app import engine, get_ist_now

class TestHUDCardsDynamics(unittest.TestCase):
    def test_dynamic_hud_metrics_calculation(self):
        print("\n--- Testing HUD Cards Dynamic Metrics Calculation ---")
        # Simulate active position with entry net credit 108.40 and 65 qty (today's exact trade)
        sim_pos = {
            "trade_id": "TRD-LIVE-TEST-01",
            "entry_time": "2026-11-20 09:53:35",
            "entry_spot": 25530.0,
            "sell_strike": 25500,
            "sell_type": "PE",
            "sell_price": 139.55,
            "buy_strike": 25150,
            "buy_type": "PE",
            "buy_price": 31.15,
            "net_credit": 108.40,
            "lot_size": 65,
            "lot_count": 1,
            "direction": "BULL_PUT_SPREAD",
            "execution_mode": "LIVE",
            "current_sell_ltp": 133.70,
            "current_buy_ltp": 29.65,
            "current_spread": 104.05,
            "broker_unrealised_pnl": 283.0,
            "breakeven_active": False
        }
        engine.state["active_position"] = sim_pos
        engine.update_position_dynamics()
        
        pos = engine.state["active_position"]
        self.assertIsNotNone(pos)
        
        # 1. Max Potential
        self.assertEqual(pos["max_credit_inr"], round(108.40 * 65, 2)) # 7046.00
        print(f"  [PASS] Card 1 Max Credit INR: Rs {pos['max_credit_inr']}")
        
        # 2. 40% Target Exit
        self.assertEqual(pos["target_spread"], round(108.40 * 0.60, 2)) # 65.04
        self.assertEqual(pos["target_profit_inr"], round((108.40 * 0.40) * 65, 2)) # 2818.40
        self.assertIn("target_pts_needed", pos)
        self.assertIn("target_profit_remaining_inr", pos)
        self.assertIn("target_progress_pct", pos)
        print(f"  [PASS] Card 2 40% Target: Rs {pos['target_profit_inr']} (Target Spread: {pos['target_spread']}, Progress: {pos['target_progress_pct']}%)")
        
        # 3. 25% Auto-Trail Breakeven
        self.assertEqual(pos["trail_trigger_spread"], round(108.40 * 0.75, 2)) # 81.30
        self.assertEqual(pos["trail_trigger_inr"], round((108.40 * 0.25) * 65, 2)) # 1761.50
        self.assertIn("trail_pts_needed", pos)
        self.assertIn("trail_profit_remaining_inr", pos)
        self.assertIn("trail_progress_pct", pos)
        print(f"  [PASS] Card 3 25% Auto-Trail: Rs {pos['trail_trigger_inr']} (Trigger Spread: {pos['trail_trigger_spread']}, Needed: Rs {pos['trail_profit_remaining_inr']})")
        
        # 4. Stop Loss & Max Risk
        self.assertEqual(pos["sl_spread"], round(108.40 * 1.60, 2)) # 173.44
        self.assertEqual(pos["max_risk_inr"], 2800.0) # 2800.00 cap
        self.assertIn("sl_buffer_pts", pos)
        self.assertIn("sl_buffer_inr", pos)
        print(f"  [PASS] Card 4 Stop Loss: Cap Rs {pos['max_risk_inr']} (Cut Spread: {pos['sl_spread']}, Buffer: Rs {pos['sl_buffer_inr']})")
        
        # Cleanup
        engine.state["active_position"] = None

if __name__ == "__main__":
    unittest.main()
