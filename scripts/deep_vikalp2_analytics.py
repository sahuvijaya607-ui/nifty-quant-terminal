"""
scripts/deep_vikalp2_analytics.py
Exhaustive statistical analysis of Vikalp 2 (Rs 2,800 Hard SL Cap)
"""

import pandas as pd
import numpy as np

def main():
    df = pd.read_csv('results/vikalp2_full_967_trades_postmortem.csv')

    print("=" * 90)
    print("1. HARD CAPPED VS REGULAR SL TRADES")
    print("=" * 90)
    print(df['is_hard_capped'].value_counts())

    hard_capped = df[df['is_hard_capped'] == True]
    reg_sl = df[df['is_hard_capped'] == False]
    print(f"\nHard Capped Trades (where Rs 2800 cap was tighter than 60%): {len(hard_capped)} ({len(hard_capped)/len(df)*100:.1f}%)")
    print(f"Regular SL Trades (where 60% SL was already tighter than Rs 2800): {len(reg_sl)} ({len(reg_sl)/len(df)*100:.1f}%)")

    print("\nOutcome of Hard Capped Trades:")
    for reason, grp in hard_capped.groupby('exit_reason'):
        print(f"  • {reason:<14}: {len(grp)} ({len(grp)/len(hard_capped)*100:.1f}%) | Total PnL: Rs {grp['net_pnl'].sum():,.2f}")

    print("\n" + "=" * 90)
    print("2. VIX / VOLATILITY REGIMES BREAKDOWN")
    print("=" * 90)
    df['approx_vix'] = df['daily_vol_ann'] * 100
    bins = [0, 13, 17, 22, 28, 100]
    labels = ['VIX < 13 (Ultra Low)', 'VIX 13-17 (Normal)', 'VIX 17-22 (Elevated)', 'VIX 22-28 (High)', 'VIX > 28 (Extreme Panic)']
    df['vix_bracket'] = pd.cut(df['approx_vix'], bins=bins, labels=labels)

    vix_summary = df.groupby('vix_bracket', observed=False).agg(
        trades=('trade_no', 'count'),
        wins=('net_pnl', lambda x: (x > 0).sum()),
        win_rate=('net_pnl', lambda x: (x > 0).mean() * 100),
        net_pnl=('net_pnl', 'sum'),
        avg_pnl=('net_pnl', 'mean'),
        max_loss=('net_pnl', 'min'),
        max_win=('net_pnl', 'max')
    )
    print(vix_summary.to_string())

    print("\n" + "=" * 90)
    print("3. DAY OF WEEK PERFORMANCE (TUE, WED, THU, FRI)")
    print("=" * 90)
    day_summary = df.groupby('day').agg(
        trades=('trade_no', 'count'),
        win_rate=('net_pnl', lambda x: (x > 0).mean() * 100),
        net_pnl=('net_pnl', 'sum'),
        avg_pnl=('net_pnl', 'mean')
    ).reindex(['Tuesday', 'Wednesday', 'Thursday', 'Friday'])
    print(day_summary.to_string())

    print("\n" + "=" * 90)
    print("4. TRADE DURATION IN MINUTES (HOLDING TIME)")
    print("=" * 90)
    dur_bins = [0, 30, 60, 120, 240, 500]
    dur_labels = ['< 30 mins (Fast exit)', '30-60 mins', '1-2 hours', '2-4 hours', '> 4 hours (Full Day)']
    df['dur_bracket'] = pd.cut(df['duration_mins'], bins=dur_bins, labels=dur_labels)
    dur_summary = df.groupby('dur_bracket', observed=False).agg(
        trades=('trade_no', 'count'),
        win_rate=('net_pnl', lambda x: (x > 0).mean() * 100),
        net_pnl=('net_pnl', 'sum'),
        avg_pnl=('net_pnl', 'mean')
    )
    print(dur_summary.to_string())

    print("\n" + "=" * 90)
    print("5. MONTH-BY-MONTH PNL MATRIX (75 MONTHS)")
    print("=" * 90)
    mo_matrix = df.groupby(['year', 'year_month']).agg(
        trades=('trade_no', 'count'),
        wins=('net_pnl', lambda x: (x > 0).sum()),
        win_rate=('net_pnl', lambda x: (x > 0).mean() * 100),
        net_pnl=('net_pnl', 'sum')
    ).reset_index()
    
    # Save pivoted month matrix
    piv = mo_matrix.pivot(index='year', columns='year_month', values='net_pnl').fillna(0)
    print(f"Total Months: {len(mo_matrix)}")
    green_months = (mo_matrix['net_pnl'] > 0).sum()
    red_months = (mo_matrix['net_pnl'] <= 0).sum()
    print(f"Profitable Months: {green_months} / {len(mo_matrix)} ({green_months/len(mo_matrix)*100:.1f}%)")
    print(f"Losing Months: {red_months} / {len(mo_matrix)} ({red_months/len(mo_matrix)*100:.1f}%)")
    print(f"Best Month: {mo_matrix.loc[mo_matrix['net_pnl'].idxmax()]['year_month']} -> Rs {mo_matrix['net_pnl'].max():,.2f}")
    print(f"Worst Month: {mo_matrix.loc[mo_matrix['net_pnl'].idxmin()]['year_month']} -> Rs {mo_matrix['net_pnl'].min():,.2f}")

    # Top 5 best & worst
    print("\nTop 5 Most Profitable Months:")
    for _, r in mo_matrix.sort_values('net_pnl', ascending=False).head(5).iterrows():
        print(f"  • {r['year_month']}: Rs {r['net_pnl']:>9,.2f} ({r['trades']} trades, {r['win_rate']:.1f}% WR)")

    print("\nTop 5 Worst Months:")
    for _, r in mo_matrix.sort_values('net_pnl', ascending=True).head(5).iterrows():
        print(f"  • {r['year_month']}: Rs {r['net_pnl']:>9,.2f} ({r['trades']} trades, {r['win_rate']:.1f}% WR)")

    # Save month matrix
    mo_matrix.to_csv("results/vikalp2_monthly_matrix.csv", index=False)
    print("\nSaved monthly matrix to results/vikalp2_monthly_matrix.csv")

if __name__ == '__main__':
    main()
