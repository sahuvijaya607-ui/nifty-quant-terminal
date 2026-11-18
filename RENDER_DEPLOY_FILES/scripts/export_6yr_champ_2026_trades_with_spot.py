"""
scripts/export_6yr_champ_2026_trades_with_spot.py
Enriches all 2026 trades from the 6-Year Champion Model 4 (and Model 1) backtest with:
- NIFTY Spot Entry Price
- NIFTY Spot Exit Price
- Spot Points Moved
- Strategy Type (Bull Put Spread / Bear Call Spread)
- Cumulative Net PnL & Equity
"""

import os
import sys
import pandas as pd
import numpy as np

# Load spot data
df_spot = pd.read_parquet("data/nifty_spot_master_2020_2026_1min.parquet")
spot_close_map = dict(zip(df_spot.index, df_spot['close']))

df_m4 = pd.read_csv("results/monthly_champ_trades_Model_4_4Day_ATM_Trend_CircuitBreaker.csv")
df_26 = df_m4[df_m4['year'] == 2026].copy().reset_index(drop=True)

enriched_records = []
cum_pnl = 0.0

for idx, r in df_26.iterrows():
    entry_ts = pd.Timestamp(f"{r['date']} {r['entry_time']}:00")
    exit_ts = pd.Timestamp(f"{r['date']} {r['exit_time']}:00")
    
    spot_entry = spot_close_map.get(entry_ts, np.nan)
    spot_exit = spot_close_map.get(exit_ts, np.nan)
    spot_diff = round(spot_exit - spot_entry, 2) if (spot_entry and spot_exit) else np.nan
    
    # Strategy type: If short strike > long strike -> Bull Put Spread (or check which strike is which)
    # In ATM spread: For Bull Put Spread: short_strike > long_strike. For Bear Call Spread: short_strike < long_strike.
    if r['short_strike'] > r['long_strike']:
        strat_type = "BULL_PUT_SPREAD"
        strikes_str = f"Sell {int(r['short_strike'])} PE / Buy {int(r['long_strike'])} PE"
    else:
        strat_type = "BEAR_CALL_SPREAD"
        strikes_str = f"Sell {int(r['short_strike'])} CE / Buy {int(r['long_strike'])} CE"

    net_pnl = round(r['net_pnl'], 2)
    cum_pnl += net_pnl
    
    enriched_records.append({
        'trade_no': idx + 1,
        'year_month': r['year_month'],
        'date': r['date'],
        'day': r['day'],
        'entry_time': r['entry_time'],
        'spot_entry': spot_entry,
        'strategy_type': strat_type,
        'strikes': strikes_str,
        'net_credit': r['net_credit'],
        'exit_time': r['exit_time'],
        'spot_exit': spot_exit,
        'spot_move': spot_diff,
        'exit_spread': r['exit_spread'],
        'lot_size': r['lot_size'],
        'net_pnl': net_pnl,
        'exit_reason': r['exit_reason'],
        'cum_pnl': round(cum_pnl, 2)
    })

df_enriched = pd.DataFrame(enriched_records)
df_enriched.to_csv("results/champ_6yr_2026_trades_detailed.csv", index=False)
print("Exported results/champ_6yr_2026_trades_detailed.csv with", len(df_enriched), "trades.")
print("\nMonth breakdown:")
print(df_enriched.groupby('year_month')['net_pnl'].agg(['count', 'sum', lambda s: (s > 0).sum()]).rename(columns={'<lambda_0>': 'wins'}))
