"""
Verify Champion Model 4 Signals & Spot Performance from 25-March-2026 to 09-November-2026.
Using the 154 trading days of clean 1-minute NIFTY spot data already on disk.
"""

import pandas as pd
import numpy as np

def run_spot_verification():
    # 1. Load 5-Year Spot
    df_spot = pd.read_parquet("data/nifty_1min_5yr.parquet")
    df_spot = df_spot.sort_index()

    # Slicing from 2026-03-01 to ensure EMA warm-up, but trade evaluation from 2026-03-25
    df_eval = df_spot.loc["2026-03-01":"2026-11-09"].copy()

    # Daily EMA 20
    df_daily = df_eval.resample("D").agg({
        "open": "first", "high": "max", "low": "min", "close": "last"
    }).dropna()
    df_daily["ema20_d"] = df_daily["close"].ewm(span=20, adjust=False).mean()

    # 5-min Resampling
    df_5m = df_eval.resample("5min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last"
    }).dropna()
    df_5m["ema9"] = df_5m["close"].ewm(span=9, adjust=False).mean()
    df_5m["ema20"] = df_5m["close"].ewm(span=20, adjust=False).mean()
    df_5m["ema50"] = df_5m["close"].ewm(span=50, adjust=False).mean()

    # Filter to intraday hours 09:15 to 15:30
    df_5m = df_5m.between_time("09:15", "15:25")

    # Map Daily EMA to 5m
    daily_map = df_daily["ema20_d"].to_dict()
    df_5m["date_str"] = df_5m.index.strftime("%Y-%m-%d")
    df_5m["daily_ema20"] = df_5m.index.normalize().map(daily_map)

    # Strategy Parameters (Champion Model 4)
    # Entry after 09:30, Exit by 15:15
    # Max 1 loss per day circuit breaker
    # Trend alignment:
    # BULL: Daily Close > Daily EMA20 AND 5m EMA9 > EMA20 > EMA50
    # BEAR: Daily Close < Daily EMA20 AND 5m EMA9 < EMA20 < EMA50

    trades = []
    
    # Group by trading date
    test_dates = [d for d in sorted(df_5m["date_str"].unique()) if d >= "2026-03-25"]
    
    print(f"Total Trading Days in Period (25-Mar-2026 to 09-Nov-2026): {len(test_dates)}")

    for d in test_dates:
        day_bars = df_5m[df_5m["date_str"] == d].copy()
        if len(day_bars) < 10:
            continue
            
        day_losses = 0
        in_trade = False
        pos_type = None
        entry_time = None
        entry_spot = 0.0
        sl_spot = 0.0
        target_spot = 0.0
        
        # We trade Monday to Thursday (Champion Model 4: 4-Day Engine)
        weekday = pd.to_datetime(d).weekday() # 0: Mon, 1: Tue, 2: Wed, 3: Thu, 4: Fri
        if weekday == 4: # Friday skipped in 4-day engine
            continue

        for ts, row in day_bars.iterrows():
            curr_time = ts.time()
            spot_close = row["close"]

            # Exit check if in trade
            if in_trade:
                hit_sl = False
                hit_target = False
                hit_eod = (curr_time >= pd.to_datetime("15:15").time())

                if pos_type == "BULL_PUT": # Bullish: spot going down hurts us
                    if spot_close <= sl_spot:
                        hit_sl = True
                    elif spot_close >= target_spot:
                        hit_target = True
                elif pos_type == "BEAR_CALL": # Bearish: spot going up hurts us
                    if spot_close >= sl_spot:
                        hit_sl = True
                    elif spot_close <= target_spot:
                        hit_target = True

                if hit_sl or hit_target or hit_eod:
                    exit_time = ts
                    exit_spot = spot_close
                    pnl_points = (exit_spot - entry_spot) if pos_type == "BULL_PUT" else (entry_spot - exit_spot)
                    
                    # Estimated Spread PnL (ATM delta ~0.50 - Wing delta ~0.10 = Net Delta ~0.40)
                    # + Theta decay component (~₹15-20/lot/hr)
                    status = "WIN" if (hit_target or (hit_eod and pnl_points >= 0)) else "LOSS"
                    if status == "LOSS":
                        day_losses += 1

                    trades.append({
                        "date": d,
                        "day_name": ts.strftime("%a"),
                        "direction": pos_type,
                        "entry_time": entry_time.strftime("%H:%M"),
                        "entry_spot": round(entry_spot, 2),
                        "exit_time": exit_time.strftime("%H:%M"),
                        "exit_spot": round(exit_spot, 2),
                        "spot_move": round(pnl_points, 2),
                        "outcome": status,
                        "exit_reason": "SL" if hit_sl else ("TARGET" if hit_target else "EOD")
                    })
                    
                    in_trade = False
                    pos_type = None

            # Entry check
            if not in_trade and day_losses < 1:
                if pd.to_datetime("09:30").time() <= curr_time <= pd.to_datetime("14:30").time():
                    bullish = (row["close"] > row["daily_ema20"]) and (row["ema9"] > row["ema20"] > row["ema50"])
                    bearish = (row["close"] < row["daily_ema20"]) and (row["ema9"] < row["ema20"] < row["ema50"])
                    
                    if bullish:
                        in_trade = True
                        pos_type = "BULL_PUT"
                        entry_time = ts
                        entry_spot = spot_close
                        # 0.5% spot SL, 0.6% target
                        sl_spot = entry_spot * 0.995
                        target_spot = entry_spot * 1.006
                    elif bearish:
                        in_trade = True
                        pos_type = "BEAR_CALL"
                        entry_time = ts
                        entry_spot = spot_close
                        sl_spot = entry_spot * 1.005
                        target_spot = entry_spot * 0.994

    df_trades = pd.DataFrame(trades)
    df_trades.to_csv("results/march_nov_2026_spot_trades_audit.csv", index=False)
    
    print("\n================================================================================")
    print("      MARCH 2026 - NOVEMBER 2026 SPOT VERIFICATION OF CHAMPION MODEL 4          ")
    print("================================================================================")
    print(f"Total Trades Generated: {len(df_trades)}")
    if len(df_trades) > 0:
        wins = len(df_trades[df_trades["outcome"] == "WIN"])
        losses = len(df_trades[df_trades["outcome"] == "LOSS"])
        win_rate = (wins / len(df_trades)) * 100
        print(f"Winning Trades: {wins}")
        print(f"Losing Trades:  {losses}")
        print(f"Win Rate:       {win_rate:.2f}%")
        
        # Monthly breakdown
        df_trades["month"] = pd.to_datetime(df_trades["date"]).dt.strftime("%Y-%m")
        monthly = df_trades.groupby("month").agg(
            total_trades=('date', 'count'),
            wins=('outcome', lambda x: (x == 'WIN').sum()),
            losses=('outcome', lambda x: (x == 'LOSS').sum()),
            avg_spot_move=('spot_move', 'mean')
        )
        monthly['win_rate'] = (monthly['wins'] / monthly['total_trades']) * 100
        print("\n--- MONTHLY BREAKDOWN (APRIL TO NOVEMBER 2026) ---")
        print(monthly.to_string())

if __name__ == "__main__":
    run_spot_verification()
