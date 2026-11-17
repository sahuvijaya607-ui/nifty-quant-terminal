"""
Compute Synthetic Options PnL for all 121 Trades from 25-March-2026 to 09-November-2026.
Based on real 1-minute Spot movements, ATM 50Δ / Wing 10Δ net delta, and theta decay.
Lot Size: 65 (SEBI 2026 mandate).
"""

import pandas as pd
import numpy as np

def calculate_pnl():
    df = pd.read_csv("results/march_nov_2026_spot_trades_audit.csv")
    lot_size = 65
    
    # Model parameters
    # Net Delta: Sell ATM (~0.50) - Buy OTM (~0.10) = 0.40 net delta
    # Max SL: 40% of premium or spot SL (~45 pts options premium = ~Rs 2,925 / lot)
    # Target: 50% profit target (~25 pts options premium = +Rs 1,625 / lot)
    # Theta Decay: ~2.5 pts per trading day (~Rs 162.5 / lot)
    
    trades = []
    
    for idx, row in df.iterrows():
        spot_move = row["spot_move"]
        outcome = row["outcome"]
        reason = row["exit_reason"]
        
        # Calculate option points
        if reason == "TARGET":
            opt_pts = 25.0
        elif reason == "SL":
            opt_pts = -45.0
        else: # EOD
            # Delta gain/loss + Theta credit
            raw_pts = spot_move * 0.40
            opt_pts = np.clip(raw_pts + 3.0, -45.0, 25.0)
            
        pnl_rupees = round(opt_pts * lot_size, 2)
        
        trades.append({
            "date": row["date"],
            "day": row["day_name"],
            "direction": row["direction"],
            "entry_time": row["entry_time"],
            "entry_spot": row["entry_spot"],
            "exit_time": row["exit_time"],
            "exit_spot": row["exit_spot"],
            "spot_move": row["spot_move"],
            "exit_reason": reason,
            "outcome": "WIN" if pnl_rupees > 0 else "LOSS",
            "opt_points": round(opt_pts, 2),
            "pnl_inr": pnl_rupees
        })
        
    res_df = pd.DataFrame(trades)
    res_df["cum_pnl"] = res_df["pnl_inr"].cumsum()
    res_df["peak"] = res_df["cum_pnl"].cummax()
    res_df["drawdown"] = res_df["cum_pnl"] - res_df["peak"]
    
    res_df.to_csv("results/march_nov_2026_options_pnl_ledger.csv", index=False)
    
    # Monthly Aggregation
    res_df["month"] = pd.to_datetime(res_df["date"]).dt.strftime("%Y-%m")
    monthly = res_df.groupby("month").agg(
        trades=('date', 'count'),
        wins=('outcome', lambda x: (x == 'WIN').sum()),
        losses=('outcome', lambda x: (x == 'LOSS').sum()),
        net_pnl=('pnl_inr', 'sum'),
        avg_trade=('pnl_inr', 'mean')
    )
    monthly["win_rate"] = (monthly["wins"] / monthly["trades"]) * 100
    
    print("================================================================================")
    print("      MARCH 2026 - NOVEMBER 2026 ESTIMATED OPTIONS PNL (1 LOT = 65 QTY)         ")
    print("================================================================================")
    print(f"Total Trades:       {len(res_df)}")
    print(f"Total Wins:         {len(res_df[res_df['pnl_inr'] > 0])}")
    print(f"Total Losses:       {len(res_df[res_df['pnl_inr'] <= 0])}")
    print(f"Overall Net PnL:    Rs {res_df['pnl_inr'].sum():,.2f}")
    print(f"Max Drawdown:       Rs {res_df['drawdown'].min():,.2f}")
    print("\n--- MONTHLY BREAKDOWN ---")
    for m, r in monthly.iterrows():
        pnl_str = f"+Rs {r['net_pnl']:,.2f}" if r['net_pnl'] >= 0 else f"-Rs {abs(r['net_pnl']):,.2f}"
        print(f"  {m} | Trades: {int(r['trades']):2d} | Wins: {int(r['wins']):2d} | Losses: {int(r['losses']):2d} | WR: {r['win_rate']:5.1f}% | Net PnL: {pnl_str}")

if __name__ == "__main__":
    calculate_pnl()
