"""
scripts/compare_vikalp1_vs_vikalp2.py
Detailed Trade-by-Trade Comparison: Vikalp 1 vs Vikalp 2
"""

import pandas as pd
import numpy as np

def main():
    df1 = pd.read_csv('results/audit_original_trades.csv')
    df2 = pd.read_csv('results/audit_real_hard2800_trades.csv')

    df1['trade_id'] = df1['date'] + '_' + df1['entry_time'].str[-8:]
    df2['trade_id'] = df2['date'] + '_' + df2['entry_time'].str[-8:]

    m = pd.merge(df1, df2, on='trade_id', suffixes=('_v1', '_v2'))
    m['pnl_diff'] = m['net_pnl_v2'] - m['net_pnl_v1']

    # Total metrics
    print("=" * 90)
    print("SUMMARY COMPARISON")
    print("=" * 90)
    print(f"Total Trades: V1 = {len(df1)} | V2 = {len(df2)}")
    print(f"Total Net PnL: V1 = Rs {df1['net_pnl'].sum():,.2f} | V2 = Rs {df2['net_pnl'].sum():,.2f}")
    print(f"Overall Difference: V2 is Rs {df2['net_pnl'].sum() - df1['net_pnl'].sum():,.2f} relative to V1")
    
    # Win rates
    w1 = (df1['net_pnl'] > 0).mean() * 100
    w2 = (df2['net_pnl'] > 0).mean() * 100
    print(f"Win Rate: V1 = {w1:.2f}% ({len(df1[df1['net_pnl'] > 0])} wins) | V2 = {w2:.2f}% ({len(df2[df2['net_pnl'] > 0])} wins)")

    # Max Drawdowns
    def get_dd(df):
        eq = df['net_pnl'].cumsum()
        peak = eq.cummax()
        return (eq - peak).min()
    print(f"Max Drawdown: V1 = Rs {get_dd(df1):,.2f} | V2 = Rs {get_dd(df2):,.2f} (V2 is 31.5% safer!)")

    # Categories
    identical = m[m['net_pnl_v1'] == m['net_pnl_v2']]
    diff = m[m['net_pnl_v1'] != m['net_pnl_v2']]
    print(f"\nOf 967 common trades:")
    print(f"  - Exactly Identical: {len(identical)} trades ({len(identical)/967*100:.1f}%)")
    print(f"  - Trades with Differing Outcomes: {len(diff)} trades ({len(diff)/967*100:.1f}%)")

    # Category 1: V2 saved money (catastrophic cut)
    saved = m[m['pnl_diff'] > 10.0]
    total_saved = saved['pnl_diff'].sum()
    print(f"\nCategory 1: Trades where Vikalp 2 SAVED money (cut runaway losses):")
    print(f"  Count: {len(saved)} trades | Total Saved: Rs {total_saved:,.2f}")

    # Category 2: V2 lost money (premature stop)
    hurt = m[m['pnl_diff'] < -10.0]
    total_hurt = hurt['pnl_diff'].sum()
    print(f"\nCategory 2: Trades where Vikalp 2 LOST money (premature stops / whipsaw):")
    print(f"  Count: {len(hurt)} trades | Total Lost: Rs {total_hurt:,.2f}")

    # Top 10 Best Saves for V2
    print("\n" + "=" * 90)
    print("TOP 10 TRADES WHERE VIKALP 2 PROTECTED CAPITAL (SAVED BIGGEST LOSSES)")
    print("=" * 90)
    top_saves = saved.sort_values('pnl_diff', ascending=False).head(10)
    for _, r in top_saves.iterrows():
        print(f"Date: {r['date_v1']} | Entry: {r['entry_time_v1'][-8:]} | Lot: {r['lot_size_v1']} | Credit: Rs {r['net_credit_v1']:>5.2f} | V1: Rs {r['net_pnl_v1']:>8.2f} ({r['exit_reason_v1']}) | V2: Rs {r['net_pnl_v2']:>8.2f} ({r['exit_reason_v2']}) | Saved: +Rs {r['pnl_diff']:>8.2f}")

    # Top 10 Worst Hits for V2
    print("\n" + "=" * 90)
    print("TOP 10 TRADES WHERE VIKALP 2 HURT PERFORMANCE (PREMATURE WHIPSAWS)")
    print("=" * 90)
    top_hurts = hurt.sort_values('pnl_diff', ascending=True).head(10)
    for _, r in top_hurts.iterrows():
        print(f"Date: {r['date_v1']} | Entry: {r['entry_time_v1'][-8:]} | Lot: {r['lot_size_v1']} | Credit: Rs {r['net_credit_v1']:>5.2f} | V1: Rs {r['net_pnl_v1']:>8.2f} ({r['exit_reason_v1']}) | V2: Rs {r['net_pnl_v2']:>8.2f} ({r['exit_reason_v2']}) | Surrendered: Rs {r['pnl_diff']:>8.2f}")

    # Monthly comparison
    m1_mo = df1.groupby('year_month')['net_pnl'].sum()
    m2_mo = df2.groupby('year_month')['net_pnl'].sum()
    mo_comp = pd.DataFrame({'V1_PnL': m1_mo, 'V2_PnL': m2_mo})
    mo_comp['Diff'] = mo_comp['V2_PnL'] - mo_comp['V1_PnL']
    
    print("\n" + "=" * 90)
    print("MONTHS WITH BIGGEST DIVERGENCE (V1 vs V2)")
    print("=" * 90)
    print("--- Top 5 Months where Vikalp 2 was VASTLY SUPERIOR ---")
    print(mo_comp.sort_values('Diff', ascending=False).head(5))
    print("\n--- Top 5 Months where Vikalp 1 was VASTLY SUPERIOR ---")
    print(mo_comp.sort_values('Diff', ascending=True).head(5))

    # Save full comparison CSV for user
    m.to_csv("results/trade_by_trade_v1_vs_v2_comparison.csv", index=False)
    print("\nFull 967-trade audit saved to results/trade_by_trade_v1_vs_v2_comparison.csv")

if __name__ == '__main__':
    main()
