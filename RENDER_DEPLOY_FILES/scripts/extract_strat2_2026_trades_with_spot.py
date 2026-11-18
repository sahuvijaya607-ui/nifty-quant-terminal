"""
scripts/extract_strat2_2026_trades_with_spot.py
Extracts every trade of Strategy 2 for Jan 2026 to Mar 2026,
enriches with NIFTY Spot Price at Entry and NIFTY Spot Price at Exit from real 1-minute spot parquet vault.
"""

import os
import sys
import pandas as pd
import numpy as np

def extract_trades():
    trades_path = "results/trades_Strat_2_Pure_ATM_D50_L10.csv"
    spot_path = "data/nifty_spot_master_2020_2026_1min.parquet"

    df_trades = pd.read_csv(trades_path)
    df_spot = pd.read_parquet(spot_path)
    spot_map = dict(zip(df_spot.index, df_spot['close']))

    # Filter for Jan 2026 to Mar 2026
    df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'])
    df_trades['exit_dt'] = pd.to_datetime(df_trades['exit_time'])
    df_trades['date_parsed'] = pd.to_datetime(df_trades['date'])

    mask = (df_trades['date_parsed'] >= '2026-01-01') & (df_trades['date_parsed'] <= '2026-03-31')
    sub_df = df_trades[mask].copy().reset_index(drop=True)

    print(f"Found {len(sub_df)} trades in Jan 2026 - Mar 2026 for Strategy 2.")

    enriched_records = []
    for idx, row in sub_df.iterrows():
        e_time = row['entry_dt']
        x_time = row['exit_dt']
        
        # Spot at entry
        spot_entry = spot_map.get(e_time, None)
        if spot_entry is None:
            # Nearest
            nearest_idx = df_spot.index.get_indexer([e_time], method='nearest')[0]
            spot_entry = df_spot.iloc[nearest_idx]['close']
            
        # Spot at exit
        spot_exit = spot_map.get(x_time, None)
        if spot_exit is None:
            nearest_idx = df_spot.index.get_indexer([x_time], method='nearest')[0]
            spot_exit = df_spot.iloc[nearest_idx]['close']

        day_name = row['date_parsed'].day_name()

        enriched_records.append({
            'trade_no': idx + 1,
            'date': str(row['date_parsed'].date()),
            'day': day_name,
            'entry_time': str(e_time.time())[:5],
            'spot_entry': round(float(spot_entry), 2),
            'strategy_type': row['type'],
            'short_strike': int(row['short_strike']),
            'long_strike': int(row['long_strike']),
            'entry_credit': round(float(row['entry_credit']), 2),
            'exit_time': str(x_time.time())[:5],
            'spot_exit': round(float(spot_exit), 2),
            'spot_change_pts': round(float(spot_exit - spot_entry), 2),
            'exit_spread': round(float(row['exit_spread']), 2),
            'lot_size': int(row['lot_size']),
            'gross_pnl': round(float(row['gross_pnl']), 2),
            'slippage': round(float(row['slippage']), 2),
            'net_pnl': round(float(row['net_pnl']), 2),
            'exit_reason': row['exit_reason'],
            'equity': round(float(row['equity']), 2)
        })

    res_df = pd.DataFrame(enriched_records)
    out_csv = "results/strat2_jan_mar_2026_detailed_trades.csv"
    res_df.to_csv(out_csv, index=False)
    print(f"Saved enriched trades to {out_csv}")

    # Summary
    print("\nSummary for Jan 2026 - Mar 2026 (Strategy 2):")
    total_net = res_df['net_pnl'].sum()
    wins = (res_df['net_pnl'] > 0).sum()
    losses = (res_df['net_pnl'] <= 0).sum()
    print(f"Total Trades: {len(res_df)}")
    print(f"Wins: {wins} | Losses: {losses} | Win Rate: {wins/len(res_df)*100:.1f}%")
    print(f"Total Net PnL: Rs. {total_net:+,.2f}")
    print(res_df[['trade_no', 'date', 'day', 'entry_time', 'spot_entry', 'strategy_type', 'short_strike', 'spot_exit', 'net_pnl', 'exit_reason']].to_string(index=False))

if __name__ == '__main__':
    extract_trades()
