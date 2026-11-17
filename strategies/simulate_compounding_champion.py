"""
strategies/simulate_compounding_champion.py
Simulates exact trade-by-trade compounding for Champion Model 4 and Model 1 across 2020-2026:
Starting Capital: Rs. 2,00,000.
Models tested:
1. Fixed 1 Lot (Non-Compounded Benchmark)
2. Conservative Compounding: 1 Lot per Rs. 80,000 Equity (Cap at 20 Lots)
3. Balanced Compounding: 1 Lot per Rs. 60,000 Equity (Cap at 20 Lots)
4. Aggressive Compounding: 1 Lot per Rs. 50,000 Equity (Cap at 25 Lots)

Tracks exact equity curve, peak capital, max drawdown %, CAGR %, year-by-year ending capital.
"""

import os
import sys
import pandas as pd
import numpy as np

def simulate_compounding(csv_path, initial_capital=200000.0, capital_per_lot=60000.0, max_lots=20):
    df = pd.read_csv(csv_path)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(['date', 'entry_time']).reset_index(drop=True)

    equity = initial_capital
    equity_curve = [equity]
    trade_records = []

    for idx, row in df.iterrows():
        # Determine lot count based on current equity at start of trade
        lots = max(int(equity // capital_per_lot), 1)
        if max_lots > 0:
            lots = min(lots, max_lots)

        # 1-lot net PnL from historical trade
        pnl_1lot = row['net_pnl']
        
        # Scale by number of lots
        # (Slippage scales linearly with lots)
        trade_pnl = pnl_1lot * lots
        equity += trade_pnl
        equity_curve.append(equity)

        trade_records.append({
            'date': row['date'],
            'year': row['date'].year,
            'year_month': row['year_month'],
            'lots': lots,
            'pnl_1lot': pnl_1lot,
            'trade_pnl': trade_pnl,
            'equity': equity
        })

    df_res = pd.DataFrame(trade_records)
    
    # Calculate drawdown
    df_res['peak_equity'] = df_res['equity'].cummax()
    df_res['drawdown_rs'] = df_res['equity'] - df_res['peak_equity']
    df_res['drawdown_pct'] = (df_res['drawdown_rs'] / df_res['peak_equity']) * 100.0
    
    max_dd_rs = df_res['drawdown_rs'].min()
    max_dd_pct = df_res['drawdown_pct'].min()

    # Annual stats
    years = sorted(df_res['year'].unique())
    year_stats = {}
    prev_eq = initial_capital
    for yr in years:
        yr_df = df_res[df_res['year'] == yr]
        end_eq = yr_df['equity'].iloc[-1]
        yr_pnl = end_eq - prev_eq
        yr_roi = (yr_pnl / prev_eq) * 100.0
        yr_lots = yr_df['lots'].max()
        year_stats[yr] = {
            'end_equity': round(end_eq, 2),
            'year_pnl': round(yr_pnl, 2),
            'year_roi_pct': round(yr_roi, 2),
            'max_lots': yr_lots
        }
        prev_eq = end_eq

    # 6-Year CAGR
    total_days = (df_res['date'].iloc[-1] - df_res['date'].iloc[0]).days
    total_years = total_days / 365.25
    cagr = ((equity / initial_capital) ** (1.0 / total_years) - 1.0) * 100.0

    return {
        'initial_capital': initial_capital,
        'final_equity': round(equity, 2),
        'total_profit': round(equity - initial_capital, 2),
        'roi_pct': round((equity - initial_capital) / initial_capital * 100.0, 2),
        'cagr_pct': round(cagr, 2),
        'max_dd_rs': round(max_dd_rs, 2),
        'max_dd_pct': round(max_dd_pct, 2),
        'final_lots': lots,
        'year_stats': year_stats,
        'trade_df': df_res
    }

print("=" * 100)
print("   COMPOUNDING SIMULATION ACROSS 6 YEARS (2020 TO 2026 - MODEL 4)   ")
print("=" * 100)

csv_m4 = 'results/monthly_champ_trades_Model_4_4Day_ATM_Trend_CircuitBreaker.csv'

# 1. Non-compounded fixed 1 lot
res_fixed = simulate_compounding(csv_m4, initial_capital=200000.0, capital_per_lot=999999999.0, max_lots=1)

# 2. Conservative Compounding (1 lot per 80k)
res_cons = simulate_compounding(csv_m4, initial_capital=200000.0, capital_per_lot=80000.0, max_lots=20)

# 3. Balanced Compounding (1 lot per 60k)
res_bal = simulate_compounding(csv_m4, initial_capital=200000.0, capital_per_lot=60000.0, max_lots=20)

# 4. Aggressive Compounding (1 lot per 50k)
res_agg = simulate_compounding(csv_m4, initial_capital=200000.0, capital_per_lot=50000.0, max_lots=25)

print("\n--- COMPARISON OF COMPOUNDING MODES (Starting Capital: Rs. 2,00,000) ---")
modes = [
    ('Fixed 1 Lot (No Compounding)', res_fixed),
    ('Conservative (1 Lot / 80k)', res_cons),
    ('Balanced Quant (1 Lot / 60k)', res_bal),
    ('Aggressive (1 Lot / 50k)', res_agg),
]

summary_rows = []
for name, r in modes:
    summary_rows.append({
        'Mode': name,
        'Starting_Cap': f"Rs. {r['initial_capital']:,.0f}",
        'Final_Capital': f"Rs. {r['final_equity']:,.0f}",
        'Total_Net_Profit': f"Rs. {r['total_profit']:,.0f}",
        'Total_ROI_%': f"{r['roi_pct']:.1f}%",
        'CAGR_%': f"{r['cagr_pct']:.2f}%",
        'Max_Drawdown_%': f"{r['max_dd_pct']:.2f}%",
        'Max_Drawdown_Rs': f"Rs. {r['max_dd_rs']:,.0f}",
        'Final_Lot_Size': f"{r['final_lots']} Lots"
    })

df_sum = pd.DataFrame(summary_rows)
print(df_sum.to_string(index=False))

print("\n--- BALANCED QUANT COMPOUNDING (1 LOT / 60K) YEAR-BY-YEAR GROWTH ---")
bal_yrs = res_bal['year_stats']
for y, s in bal_yrs.items():
    print(f"Year {y}: Ending Equity = Rs. {s['end_equity']:10,.2f} | Profit = +Rs. {s['year_pnl']:9,.2f} | ROI = +{s['year_roi_pct']:5.1f}% | Lots Traded: {s['max_lots']} Lots")

# Save detailed compounding ledgers
res_bal['trade_df'].to_csv("results/compounding_balanced_60k_trades.csv", index=False)
res_cons['trade_df'].to_csv("results/compounding_conservative_80k_trades.csv", index=False)
res_agg['trade_df'].to_csv("results/compounding_aggressive_50k_trades.csv", index=False)
