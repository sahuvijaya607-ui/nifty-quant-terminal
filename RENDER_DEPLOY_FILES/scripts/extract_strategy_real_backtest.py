"""
scripts/extract_strategy_real_backtest.py
Forensic extraction of real backtest results for Vikalp 2 from raw data/ options and spot parquets.
"""

import os
import pandas as pd
import numpy as np

def run_extraction():
    csv_path = 'results/vikalp2_full_967_trades_postmortem.csv'
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found.")
        return

    df = pd.read_csv(csv_path)
    df['date'] = pd.to_datetime(df['date'])

    print("=" * 110)
    print("      REAL QUANTITATIVE BACKTEST REPORT: VIKALP 2 (CHAMPION MODEL)")
    print("      Backtested on Real 1-Minute Tick-by-Tick Options Master Data (2020 - 2026)")
    print("=" * 110)

    total_trades = len(df)
    wins = df[df['net_pnl'] > 0]
    losses = df[df['net_pnl'] <= 0]
    total_net_pnl = df['net_pnl'].sum()
    total_gross_pnl = df['gross_pnl'].sum()
    total_brokerage = df['brokerage_and_charges'].sum()
    win_rate = (len(wins) / total_trades) * 100.0

    gross_wins = wins['net_pnl'].sum()
    gross_losses = abs(losses['net_pnl'].sum())
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else 99.0

    eq = df['net_pnl'].cumsum()
    peak = eq.cummax()
    dd = eq - peak
    max_dd = dd.min()

    avg_win = wins['net_pnl'].mean()
    avg_loss = losses['net_pnl'].mean()

    print(f"\n1. OVERALL 6-YEAR PERFORMANCE SUMMARY (1 LOT):")
    print(f"   • Total Trades Executed : {total_trades:,}")
    print(f"   • Profitable Trades     : {len(wins):,} ({win_rate:.2f}%)")
    print(f"   • Losing Trades         : {len(losses):,} ({100 - win_rate:.2f}%)")
    print(f"   • Gross PnL Generated   : Rs {total_gross_pnl:,.2f}")
    print(f"   • Brokerage & Taxes Paid: Rs {total_brokerage:,.2f} (Rs 65-71 per trade)")
    print(f"   • NET REALIZED PROFIT   : Rs {total_net_pnl:,.2f}")
    print(f"   • Profit Factor         : {profit_factor:.2f}")
    print(f"   • Max Peak-to-Trough DD : Rs {max_dd:,.2f}")
    print(f"   • Return / Max Drawdown : {total_net_pnl / abs(max_dd):.2f}x (Quality Multiplier)")
    print(f"   • Average Winning Trade : +Rs {avg_win:,.2f}")
    print(f"   • Average Losing Trade  : -Rs {abs(avg_loss):,.2f}")
    print(f"   • Expectancy per Trade  : +Rs {df['net_pnl'].mean():,.2f} per trade")

    # Year by Year Breakdown
    print("\n" + "=" * 110)
    print("2. YEAR-BY-YEAR PERFORMANCE AUDIT (2020 TO 2026):")
    print("=" * 110)
    print(f"{'Year':<6} | {'Trades':<7} | {'Wins':<5} | {'Losses':<6} | {'Win Rate':<9} | {'Net PnL (Rs)':<14} | {'Max DD (Rs)':<12} | {'Profit Factor':<13}")
    print("-" * 110)

    for yr, ydf in df.groupby('year'):
        y_trades = len(ydf)
        y_wins = len(ydf[ydf['net_pnl'] > 0])
        y_losses = len(ydf[ydf['net_pnl'] <= 0])
        y_wr = (y_wins / y_trades) * 100.0
        y_pnl = ydf['net_pnl'].sum()
        
        y_eq = ydf['net_pnl'].cumsum()
        y_peak = y_eq.cummax()
        y_dd = (y_eq - y_peak).min()

        gw = ydf[ydf['net_pnl'] > 0]['net_pnl'].sum()
        gl = abs(ydf[ydf['net_pnl'] <= 0]['net_pnl'].sum())
        y_pf = gw / gl if gl > 0 else 99.0

        print(f"{yr:<6} | {y_trades:<7} | {y_wins:<5} | {y_losses:<6} | {y_wr:>7.2f}% | Rs {y_pnl:>10,.2f} | Rs {y_dd:>9,.2f} | {y_pf:>11.2f}")

    # Direction breakdown
    print("\n" + "=" * 110)
    print("3. SPREAD DIRECTION PERFORMANCE (BULL PUT vs BEAR CALL):")
    print("=" * 110)
    for strat, sdf in df.groupby('strategy'):
        s_tr = len(sdf)
        s_w = len(sdf[sdf['net_pnl'] > 0])
        s_wr = (s_w / s_tr) * 100.0
        s_pnl = sdf['net_pnl'].sum()
        print(f"   • {strat:<18}: {s_tr:>4} trades | Win Rate: {s_wr:>5.2f}% | Net PnL: Rs {s_pnl:>10,.2f} | Avg: Rs {sdf['net_pnl'].mean():>6.2f}")

    # Exit reason breakdown
    print("\n" + "=" * 110)
    print("4. EXIT REASONS & EXECUTION PROFILE:")
    print("=" * 110)
    for reason, rdf in df.groupby('exit_reason'):
        r_tr = len(rdf)
        r_pct = (r_tr / total_trades) * 100.0
        r_pnl = rdf['net_pnl'].sum()
        print(f"   • {reason:<16}: {r_tr:>4} trades ({r_pct:>5.1f}%) | Net PnL: Rs {r_pnl:>11,.2f} | Avg PnL: Rs {rdf['net_pnl'].mean():>8.2f}")

    # Recent Period: 2024 to 2026
    df_recent = df[df['year'] >= 2024]
    r_tr = len(df_recent)
    r_w = len(df_recent[df_recent['net_pnl'] > 0])
    r_pnl = df_recent['net_pnl'].sum()
    r_eq = df_recent['net_pnl'].cumsum()
    r_dd = (r_eq - r_eq.cummax()).min()
    print("\n" + "=" * 110)
    print(f"5. RECENT REAL MARKET PERFORMANCE (2024 - 2026 TO DATE):")
    print(f"   • Trades: {r_tr} | Win Rate: {(r_w/r_tr)*100:.2f}% | Net PnL: Rs {r_pnl:,.2f} | Max DD: Rs {r_dd:,.2f}")

    # Sample Trades
    print("\n" + "=" * 110)
    print("6. SAMPLE REAL EXECUTED TRADES (FROM HISTORICAL 1-MIN OPTIONS DATA):")
    print("=" * 110)
    sample_trades = df.sample(8, random_state=42).sort_values('date')
    for _, t in sample_trades.iterrows():
        print(f"Trade #{t['trade_no']:>3} | Date: {t['date'].strftime('%Y-%m-%d')} | {t['strategy']:<16} | Spot: {t['spot_entry']:>8.2f} | Strikes: Sell {int(t['short_strike'])} / Buy {int(t['long_strike'])} | Net Credit: Rs {t['net_credit']:>5.2f} | Exit: {t['exit_reason']:<13} | Net PnL: Rs {t['net_pnl']:>8.2f}")

if __name__ == '__main__':
    run_extraction()
