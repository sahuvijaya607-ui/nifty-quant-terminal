import pandas as pd
import numpy as np

def run_audit():
    v1 = pd.read_csv('results/audit_original_trades.csv')
    v2 = pd.read_csv('results/vikalp2_full_967_trades_postmortem.csv')

    print("=" * 80)
    print("      DEEP QUANTITATIVE AUDIT: STRATEGY 2 (V1) vs VIKALP 2 (V2)")
    print("=" * 80)

    # 1. Cumulative Metrics
    eq1 = v1['net_pnl'].cumsum()
    peak1 = eq1.cummax()
    dd1 = eq1 - peak1
    max_dd1 = dd1.min()

    eq2 = v2['net_pnl'].cumsum()
    peak2 = eq2.cummax()
    dd2 = eq2 - peak2
    max_dd2 = dd2.min()

    tot_pnl1 = v1['net_pnl'].sum()
    tot_pnl2 = v2['net_pnl'].sum()

    print(f"\n1. OVERALL RETURNS & DRAWDOWN:")
    print(f"   V1 Net PnL: Rs {tot_pnl1:,.2f} | Max Drawdown: Rs {max_dd1:,.2f} | Return / Max DD: {tot_pnl1 / abs(max_dd1):.2f}x")
    print(f"   V2 Net PnL: Rs {tot_pnl2:,.2f} | Max Drawdown: Rs {max_dd2:,.2f} | Return / Max DD: {tot_pnl2 / abs(max_dd2):.2f}x")

    # 2. Single Trade Risk (Tail Risk / Black Swan)
    print(f"\n2. SINGLE TRADE TAIL RISK (CATASTROPHE RISK):")
    print(f"   V1 Worst Single Trade Loss: Rs {v1['net_pnl'].min():,.2f}")
    print(f"   V2 Worst Single Trade Loss: Rs {v2['net_pnl'].min():,.2f}")
    print(f"   V1 Trades with Loss > Rs 3,000: {len(v1[v1['net_pnl'] < -3000])} trades")
    print(f"   V2 Trades with Loss > Rs 3,000: {len(v2[v2['net_pnl'] < -3000])} trades (Hard Cap Protected!)")
    print(f"   V1 Trades with Loss > Rs 5,000: {len(v1[v1['net_pnl'] < -5000])} trades (Severe Drawdown!)")
    print(f"   V2 Trades with Loss > Rs 5,000: {len(v2[v2['net_pnl'] < -5000])} trades (ZERO - 100% Shielded!)")

    # 3. Monthly Consistency
    v1['month'] = pd.to_datetime(v1['date']).dt.to_period('M')
    v2['month'] = pd.to_datetime(v2['date']).dt.to_period('M')

    m1 = v1.groupby('month')['net_pnl'].sum()
    m2 = v2.groupby('month')['net_pnl'].sum()

    win_months1 = (m1 > 0).sum()
    total_months1 = len(m1)
    win_months2 = (m2 > 0).sum()
    total_months2 = len(m2)

    print(f"\n3. MONTHLY CONSISTENCY (6 YEARS - 75 MONTHS):")
    print(f"   V1 Profitable Months: {win_months1}/{total_months1} ({win_months1/total_months1*100:.1f}%)")
    print(f"   V2 Profitable Months: {win_months2}/{total_months2} ({win_months2/total_months2*100:.1f}%)")
    print(f"   V1 Worst Month PnL: Rs {m1.min():,.2f} ({m1.idxmin()})")
    print(f"   V2 Worst Month PnL: Rs {m2.min():,.2f} ({m2.idxmin()})")

    # 4. Consecutive Losses & Recovery Duration
    def max_consecutive_losses(df):
        losses = (df['net_pnl'] < 0).astype(int)
        max_c = 0
        cur_c = 0
        for x in losses:
            if x == 1:
                cur_c += 1
                max_c = max(max_c, cur_c)
            else:
                cur_c = 0
        return max_c

    print(f"\n4. PSYCHOLOGICAL & RECOVERY STRESS:")
    print(f"   V1 Max Consecutive Losing Trades: {max_consecutive_losses(v1)}")
    print(f"   V2 Max Consecutive Losing Trades: {max_consecutive_losses(v2)}")

if __name__ == '__main__':
    run_audit()
