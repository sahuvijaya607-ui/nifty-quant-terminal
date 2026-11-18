import pandas as pd
import numpy as np

def audit_daily_ema_impact():
    # Load 6yr spot data and check trades taken when Spot < Daily 20 EMA vs Spot > Daily 20 EMA
    v1 = pd.read_csv('results/audit_original_trades.csv')
    print(f"Total V1 Trades: {len(v1)}")
    
    # Check bull trades vs bear trades in V1
    bulls = v1[v1['direction'] == 'BULL_PUT_SPREAD']
    bears = v1[v1['direction'] == 'BEAR_CALL_SPREAD']
    print(f"Bull Put Trades: {len(bulls)} | PnL: Rs {bulls['net_pnl'].sum():,.2f} | Win Rate: {(bulls['net_pnl']>0).mean()*100:.1f}%")
    print(f"Bear Call Trades: {len(bears)} | PnL: Rs {bears['net_pnl'].sum():,.2f} | Win Rate: {(bears['net_pnl']>0).mean()*100:.1f}%")

if __name__ == '__main__':
    audit_daily_ema_impact()
