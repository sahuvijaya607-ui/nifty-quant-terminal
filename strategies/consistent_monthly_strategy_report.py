import os
import sys
import pickle
import time
from pathlib import Path
import pandas as pd
import numpy as np

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.nifty_lot_size import get_nifty_lot_size

def generate_monthly_consistency_master():
    t_start = time.time()
    
    print("=" * 115)
    print("      QUANTITATIVE AUDIT: HOW TO ACHIEVE CONSISTENT PROFITABILITY EVERY MONTH (2020 - 2026)      ")
    print("=" * 115)
    print("Starting Capital:         Rs. 2,00,000.00")
    print("Position Sizing:          1 Lot (Exact SEBI Historical Transitions: 75 -> 50 -> 25 -> 75 -> 65)")
    print("Brokerage & Slippage:     Kotak Neo Zero Brokerage (Rs. 0) + 0.15% Executed Slippage")
    print("Evaluation Horizon:       All 75 Calendar Months (January 2020 to March 2026)")
    print("Total Trading Sessions:   1,278 Intraday Days & 320 Weekly Cycles")
    print("=" * 115 + "\n")
    
    # ----------------------------------------------------
    # 1. Compute Daily Spot Indicators: ATR, ADX, Prev Candle
    # ----------------------------------------------------
    print("1. Computing Master Daily Technical Indicators (ATR-14, ADX-14, Candle Color)...")
    df_spot = pd.read_parquet("data/nifty_spot_master_2020_2026_1min.parquet")
    daily = df_spot.resample('1D').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    }).dropna()

    high = daily['high']
    low = daily['low']
    close = daily['close']
    prev_close = close.shift(1)

    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = pd.Series(tr, index=daily.index).rolling(14).mean()
    plus_di = 100 * (pd.Series(plus_dm, index=daily.index).rolling(14).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm, index=daily.index).rolling(14).mean() / atr)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(14).mean()

    daily['prev_adx'] = adx.shift(1)
    daily['prev_atr'] = atr.shift(1)
    daily['prev_close'] = close.shift(1)
    daily['prev_open'] = daily['open'].shift(1)
    daily['prev_green'] = daily['prev_close'] > daily['prev_open']
    daily['date_str'] = daily.index.strftime('%Y-%m-%d')
    daily_ind_map = daily.set_index('date_str')[['prev_adx', 'prev_atr', 'prev_green']].to_dict('index')
    print(f"   Calculated daily indicators across {len(daily)} sessions.\n")

    # ----------------------------------------------------
    # 2. Model 1: Baseline Intraday IC (Hold till 15:15, 90% SL)
    # ----------------------------------------------------
    print("2. Simulating Model 1: Baseline Intraday IC (No early target, 90% SL)...")
    with open('data/cache_ic_spreads_2020_2026.pkl', 'rb') as f:
        ic_cache = pickle.load(f)

    m1_trades = []
    for ctx in ic_cache:
        d = ctx['date']
        net_credit = ctx['net_credit_pts']
        lot_size = ctx['lot_size']
        sl_thresh = net_credit * 1.90
        
        spreads = ctx['spreads']
        times = ctx['times']
        n_bars = len(spreads)
        exit_idx = n_bars - 1
        exit_reason = 'TIME_EXIT'
        
        for i in range(n_bars):
            if spreads[i] >= sl_thresh:
                exit_idx = i
                exit_reason = 'SL_90'
                break
            elif times[i] >= ctx['ts_1515']:
                exit_idx = i
                exit_reason = 'TIME_EXIT'
                break
                
        exit_cost = spreads[exit_idx]
        pnl_pts = net_credit - exit_cost
        gross_pnl = pnl_pts * lot_size
        turnover = (ctx['sc_entry'] + ctx['lc_entry'] + ctx['sp_entry'] + ctx['lp_entry'] + 
                    ctx['sc'][exit_idx] + ctx['lc'][exit_idx] + ctx['sp'][exit_idx] + ctx['lp'][exit_idx]) * lot_size
        slippage = turnover * 0.0015
        net_pnl = gross_pnl - slippage
        m1_trades.append({'Date': d, 'Net_PnL': net_pnl, 'Model': 'M1_Baseline_IC'})

    df_m1 = pd.DataFrame(m1_trades)

    # ----------------------------------------------------
    # 3. Model 2: Optimized Intraday IC with 40% Early Target
    # ----------------------------------------------------
    print("3. Simulating Model 2: Optimized Intraday IC (40% Early Target + 90% SL)...")
    m2_trades = []
    for ctx in ic_cache:
        d = ctx['date']
        net_credit = ctx['net_credit_pts']
        lot_size = ctx['lot_size']
        tgt_thresh = net_credit * 0.60 # Book profit when spread decays by 40%
        sl_thresh = net_credit * 1.90  # 90% SL
        
        spreads = ctx['spreads']
        times = ctx['times']
        n_bars = len(spreads)
        exit_idx = n_bars - 1
        exit_reason = 'TIME_EXIT'
        
        for i in range(n_bars):
            if spreads[i] >= sl_thresh:
                exit_idx = i
                exit_reason = 'SL_90'
                break
            elif spreads[i] <= tgt_thresh:
                exit_idx = i
                exit_reason = 'TGT_40'
                break
            elif times[i] >= ctx['ts_1515']:
                exit_idx = i
                exit_reason = 'TIME_EXIT'
                break
                
        exit_cost = spreads[exit_idx]
        pnl_pts = net_credit - exit_cost
        gross_pnl = pnl_pts * lot_size
        turnover = (ctx['sc_entry'] + ctx['lc_entry'] + ctx['sp_entry'] + ctx['lp_entry'] + 
                    ctx['sc'][exit_idx] + ctx['lc'][exit_idx] + ctx['sp'][exit_idx] + ctx['lp'][exit_idx]) * lot_size
        slippage = turnover * 0.0015
        net_pnl = gross_pnl - slippage
        m2_trades.append({'Date': d, 'Net_PnL': net_pnl, 'Model': 'M2_Target40_IC'})

    df_m2 = pd.DataFrame(m2_trades)

    # ----------------------------------------------------
    # 4. Model 3: Directional Credit Spread (Supertrend ST 5, 2.0 with ADX >= 20 Filter)
    # ----------------------------------------------------
    print("4. Simulating Model 3: Directional Credit Spread (ST 5, 2.0 with ADX >= 20 Filter)...")
    df_cs_raw = pd.read_csv('results/st_cs_6yr_best_daily.csv')
    m3_trades = []
    for _, row in df_cs_raw.iterrows():
        d = row['Date']
        adx_val = daily_ind_map.get(d, {}).get('prev_adx', np.nan)
        # Apply ADX Filter: Only trade directional spread when ADX >= 20 (avoid choppy range)
        if not np.isnan(adx_val) and adx_val >= 20.0:
            m3_trades.append({'Date': d, 'Net_PnL': row['Net_PnL_Rs'], 'Model': 'M3_ST_ADX_CS'})
        else:
            m3_trades.append({'Date': d, 'Net_PnL': 0.0, 'Model': 'M3_ST_ADX_CS'})

    df_m3 = pd.DataFrame(m3_trades)

    # ----------------------------------------------------
    # 5. Model 4: Weekly Positional Iron Condor (35% Target / 40% SL)
    # ----------------------------------------------------
    print("5. Simulating Model 4: Weekly Positional Iron Condor (35% Target / 40% SL)...")
    with open('data/cache_weekly_positional_spreads_2020_2026.pkl', 'rb') as f:
        pos_cache = pickle.load(f)

    m4_trades = []
    for item in pos_cache:
        entry_date = item['entry_date']
        lot_size = get_nifty_lot_size(entry_date)
        net_credit = item['net_credit_pts']
        
        tgt_thresh = net_credit * 0.65 # 35% profit target
        sl_thresh = net_credit * 1.40  # 40% SL
        
        spreads = item['spreads']
        n_bars = len(spreads)
        exit_idx = n_bars - 1
        
        for i in range(n_bars):
            if spreads[i] >= sl_thresh:
                exit_idx = i
                break
            elif spreads[i] <= tgt_thresh:
                exit_idx = i
                break
                
        exit_cost = spreads[exit_idx]
        pnl_pts = net_credit - exit_cost
        gross_pnl = pnl_pts * lot_size
        sc_ex = item['sc'][exit_idx]
        lc_ex = item['lc'][exit_idx]
        sp_ex = item['sp'][exit_idx]
        lp_ex = item['lp'][exit_idx]
        turnover = (item['sc_entry'] + item['lc_entry'] + item['sp_entry'] + item['lp_entry'] + 
                    sc_ex + lc_ex + sp_ex + lp_ex) * lot_size
        slippage = turnover * 0.0015
        net_pnl = gross_pnl - slippage
        m4_trades.append({'Date': entry_date, 'Net_PnL': net_pnl, 'Model': 'M4_Weekly_Pos_IC'})

    df_m4 = pd.DataFrame(m4_trades)

    # ----------------------------------------------------
    # 6. Aggregate to Monthly Breakdown across all 75 Months
    # ----------------------------------------------------
    print("6. Aggregating Month-by-Month Matrix (75 Calendar Months)...")
    for df in [df_m1, df_m2, df_m3, df_m4]:
        df['Month'] = pd.to_datetime(df['Date']).dt.to_period('M')

    m1_m = df_m1.groupby('Month')['Net_PnL'].sum().rename('M1_Baseline_IC')
    m2_m = df_m2.groupby('Month')['Net_PnL'].sum().rename('M2_Target40_IC')
    m3_m = df_m3.groupby('Month')['Net_PnL'].sum().rename('M3_ST_ADX_CS')
    m4_m = df_m4.groupby('Month')['Net_PnL'].sum().rename('M4_Weekly_Pos_IC')

    master_m = pd.concat([m1_m, m2_m, m3_m, m4_m], axis=1).fillna(0.0)

    # Model 5: Institutional Multi-Strategy Ensemble (Theta Core M2 + Trend Momentum M3)
    master_m['M5_Hybrid_Ensemble'] = master_m['M2_Target40_IC'] + master_m['M3_ST_ADX_CS']

    # Model 6: Weekly Positional + Intraday Target Ensemble
    master_m['M6_Dual_Engine'] = master_m['M4_Weekly_Pos_IC'] + master_m['M2_Target40_IC']

    # ----------------------------------------------------
    # 7. Print Master Comparison Scorecard
    # ----------------------------------------------------
    print("\n" + "=" * 115)
    print("                    EXECUTIVE SUMMARY SCORECARD: 6 STRATEGIC MODELS COMPARED (2020-2026)                    ")
    print("=" * 115)
    print(f"{'Model Architecture':<32} | {'Net PnL (Rs)':<14} | {'ROI (%)':<8} | {'Pos Months':<12} | {'Win Rate %':<10} | {'Max DD (%)':<10} | {'Monthly Sharpe':<14}")
    print("-" * 115)

    models_order = [
        ('M1: Baseline IC (Hold 15:15, 90% SL)', 'M1_Baseline_IC'),
        ('M2: Intraday IC (40% Tgt, 90% SL)', 'M2_Target40_IC'),
        ('M3: ST Credit Spread (ADX Filter)', 'M3_ST_ADX_CS'),
        ('M4: Weekly Positional (35% Tgt, 40% SL)', 'M4_Weekly_Pos_IC'),
        ('M5: Hybrid Ensemble (M2 + M3)', 'M5_Hybrid_Ensemble'),
        ('M6: Dual Engine (M4 Pos + M2 Intraday)', 'M6_Dual_Engine')
    ]

    for label, col in models_order:
        tot_pnl = master_m[col].sum()
        roi = (tot_pnl / 200000.0) * 100.0
        pos_m = (master_m[col] > 0).sum()
        tot_m = len(master_m)
        m_wr = (pos_m / tot_m) * 100.0
        
        cum = master_m[col].cumsum() + 200000.0
        peak = cum.cummax()
        dd = (peak - cum) / peak * 100.0
        max_dd = dd.max()
        
        mean_pnl = master_m[col].mean()
        std_pnl = master_m[col].std()
        sharpe = (mean_pnl / std_pnl) * np.sqrt(12) if std_pnl > 0 else 0.0
        
        print(f"{label:<32} | Rs. {tot_pnl:>10,.2f} | {roi:>7.1f}% | {pos_m:>2d} / {tot_m:>2d}    | {m_wr:>8.1f}% | {max_dd:>8.2f}% | {sharpe:>12.2f}")
    print("=" * 115 + "\n")

    # ----------------------------------------------------
    # 8. Complete 75-Month Day-by-Day / Month-by-Month Ledger
    # ----------------------------------------------------
    master_m_df = master_m.reset_index()
    master_m_df['Year'] = master_m_df['Month'].dt.year
    master_m_df['Month_Name'] = master_m_df['Month'].dt.strftime('%b')
    master_m_df['M5_Cumulative_Cap'] = master_m_df['M5_Hybrid_Ensemble'].cumsum() + 200000.0
    master_m_df['M6_Cumulative_Cap'] = master_m_df['M6_Dual_Engine'].cumsum() + 200000.0

    output_path = Path("results/monthly_consistency_comparison_75m.csv")
    master_m_df.to_csv(output_path, index=False)
    print(f"Saved complete 75-month audit ledger to {output_path}.\n")

    # Print Year-by-Year breakdown for M5 & M6
    print("=" * 115)
    print("                   YEAR-BY-YEAR PnL BREAKDOWN: M5 (HYBRID ENSEMBLE) vs M6 (DUAL ENGINE)                   ")
    print("=" * 115)
    print(f"{'Year':<8} | {'M2 Intraday IC':<16} | {'M3 ST ADX CS':<16} | {'M4 Weekly Pos IC':<18} | {'M5 Hybrid (M2+M3)':<20} | {'M6 Dual (M4+M2)':<20}")
    print("-" * 115)
    
    y_grp = master_m_df.groupby('Year')[['M2_Target40_IC', 'M3_ST_ADX_CS', 'M4_Weekly_Pos_IC', 'M5_Hybrid_Ensemble', 'M6_Dual_Engine']].sum()
    for yr, r in y_grp.iterrows():
        print(f"{yr:<8} | Rs. {r['M2_Target40_IC']:>12,.2f} | Rs. {r['M3_ST_ADX_CS']:>12,.2f} | Rs. {r['M4_Weekly_Pos_IC']:>14,.2f} | Rs. {r['M5_Hybrid_Ensemble']:>16,.2f} | Rs. {r['M6_Dual_Engine']:>16,.2f}")
    print("-" * 115)
    tot_r = y_grp.sum()
    print(f"{'TOTAL':<8} | Rs. {tot_r['M2_Target40_IC']:>12,.2f} | Rs. {tot_r['M3_ST_ADX_CS']:>12,.2f} | Rs. {tot_r['M4_Weekly_Pos_IC']:>14,.2f} | Rs. {tot_r['M5_Hybrid_Ensemble']:>16,.2f} | Rs. {tot_r['M6_Dual_Engine']:>16,.2f}")
    print("=" * 115 + "\n")

    # ----------------------------------------------------
    # 9. Print Recent 24 Months Breakdown
    # ----------------------------------------------------
    print("=" * 115)
    print("                      RECENT 24 MONTHS PnL AUDIT (MONTH-BY-MONTH VERIFICATION)                      ")
    print("=" * 115)
    print(f"{'Period':<10} | {'M2 Target40 IC':<16} | {'M3 ST ADX CS':<16} | {'M4 Weekly Pos':<16} | {'M5 Hybrid':<16} | {'Status':<10}")
    print("-" * 115)
    recent = master_m_df.tail(24)
    for _, row in recent.iterrows():
        m_str = str(row['Month'])
        pnl_m5 = row['M5_Hybrid_Ensemble']
        status = "[PROFIT]" if pnl_m5 >= 0 else "[LOSS]"
        print(f"{m_str:<10} | Rs. {row['M2_Target40_IC']:>12,.2f} | Rs. {row['M3_ST_ADX_CS']:>12,.2f} | Rs. {row['M4_Weekly_Pos_IC']:>12,.2f} | Rs. {pnl_m5:>12,.2f} | {status:<10}")
    print("=" * 115)

if __name__ == "__main__":
    generate_monthly_consistency_master()
