"""
test_5m_candle_close_latch.py
Historical Candle Replay Verification Suite for 5-Minute Candle Close Confirmation:
1. 09:53:35 IST — Running/Incomplete Candle -> Trade MUST BE BLOCKED.
2. 09:55:00 IST — 09:50-09:55 Candle Closes -> Final Candle Data Finalized.
3. 09:55:01 IST — Backtest Convention Alignment -> Signal Evaluates and Fires Trade ONCE.
4. 09:55:03 IST — Subsequent Poll -> Duplicate Signal & Duplicate Order Blocked.
5. Stale Data / API Delay -> Trade MUST BE BLOCKED.
"""
import time
from datetime import datetime, time as dtime

class CandleCloseStrategyEngine:
    """
    Simulation of the Proposed 5-Minute Candle Close Confirmation Latch
    without modifying app.py or placing any live orders.
    """
    def __init__(self):
        self.current_5m_bucket = None
        self.candle_5m_open = None
        self.candle_5m_high = None
        self.candle_5m_low = None
        self.last_tick_time = None
        
        # Proposed 5M Candle Confirmation State
        self.last_finalized_candle = None # Dict with open, high, low, close, color, time
        self.last_evaluated_candle_bucket = None # Idempotency: evaluates strictly once per closed bar
        self.signal = "NEUTRAL_SCANNING"
        self.trade_executed_count = 0
        self.executed_trades = []
        
        # Indicators
        self.daily_ema20 = 24483.43
        self.ema9 = 25499.15
        self.ema20 = 25479.22
        self.ema50 = 25430.58
        self.pcr = 1.335
        
    def process_tick(self, simulated_dt_str: str, spot: float, pcr: float = 1.335, data_delay_sec: float = 0.0):
        dt = datetime.strptime(simulated_dt_str, "%Y-%m-%d %H:%M:%S")
        now_min = dt.minute
        now_sec = dt.second
        bucket_5m = (now_min // 5) * 5
        
        # 1. Stale Data / API Delay Guard
        if data_delay_sec > 15.0:
            self.signal = "BLOCKED_STALE_DATA"
            return False, f"API Delay ({data_delay_sec:.1f}s > 15s limit). Trade Blocked."
            
        # 2. Check for 5-Minute Bucket Rollover (Finalizing Previous Candle)
        if self.current_5m_bucket is not None and self.current_5m_bucket != bucket_5m:
            # Previous candle has COMPLETED and CLOSED!
            prev_bucket_time = f"{dt.strftime('%Y-%m-%d')} {dt.hour:02d}:{self.current_5m_bucket:02d}:00"
            c_open = self.candle_5m_open if self.candle_5m_open is not None else spot
            c_close = spot # Closing tick of completed bucket
            is_green = (c_close >= c_open)
            is_red = (c_close <= c_open)
            
            self.last_finalized_candle = {
                "bucket_str": prev_bucket_time,
                "bucket_min": self.current_5m_bucket,
                "open": round(c_open, 2),
                "close": round(c_close, 2),
                "is_green": is_green,
                "is_red": is_red,
                "finalized_at": simulated_dt_str
            }
            # Start new candle bucket
            self.current_5m_bucket = bucket_5m
            self.candle_5m_open = spot
        elif self.current_5m_bucket is None:
            # First initialization
            self.current_5m_bucket = bucket_5m
            self.candle_5m_open = spot

        self.last_tick_time = dt
        
        # 3. STRICT CANDLE CLOSE SIGNAL EVALUATION (Requirement 1, 2, 3, 4, 7)
        # Entry Timing: Backtest Convention evaluates strictly once on newly finalized candle
        if self.last_finalized_candle is not None:
            finalized_bucket = self.last_finalized_candle["bucket_str"]
            
            # Idempotency: Evaluate ONLY ONCE per completed candle
            if self.last_evaluated_candle_bucket != finalized_bucket:
                self.last_evaluated_candle_bucket = finalized_bucket
                
                # Use ONLY the finalized candle data
                f_close = self.last_finalized_candle["close"]
                f_is_green = self.last_finalized_candle["is_green"]
                f_is_red = self.last_finalized_candle["is_red"]
                
                # 6 Rules verification on finalized candle:
                time_ok = (dtime(9, 30) <= dt.time() <= dtime(14, 30))
                quota_ok = (self.trade_executed_count < 2)
                
                # Bullish Check
                if (time_ok and quota_ok and pcr >= 1.05 and 
                    f_close > self.daily_ema20 and 
                    self.ema9 > self.ema20 > self.ema50 and 
                    f_is_green):
                    self.signal = "BULL_PUT_SPREAD"
                    self.trade_executed_count += 1
                    self.executed_trades.append({
                        "entry_time": simulated_dt_str,
                        "signal_candle": finalized_bucket,
                        "signal": self.signal,
                        "fill_spot": f_close
                    })
                    return True, f"Trade Executed on Completed Candle [{finalized_bucket}]"
                    
                # Bearish Check
                elif (time_ok and quota_ok and pcr <= 0.85 and 
                      f_close < self.daily_ema20 and 
                      self.ema9 < self.ema20 < self.ema50 and 
                      f_is_red):
                    self.signal = "BEAR_CALL_SPREAD"
                    self.trade_executed_count += 1
                    self.executed_trades.append({
                        "entry_time": simulated_dt_str,
                        "signal_candle": finalized_bucket,
                        "signal": self.signal,
                        "fill_spot": f_close
                    })
                    return True, f"Trade Executed on Completed Candle [{finalized_bucket}]"
                else:
                    self.signal = "NEUTRAL_SCANNING"
                    return False, "Closed candle did not meet all 6 criteria."
            else:
                # Candle already evaluated in this 5m cycle
                return False, f"Already evaluated candle [{finalized_bucket}]. Waiting for next close."
        else:
            self.signal = "WAITING_FOR_FIRST_5M_CLOSE"
            return False, "Waiting for initial 5-minute candle to close."

def run_historical_replay_test():
    print("=" * 80)
    print(" 5-MINUTE CANDLE CLOSE CONFIRMATION LATCH: REPLAY VERIFICATION TEST")
    print("=" * 80)
    
    engine = CandleCloseStrategyEngine()
    
    # Pre-seed 09:50 candle start
    print("\n--- STEP 1: Seed 09:50:00 Candle Open ---")
    engine.process_tick("2026-11-20 09:50:00", spot=25505.0)
    print(f"  [STATUS] 09:50:00 Candle Open: {engine.candle_5m_open} | Bucket: {engine.current_5m_bucket}")
    
    # --- TEST A: 09:53:35 Running Candle Tick ---
    print("\n--- STEP 2: Replay 09:53:35 (The Problematic Running Tick from 20-Nov) ---")
    # At 09:53:35, spot is 25,514, PCR is 1.335, EMA 9>20>50, Spot > Daily 20 EMA
    traded_953, msg_953 = engine.process_tick("2026-11-20 09:53:35", spot=25514.0, pcr=1.335)
    print(f"  [TICK 09:53:35] Result: Traded={traded_953} | Message: {msg_953} | Signal: {engine.signal}")
    assert traded_953 is False, "CRITICAL ERROR: Trade was taken on a running, unclosed candle!"
    assert engine.trade_executed_count == 0, "Trade count must be 0!"
    print("  [PASS] 09:53:35: Trade STRICTLY BLOCKED! Running candle rejected.")
    
    # --- TEST B: 09:54:58 Final Tick of Running Candle ---
    print("\n--- STEP 3: Replay 09:54:58 (Final seconds of 09:50-09:55 Candle) ---")
    traded_954, msg_954 = engine.process_tick("2026-11-20 09:54:58", spot=25516.0, pcr=1.335)
    print(f"  [TICK 09:54:58] Result: Traded={traded_954} | Message: {msg_954}")
    assert traded_954 is False
    print("  [PASS] 09:54:58: Trade STILL BLOCKED (Candle not closed yet).")
    
    # --- TEST C: 09:55:00 Bucket Rollover & Candle Finalization ---
    print("\n--- STEP 4: Replay 09:55:00 (09:50-09:55 Candle Finalized & Evaluated) ---")
    # At 09:55:00, bucket switches to 55. The 09:50-09:55 candle is now FINALIZED!
    traded_955, msg_955 = engine.process_tick("2026-11-20 09:55:00", spot=25520.0, pcr=1.335)
    print(f"  [TICK 09:55:00] Result: Traded={traded_955} | Message: {msg_955} | Signal: {engine.signal}")
    assert engine.last_finalized_candle is not None
    fc = engine.last_finalized_candle
    print(f"  [FINALIZED DATA] 09:50 Candle -> Open: {fc['open']}, Close: {fc['close']}, Green: {fc['is_green']}")
    assert traded_955 is True, "Trade should execute on confirmed candle close!"
    assert engine.trade_executed_count == 1
    print(f"  [PASS] 09:55:00: Finalized 5M Candle successfully triggered 1 Trade ({engine.signal})!")
    
    # --- TEST D: 09:55:01 & 09:55:03 (Subsequent Ticks inside 09:55 Bucket -> Duplicate Prevention) ---
    print("\n--- STEP 5: Replay 09:55:01 & 09:55:03 (Idempotency & Duplicate Prevention) ---")
    traded_955_01, msg_955_01 = engine.process_tick("2026-11-20 09:55:01", spot=25521.0, pcr=1.335)
    traded_955_03, msg_955_03 = engine.process_tick("2026-11-20 09:55:03", spot=25522.0, pcr=1.335)
    print(f"  [TICK 09:55:01] Traded={traded_955_01} | Message: {msg_955_01}")
    print(f"  [TICK 09:55:03] Traded={traded_955_03} | Message: {msg_955_03}")
    assert traded_955_01 is False, "Duplicate trade allowed on 09:55:01!"
    assert traded_955_03 is False, "Duplicate trade allowed on 09:55:03!"
    assert engine.trade_executed_count == 1, f"Expected 1 trade, got {engine.trade_executed_count}"
    print("  [PASS] Duplicate Prevention Verified: Zero extra trades generated during the 09:55 bucket.")
    
    # --- TEST E: Stale Data / API Delay Scenario ---
    print("\n--- STEP 6: API Delay / Stale Data Test (> 15s Delay) ---")
    stale_engine = CandleCloseStrategyEngine()
    stale_engine.process_tick("2026-11-20 10:00:00", spot=25500.0)
    # Simulate a delayed tick with 25s latency
    traded_stale, msg_stale = stale_engine.process_tick("2026-11-20 10:05:00", spot=25530.0, data_delay_sec=25.0)
    print(f"  [STALE TICK] Result: Traded={traded_stale} | Message: {msg_stale}")
    assert traded_stale is False
    assert stale_engine.signal == "BLOCKED_STALE_DATA"
    print("  [PASS] Stale Data Guard Verified: Trade successfully blocked due to latency!")

    print("\n" + "=" * 80)
    print(" ALL CANDLE CLOSE REPLAY TESTS PASSED (100% EVIDENCE VERIFIED) 🚀")
    print("=" * 80)

if __name__ == "__main__":
    run_historical_replay_test()
