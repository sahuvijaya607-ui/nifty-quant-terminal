"""
scripts/print_monthly_breakdown.py
Outputs the clean month-by-month PnL for Vikalp 2 from 2020 to 2026.
"""

import pandas as pd

def main():
    df = pd.read_csv('results/vikalp2_monthly_matrix.csv')
    
    total_months = len(df)
    green_months = len(df[df['net_pnl'] > 0])
    red_months = len(df[df['net_pnl'] < 0])
    
    print("=" * 95)
    print("       VIKALP 2: EXHAUSTIVE MONTH-BY-MONTH REAL PnL BREAKDOWN (2020 - 2026)")
    print(f"       75 Total Months Tested: {green_months} Profitable ({green_months/total_months*100:.1f}%) | {red_months} Red Months ({red_months/total_months*100:.1f}%)")
    print("=" * 95)

    for yr, ydf in df.groupby('year'):
        y_trades = ydf['trades'].sum()
        y_wins = ydf['wins'].sum()
        y_pnl = ydf['net_pnl'].sum()
        y_wr = (y_wins / y_trades * 100) if y_trades > 0 else 0
        y_sign = '+' if y_pnl >= 0 else ''
        
        print(f"\n==================== YEAR {yr} | Net: {y_sign}Rs {y_pnl:,.2f} | Trades: {y_trades} (Win Rate: {y_wr:.1f}%) ====================")
        print(f"{'Month':<10} | {'Trades':<7} | {'Wins':<5} | {'Losses':<6} | {'Win %':<8} | {'Net PnL (Rs)':<14} | {'Status':<10}")
        print("-" * 75)
        
        for _, r in ydf.iterrows():
            pnl = float(r['net_pnl'])
            tr = int(r['trades'])
            w = int(r['wins'])
            l = tr - w
            wr = float(r['win_rate'])
            sign = '+' if pnl >= 0 else ''
            status = '🟢 GREEN' if pnl > 0 else ('🔴 RED' if pnl < 0 else '⚪ FLAT')
            print(f"{r['year_month']:<10} | {tr:<7} | {w:<5} | {l:<6} | {wr:>6.1f}% | {sign}Rs {pnl:>10,.2f} | {status}")

if __name__ == '__main__':
    main()
