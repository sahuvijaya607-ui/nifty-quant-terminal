"""
========================================================================================
QUANT RESEARCH: TRIPLE SUPERTREND OPTION BUYING STRATEGY
========================================================================================
1. 10 Supertrend Configurations (Triplets)
2. Timeframe Comparison: 5-Minute vs 15-Minute
3. Risk-Reward Optimization: "SL Chota, Target Bada" (15% SL vs 45%-60% Target + Trailing)
4. Sideways & PCR (Put-Call Ratio) Directional Filter
5. Starting Capital: Rs. 2,00,000 | 1 Lot (SEBI Lot Size Transitions) | Kotak Zero Brokerage
========================================================================================
"""

import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow.parquet as pq

# Add project root to sys.path
sys.path.insert(0, os.path.abspath("."))
from utils.nifty_lot_size import get_nifty_lot_size

def calculate_supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.Series:
    """Fast vectorized Supertrend calculation (1 = Green/Bull, -1 = Red/Bear)."""
    df_calc = df[['open', 'high', 'low', 'close']].copy()
    prev_close = df_calc['close'].shift(1)
    tr1 = df_calc['high'] - df_calc['low']
    tr2 = (df_calc['high'] - prev_close).abs()
    tr3 = (df_calc['low'] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    hl2 = (df_calc['high'] + df_calc['low']) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    c_vals = df_calc['close'].values
    bu_vals = basic_upper.values
    bl_vals = basic_lower.values
    fu_vals = basic_upper.values.copy()
    fl_vals = basic_lower.values.copy()
    t_vals = np.ones(len(df_calc), dtype=np.int8)

    for i in range(period, len(df_calc)):
        pc = c_vals[i-1]
        if bu_vals[i] < fu_vals[i-1] or pc > fu_vals[i-1]:
            fu_vals[i] = bu_vals[i]
        else:
            fu_vals[i] = fu_vals[i-1]
            
        if bl_vals[i] > fl_vals[i-1] or pc < fl_vals[i-1]:
            fl_vals[i] = bl_vals[i]
        else:
            fl_vals[i] = fl_vals[i-1]
            
        pt = t_vals[i-1]
        if pt == 1:
            t_vals[i] = -1 if c_vals[i] < fl_vals[i] else 1
        else:
            t_vals[i] = 1 if c_vals[i] > fu_vals[i] else -1

    return pd.Series(t_vals, index=df_calc.index)

def run_study():
    t_start = time.time()
    print("=" * 115)
    print("     TRIPLE SUPERTREND QUANT RESEARCH: 10 SETTINGS | 5M vs 15M | SL CHOTA TGT BADA | PCR FILTER     ")
    print("=" * 115)
    print("Starting Capital:         Rs. 2,00,000.00")
    print("Position Sizing:          1 Lot (Exact SEBI Historical Transitions)")
    print("Brokerage & Slippage:     Kotak Neo Zero Brokerage (Rs. 0) + 0.20% Traded Turnover Slippage")
    print("Asymmetric Risk-Reward:   SL = -15% (Small) | Target = +45% to +60% (Large) with Breakeven Trailing")
    print("=" * 115 + "\n")

    # Define 10 Distinct Supertrend Triplets (ST1 Fast, ST2 Medium, ST3 Slow)
    st_configs = [
        {"name": "S01_Classic_Std",     "st1": (7, 1.5),  "st2": (10, 2.0), "st3": (14, 3.0), "desc": "Classic Multi-Period"},
        {"name": "S02_Triple_3.0_Champ","st1": (7, 3.0),  "st2": (10, 3.0), "st3": (14, 3.0), "desc": "Dispute-Free 3.0 Champion"},
        {"name": "S03_Fast_Scalper",    "st1": (5, 1.5),  "st2": (7, 2.0),  "st3": (10, 2.5), "desc": "Fast Momentum Scalper"},
        {"name": "S04_Trend_Rider",     "st1": (7, 2.0),  "st2": (10, 3.0), "st3": (14, 3.0), "desc": "Trend Follower"},
        {"name": "S05_High_Precision",  "st1": (10, 1.5), "st2": (14, 2.0), "st3": (20, 3.0), "desc": "Noise Reduction"},
        {"name": "S06_Balanced_Momen",  "st1": (5, 2.0),  "st2": (10, 2.0), "st3": (15, 3.0), "desc": "Balanced Volatility"},
        {"name": "S07_Aggressive_Lead", "st1": (6, 1.5),  "st2": (9, 2.0),  "st3": (12, 2.5), "desc": "Short-Term Swing"},
        {"name": "S08_Fibonacci_ST",    "st1": (8, 2.0),  "st2": (13, 2.5), "st3": (21, 3.0), "desc": "Fibonacci Sequence"},
        {"name": "S09_Smooth_Swing",    "st1": (10, 2.0), "st2": (15, 2.5), "st3": (20, 3.0), "desc": "Smooth Institutional"},
        {"name": "S10_Wide_Vol_Band",   "st1": (7, 2.5),  "st2": (14, 2.5), "st3": (21, 3.5), "desc": "Wide Volatility Band"}
    ]

    # 1. Load Spot Data and compute 5m and 15m bars
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    print("1. Loading Master Spot Data...")
    df_spot = pd.read_parquet(spot_path)
    
    print("   Resampling to NSE-aligned 5-Minute and 15-Minute Spot bars...")
    df_15m = df_spot.resample('15min', origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_15m['range'] = df_15m['high'] - df_15m['low']
    df_15m['is_green'] = df_15m['close'] > df_15m['open']
    df_15m['is_red'] = df_15m['close'] < df_15m['open']
    df_15m['close_time'] = df_15m.index + pd.Timedelta(minutes=15)

    df_5m = df_spot.resample('5min', origin='start_day', offset='5min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_5m['range'] = df_5m['high'] - df_5m['low']
    df_5m['is_green'] = df_5m['close'] > df_5m['open']
    df_5m['is_red'] = df_5m['close'] < df_5m['open']
    df_5m['close_time'] = df_5m.index + pd.Timedelta(minutes=5)

    print(f"   Spot bars ready: 15-Min = {len(df_15m):,} bars | 5-Min = {len(df_5m):,} bars.\n")

    # Precalculate all 10 Supertrend triplets on both timeframes
    print("2. Precomputing all 10 Supertrend Configurations on 5m and 15m...")
    for cfg in st_configs:
        cname = cfg['name']
        p1, m1 = cfg['st1']
        p2, m2 = cfg['st2']
        p3, m3 = cfg['st3']
        
        # 15m
        df_15m[f"{cname}_st1"] = calculate_supertrend(df_15m, p1, m1)
        df_15m[f"{cname}_st2"] = calculate_supertrend(df_15m, p2, m2)
        df_15m[f"{cname}_st3"] = calculate_supertrend(df_15m, p3, m3)
        
        # 5m
        df_5m[f"{cname}_st1"] = calculate_supertrend(df_5m, p1, m1)
        df_5m[f"{cname}_st2"] = calculate_supertrend(df_5m, p2, m2)
        df_5m[f"{cname}_st3"] = calculate_supertrend(df_5m, p3, m3)

    print("   All 10 Supertrend Triplets successfully computed on both timeframes!\n")

    # 2. Options Data Processing (2024 to 2026 High-Precision Option Chain with PCR)
    clean_path = Path("data/nifty_option_chain_1min_clean.parquet")
    print(f"3. Loading Real Options Vault ({clean_path})...")
    t0_opt = time.time()
    
    opt_table = pq.read_table(clean_path, columns=[
        'timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'pcr', 'underlying_spot_price'
    ])
    df_opt = opt_table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['expiry_date'] = df_opt['expiry'].dt.date
    
    all_dates = sorted(df_opt['date'].unique())
    print(f"   Loaded options vault ({len(df_opt):,} rows across {len(all_dates)} trading sessions) in {time.time()-t0_opt:.2f}s!\n")

    # 3. High-Performance Pre-Indexing of Daily Option Trajectories & PCR
    print("   Building high-performance O(1) Daily Strike Trajectories & PCR Map...")
    t_idx0 = time.time()
    
    daily_opt_cache = {}
    for dt, day_df in df_opt.groupby('date'):
        # Target expiry > dt
        expiries = sorted([exp for exp in day_df['expiry_date'].unique() if exp > dt])
        if not expiries:
            continue
        target_exp = expiries[0]
        exp_df = day_df[day_df['expiry_date'] == target_exp]
        
        # Extract 1-min PCR & Spot map safely and fast
        ts_df = exp_df[['timestamp', 'pcr', 'underlying_spot_price']].drop_duplicates('timestamp')
        pcr_vals = ts_df['pcr'].fillna(1.0).to_numpy()
        spot_vals = ts_df['underlying_spot_price'].fillna(0.0).to_numpy()
        times_vals = ts_df['timestamp'].to_numpy()
        ts_info = {times_vals[i]: (float(pcr_vals[i]), float(spot_vals[i])) for i in range(len(times_vals))}
            
        # Group by (strike, option_type)
        trajectories = {}
        for (stk, otype), grp in exp_df.groupby(['strike_price', 'option_type']):
            grp_sorted = grp.sort_values('timestamp')
            trajectories[(int(stk), otype)] = {
                'times': grp_sorted['timestamp'].to_numpy(),
                'ltp': grp_sorted['ltp'].fillna(0.0).to_numpy()
            }
            
        daily_opt_cache[dt] = {
            'target_exp': target_exp,
            'ts_info': ts_info,
            'trajectories': trajectories
        }
        
    print(f"   Pre-indexed {len(daily_opt_cache)} trading sessions into memory in {time.time()-t_idx0:.2f}s!\n")

    # 4. Multi-Scenario Backtest Engine
    # Timeframes: ['15m', '5m']
    # PCR Modes: [False (No PCR), True (With PCR Filter)]
    # Risk-Reward Structures:
    #   RR1: SL = 15%, Target = 45% (1:3 RR) [User rule: SL chota, Target bada]
    #   RR2: SL = 15%, Target = 60% (1:4 RR)
    #   RR3: SL = 20%, Target = 50% (1:2.5 RR)
    
    print("4. Executing Comprehensive Simulation Matrix across all 10 Settings...")
    print("   Evaluating: 10 Settings x 2 Timeframes (5m, 15m) x 2 PCR Modes (Off, On) x 3 RR Structures (120 Scenarios)...\n")

    results_master = []
    champion_trades = []
    best_pnl = -999999.0

    rr_profiles = [
        {"rr_name": "RR_1_3_TightSL", "sl_pct": 0.15, "tgt_pct": 0.45, "be_trigger": 0.20},
        {"rr_name": "RR_1_4_LargeTgt", "sl_pct": 0.15, "tgt_pct": 0.60, "be_trigger": 0.20},
        {"rr_name": "RR_Classic_20_50", "sl_pct": 0.20, "tgt_pct": 0.50, "be_trigger": 0.25}
    ]

    t_sim0 = time.time()
    scenario_idx = 0

    for tf_name, tf_df in [('15m', df_15m), ('5m', df_5m)]:
        # Filter bars within trading window
        tf_df_sub = tf_df[(tf_df['close_time'].dt.time >= pd.Timestamp('09:30:00').time()) & 
                          (tf_df['close_time'].dt.time <= pd.Timestamp('14:30:00').time()) &
                          (tf_df['range'] < 45.0)]

        for pcr_filter in [False, True]:
            for rr in rr_profiles:
                sl_pct = rr['sl_pct']
                tgt_pct = rr['tgt_pct']
                be_trigger = rr['be_trigger']
                rr_name = rr['rr_name']

                for cfg in st_configs:
                    scenario_idx += 1
                    cname = cfg['name']
                    trades = []

                    for dt in all_dates:
                        if dt not in daily_opt_cache:
                            continue
                        day_cache = daily_opt_cache[dt]
                        ts_info = day_cache['ts_info']
                        trajectories = day_cache['trajectories']
                        lot_size = get_nifty_lot_size(dt)

                        day_bars = tf_df_sub[tf_df_sub.index.date == dt]
                        if day_bars.empty:
                            continue

                        daily_trades = 0
                        last_exit_ts = None

                        for _, bar in day_bars.iterrows():
                            if daily_trades >= 2:
                                break

                            bar_close_ts = bar['close_time']
                            if last_exit_ts is not None and bar_close_ts <= last_exit_ts:
                                continue

                            # Supertrend conditions
                            st1 = bar[f"{cname}_st1"]
                            st2 = bar[f"{cname}_st2"]
                            st3 = bar[f"{cname}_st3"]

                            bull_sig = (st1 == 1 and st2 == 1 and st3 == 1 and bar['is_green'])
                            bear_sig = (st1 == -1 and st2 == -1 and st3 == -1 and bar['is_red'])

                            if not (bull_sig or bear_sig):
                                continue

                            opt_type = 'CE' if bull_sig else 'PE'
                            
                            # Entry execution at next 1-minute bar close (Zero look-ahead bias)
                            entry_ts = bar_close_ts + pd.Timedelta(minutes=1)
                            if entry_ts not in ts_info:
                                continue

                            bar_pcr, spot_at_entry = ts_info[entry_ts]

                            # PCR Filter Logic
                            if pcr_filter:
                                # Directional PCR Rule:
                                # Bullish (CE): PCR >= 1.05 (Put writers dominating, upside support)
                                # Bearish (PE): PCR <= 0.95 (Call writers dominating, downside resistance)
                                # If 0.95 < PCR < 1.05 -> Sideways/Chop -> SKIP
                                if bull_sig and bar_pcr < 1.05:
                                    continue
                                if bear_sig and bar_pcr > 0.95:
                                    continue

                            # ATM Strike: round(spot / 50) * 50
                            atm_strike = int(round(spot_at_entry / 50.0) * 50)
                            key = (atm_strike, opt_type)
                            if key not in trajectories:
                                continue

                            traj = trajectories[key]
                            t_arr = traj['times']
                            p_arr = traj['ltp']

                            # Find index of entry_ts
                            idx_matches = np.where(t_arr == entry_ts)[0]
                            if len(idx_matches) == 0:
                                continue
                            e_idx = idx_matches[0]
                            entry_price = float(p_arr[e_idx])
                            if entry_price <= 5.0:
                                continue

                            # Minute-by-minute execution tracking
                            sl_price = entry_price * (1.0 - sl_pct) # Small SL
                            tgt_price = entry_price * (1.0 + tgt_pct) # Large Target
                            be_active = False

                            exit_price = None
                            exit_reason = None
                            exit_ts = None

                            ts_1515_np = np.datetime64(f"{dt}T15:15:00")

                            # Walk through forward minutes
                            for i in range(e_idx + 1, len(t_arr)):
                                curr_ts = t_arr[i]
                                curr_price = float(p_arr[i])

                                # Trailing Breakeven: Once profit >= be_trigger, move SL to entry price
                                if not be_active and curr_price >= entry_price * (1.0 + be_trigger):
                                    sl_price = entry_price
                                    be_active = True

                                # 1. Hard Stop-Loss Hit
                                if curr_price <= sl_price:
                                    exit_price = curr_price
                                    exit_reason = "BREAKEVEN_SL" if be_active else "HARD_SL"
                                    exit_ts = pd.Timestamp(curr_ts)
                                    break

                                # 2. Large Target Hit
                                if curr_price >= tgt_price:
                                    exit_price = curr_price
                                    exit_reason = f"TARGET (+{int(tgt_pct*100)}%)"
                                    exit_ts = pd.Timestamp(curr_ts)
                                    break

                                # 3. ST2 Trend Flip on Bar Close
                                curr_ts_pd = pd.Timestamp(curr_ts)
                                if curr_ts_pd in tf_df.index:
                                    st2_curr = tf_df.loc[curr_ts_pd, f"{cname}_st2"]
                                    if (opt_type == 'CE' and st2_curr == -1) or (opt_type == 'PE' and st2_curr == 1):
                                        exit_price = curr_price
                                        exit_reason = "ST2_FLIP"
                                        exit_ts = curr_ts_pd
                                        break

                                # 4. Intraday 15:15 Square-off
                                if curr_ts >= ts_1515_np:
                                    exit_price = curr_price
                                    exit_reason = "TIME_1515"
                                    exit_ts = pd.Timestamp(curr_ts)
                                    break

                            if exit_price is None:
                                exit_price = float(p_arr[-1])
                                exit_reason = "CLOSE_EXIT"
                                exit_ts = pd.Timestamp(t_arr[-1])

                            # Calculate PnL
                            points_pnl = exit_price - entry_price
                            gross_pnl = points_pnl * lot_size
                            turnover = (entry_price + exit_price) * lot_size
                            slippage = turnover * 0.0020 # 0.20% realistic slippage
                            net_pnl = gross_pnl - slippage

                            daily_trades += 1
                            last_exit_ts = exit_ts

                            trades.append({
                                'date': dt,
                                'timeframe': tf_name,
                                'config': cname,
                                'pcr_filter': pcr_filter,
                                'rr_profile': rr_name,
                                'entry_ts': entry_ts,
                                'exit_ts': exit_ts,
                                'atm': atm_strike,
                                'opt_type': opt_type,
                                'entry_price': entry_price,
                                'exit_price': exit_price,
                                'pcr': bar_pcr,
                                'exit_reason': exit_reason,
                                'points_pnl': points_pnl,
                                'gross_pnl': gross_pnl,
                                'net_pnl': net_pnl,
                                'lot_size': lot_size
                            })

                    tdf = pd.DataFrame(trades)
                    if tdf.empty:
                        continue

                    tot_trades = len(tdf)
                    pos_trades = (tdf['net_pnl'] > 0).sum()
                    win_rate = (pos_trades / tot_trades) * 100.0 if tot_trades > 0 else 0.0
                    tot_pnl = tdf['net_pnl'].sum()
                    gross_tot = tdf['gross_pnl'].sum()
                    slippage_tot = gross_tot - tot_pnl

                    wins = tdf[tdf['net_pnl'] > 0]['net_pnl']
                    losses = tdf[tdf['net_pnl'] < 0]['net_pnl'].abs()
                    profit_factor = wins.sum() / losses.sum() if losses.sum() > 0 else 9.99

                    # Monthly metrics
                    tdf['month'] = pd.to_datetime(tdf['date']).dt.to_period('M')
                    m_pnl = tdf.groupby('month')['net_pnl'].sum()
                    pos_m = (m_pnl > 0).sum()
                    tot_m = len(m_pnl)
                    monthly_wr = (pos_m / tot_m) * 100.0 if tot_m > 0 else 0.0

                    cum = tdf['net_pnl'].cumsum() + 200000.0
                    dd = (cum.cummax() - cum) / cum.cummax() * 100.0
                    max_dd = dd.max()

                    results_master.append({
                        'Timeframe': tf_name,
                        'Setting': cname,
                        'Description': cfg['desc'],
                        'PCR_Filter': 'WITH PCR' if pcr_filter else 'NO PCR',
                        'RR_Profile': rr_name,
                        'Total_Trades': tot_trades,
                        'Win_Rate_Pct': win_rate,
                        'Gross_PnL_Rs': gross_tot,
                        'Slippage_Rs': slippage_tot,
                        'Net_PnL_Rs': tot_pnl,
                        'Profit_Factor': profit_factor,
                        'Max_DD_Pct': max_dd,
                        'Pos_Months': f"{pos_m}/{tot_m}",
                        'Monthly_WR_Pct': monthly_wr
                    })

                    if tot_pnl > best_pnl:
                        best_pnl = tot_pnl
                        champion_trades = tdf.copy()

    print(f"   Evaluated all 120 Scenarios in {time.time()-t_sim0:.2f}s!\n")

    # Convert results to DataFrame
    res_df = pd.DataFrame(results_master)
    res_path = Path("results/triple_st_10settings_comparison.csv")
    res_df.to_csv(res_path, index=False)
    print(f"Simulation completed! Saved full matrix ({len(res_df)} models) to {res_path}.\n")

    # ----------------------------------------------------
    # REPORT SECTION 1: 5-MIN vs 15-MIN TIMEFRAME COMPARISON
    # ----------------------------------------------------
    print("=" * 115)
    print("                1. TIMEFRAME COMPARISON AUDIT: 5-MINUTE vs 15-MINUTE (WHICH IS BEST?)                ")
    print("=" * 115)
    print(f"{'Timeframe':<10} | {'Total Trades':<14} | {'Win Rate %':<12} | {'Net PnL (Rs)':<16} | {'Profit Factor':<15} | {'Max DD (%)':<12} | {'Monthly WR %':<14}")
    print("-" * 115)
    tf_summary = res_df.groupby('Timeframe').agg({
        'Total_Trades': 'mean',
        'Win_Rate_Pct': 'mean',
        'Net_PnL_Rs': 'mean',
        'Profit_Factor': 'mean',
        'Max_DD_Pct': 'mean',
        'Monthly_WR_Pct': 'mean'
    })
    for tf, r in tf_summary.iterrows():
        print(f"{tf:<10} | {r['Total_Trades']:>12.1f}   | {r['Win_Rate_Pct']:>10.1f}% | Rs. {r['Net_PnL_Rs']:>12,.2f} | {r['Profit_Factor']:>13.2f} | {r['Max_DD_Pct']:>10.2f}% | {r['Monthly_WR_Pct']:>12.1f}%")
    print("=" * 115 + "\n")

    # ----------------------------------------------------
    # REPORT SECTION 2: PCR FILTER IMPACT (SIDEWAYS ELIMINATION)
    # ----------------------------------------------------
    print("=" * 115)
    print("               2. PCR (PUT-CALL RATIO) FILTER AUDIT: DOES PCR ELIMINATE SIDEWAYS CHOP?               ")
    print("=" * 115)
    print(f"{'PCR Mode':<12} | {'Avg Trades':<12} | {'Win Rate %':<12} | {'Net PnL (Rs)':<16} | {'Profit Factor':<15} | {'Max DD (%)':<12} | {'Monthly WR %':<14}")
    print("-" * 115)
    pcr_summary = res_df.groupby('PCR_Filter').agg({
        'Total_Trades': 'mean',
        'Win_Rate_Pct': 'mean',
        'Net_PnL_Rs': 'mean',
        'Profit_Factor': 'mean',
        'Max_DD_Pct': 'mean',
        'Monthly_WR_Pct': 'mean'
    })
    for pcr_m, r in pcr_summary.iterrows():
        print(f"{pcr_m:<12} | {r['Total_Trades']:>10.1f}   | {r['Win_Rate_Pct']:>10.1f}% | Rs. {r['Net_PnL_Rs']:>12,.2f} | {r['Profit_Factor']:>13.2f} | {r['Max_DD_Pct']:>10.2f}% | {r['Monthly_WR_Pct']:>12.1f}%")
    print("=" * 115 + "\n")

    # ----------------------------------------------------
    # REPORT SECTION 3: RISK-REWARD STRUCTURES ("SL CHOTA, TARGET BADA")
    # ----------------------------------------------------
    print("=" * 115)
    print("         3. RISK-REWARD STRUCTURES AUDIT: 'SL CHOTA, TARGET BADA' (15% SL vs 45%-60% TGT)         ")
    print("=" * 115)
    print(f"{'Risk-Reward Profile':<22} | {'Avg Trades':<12} | {'Win Rate %':<12} | {'Net PnL (Rs)':<16} | {'Profit Factor':<15} | {'Max DD (%)':<12}")
    print("-" * 115)
    rr_summary = res_df.groupby('RR_Profile').agg({
        'Total_Trades': 'mean',
        'Win_Rate_Pct': 'mean',
        'Net_PnL_Rs': 'mean',
        'Profit_Factor': 'mean',
        'Max_DD_Pct': 'mean'
    })
    for rr_p, r in rr_summary.iterrows():
        print(f"{rr_p:<22} | {r['Total_Trades']:>10.1f}   | {r['Win_Rate_Pct']:>10.1f}% | Rs. {r['Net_PnL_Rs']:>12,.2f} | {r['Profit_Factor']:>13.2f} | {r['Max_DD_Pct']:>10.2f}%")
    print("=" * 115 + "\n")

    # ----------------------------------------------------
    # REPORT SECTION 4: TOP 10 SETTINGS LEADERBOARD
    # ----------------------------------------------------
    print("=" * 115)
    print("                     4. TOP 10 SUPERTREND CONFIGURATIONS LEADERBOARD (RANKED BY NET PnL)                     ")
    print("=" * 115)
    print(f"{'Rank':<5} | {'Setting Name':<22} | {'TF':<5} | {'PCR':<8} | {'RR Profile':<16} | {'Trades':<7} | {'WinRate':<8} | {'Net PnL (Rs)':<14} | {'PF':<6} | {'Max DD':<8} | {'Months WR':<10}")
    print("-" * 115)
    top10 = res_df.sort_values(by='Net_PnL_Rs', ascending=False).head(10).reset_index(drop=True)
    for idx, r in top10.iterrows():
        print(f"#{idx+1:<4} | {r['Setting']:<22} | {r['Timeframe']:<5} | {r['PCR_Filter']:<8} | {r['RR_Profile']:<16} | {r['Total_Trades']:<7} | {r['Win_Rate_Pct']:>6.1f}% | Rs. {r['Net_PnL_Rs']:>10,.2f} | {r['Profit_Factor']:>4.2f} | {r['Max_DD_Pct']:>6.2f}% | {r['Monthly_WR_Pct']:>6.1f}%")
    print("=" * 115 + "\n")

    # Save champion trades
    if not champion_trades.empty:
        champ_path = Path("results/triple_st_champion_trades.csv")
        champion_trades.to_csv(champ_path, index=False)
        print(f"Saved Champion Setting trade log to {champ_path}.\n")

        # Print Year-by-Year breakdown for Champion
        print("=" * 115)
        print(f"   YEAR-BY-YEAR PnL BREAKDOWN FOR RANK #1 CHAMPION: {champion_trades['config'].iloc[0]} ({champion_trades['timeframe'].iloc[0]}, {champion_trades['rr_profile'].iloc[0]})   ")
        print("=" * 115)
        print(f"{'Year':<8} | {'Trades':<8} | {'Win Rate %':<12} | {'Gross PnL (Rs)':<16} | {'Slippage (Rs)':<16} | {'Net PnL (Rs)':<16}")
        print("-" * 115)
        champion_trades['year'] = pd.to_datetime(champion_trades['date']).dt.year
        for yr, y_df in champion_trades.groupby('year'):
            y_trades = len(y_df)
            y_wr = (y_df['net_pnl'] > 0).mean() * 100
            y_gross = y_df['gross_pnl'].sum()
            y_slip = (y_df['gross_pnl'] - y_df['net_pnl']).sum()
            y_net = y_df['net_pnl'].sum()
            print(f"{yr:<8} | {y_trades:>6d}   | {y_wr:>10.1f}% | Rs. {y_gross:>12,.2f} | Rs. {y_slip:>12,.2f} | Rs. {y_net:>12,.2f}")
        print("-" * 115)
        tot_tr = len(champion_trades)
        tot_wr = (champion_trades['net_pnl'] > 0).mean() * 100
        tot_gross = champion_trades['gross_pnl'].sum()
        tot_slip = (champion_trades['gross_pnl'] - champion_trades['net_pnl']).sum()
        tot_net = champion_trades['net_pnl'].sum()
        print(f"{'TOTAL':<8} | {tot_tr:>6d}   | {tot_wr:>10.1f}% | Rs. {tot_gross:>12,.2f} | Rs. {tot_slip:>12,.2f} | Rs. {tot_net:>12,.2f}")
        print("=" * 115)

if __name__ == "__main__":
    run_study()
