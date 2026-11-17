"""
========================================================================================
QUANT STRATEGY: 15-MINUTE TRIPLE SUPERTREND INTRADAY CREDIT SPREAD (10 SETTINGS BENCHMARK)
========================================================================================
Architecture & Strategy Specification (Locked via /grill-me):
1. Account & Sizing:
   - Starting Capital: Rs. 2,00,000.
   - Position Sizing: 1 Lot (1 x get_nifty_lot_size(trade_date)).
   - Max 2 trades per day (1 active position at a time).
   
2. Signal Timeframe (15-Minute NSE-aligned Spot Bars):
   - Entry Window: Bar close between 09:30:00 and 14:30:00 IST (09:15-09:29 strictly skipped).
   - Signal Candle Filter: Candle range (High - Low) must be strictly < 45 points on Spot.
   - Bullish Signal: All 3 Supertrends GREEN and Signal Candle GREEN (Close > Open)
     -> Enter Bull Put Credit Spread (SELL 35-Delta PE, BUY 15-Delta PE).
   - Bearish Signal: All 3 Supertrends RED and Signal Candle RED (Close < Open)
     -> Enter Bear Call Credit Spread (SELL 35-Delta CE, BUY 15-Delta CE).
   - Disagreement: If any of the 3 Supertrends disagree -> STRICTLY SKIP / NO TRADE!
   
3. Smart Expiry Selection:
   - Nearest weekly expiry strictly after today (expiry > current_date).
   
4. Strike Delta Architecture:
   - Short Leg: 35-Delta OTM (Delta ~ -0.35 for PE, +0.35 for CE).
   - Long Hedge: 15-Delta OTM (Delta ~ -0.15 for PE, +0.15 for CE).
   
5. Risk-Reward Structure ("SL Chota, Target Bada" Grid):
   - Pair 1: 40% Target / 80% Stop-Loss (1:2 RR)
   - Pair 2: 50% Target / 80% Stop-Loss (1:1.6 RR - Tight SL)
   - Pair 3: 50% Target / 100% Stop-Loss (1:2 RR - Double SL)
   
6. Transaction Costs:
   - Brokerage: Rs. 0 (Kotak Neo Zero-Brokerage Plan).
   - Slippage: 0.15% on traded turnover (entry + exit).
========================================================================================
"""

import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import scipy.stats as si
import pyarrow.parquet as pq

# Add project root to sys.path
sys.path.insert(0, os.path.abspath("."))
from utils.nifty_lot_size import get_nifty_lot_size

def calc_bs_deltas(spot: float, strikes: np.ndarray, dte_years: float, r: float, sigma: float, option_type: str) -> np.ndarray:
    """Fast vectorized Black-Scholes deltas for option strikes."""
    strikes = np.asarray(strikes, dtype=float)
    T = max(float(dte_years), 0.001)
    sig = max(float(sigma), 0.05)
    d1 = (np.log(spot / strikes) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    if option_type == 'CE':
        return si.norm.cdf(d1)
    else:
        return si.norm.cdf(d1) - 1.0

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
    print("     15-MIN TRIPLE SUPERTREND INTRADAY CREDIT SPREAD: 10 SETTINGS BENCHMARK & OPTIMIZATION     ")
    print("=" * 115)
    print("Starting Capital:         Rs. 2,00,000.00")
    print("Position Sizing:          1 Lot (Exact SEBI Historical Transitions)")
    print("Brokerage & Slippage:     Kotak Neo Zero Brokerage (Rs. 0) + 0.15% Turnover Slippage")
    print("Strategy Model:           Intraday Credit Spreads (Bull Put: 35-Delta/15-Delta PE | Bear Call: 35-Delta/15-Delta CE)")
    print("Signal Confirmation:      All 3 Supertrends + Signal Candle MUST AGREE (Disagreement = SKIP)")
    print("Target & SL Grid (3):     [40% Tgt / 80% SL], [50% Tgt / 80% SL], [50% Tgt / 100% SL]")
    print("=" * 115 + "\n")

    # Define 10 Distinct Supertrend Triplets (ST1 Fast, ST2 Medium, ST3 Slow)
    st_configs = [
        {"name": "Set01_Classic_Std",     "st1": (7, 1.5),  "st2": (10, 2.0), "st3": (14, 3.0), "desc": "Classic Multi-Period"},
        {"name": "Set02_Triple_3.0_Champ","st1": (7, 3.0),  "st2": (10, 3.0), "st3": (14, 3.0), "desc": "Dispute-Free 3.0 Champion"},
        {"name": "Set03_Fast_Scalp",      "st1": (5, 1.5),  "st2": (7, 2.0),  "st3": (10, 2.5), "desc": "Fast Momentum Scalper"},
        {"name": "Set04_Trend_Rider",     "st1": (7, 2.0),  "st2": (10, 3.0), "st3": (14, 3.0), "desc": "Trend Follower"},
        {"name": "Set05_Precision",       "st1": (10, 1.5), "st2": (14, 2.0), "st3": (20, 3.0), "desc": "High Precision Filter"},
        {"name": "Set06_Balanced_Momen",  "st1": (5, 2.0),  "st2": (10, 2.0), "st3": (15, 3.0), "desc": "Balanced Volatility"},
        {"name": "Set07_Aggressive_Lead", "st1": (6, 1.5),  "st2": (9, 2.0),  "st3": (12, 2.5), "desc": "Short-Term Swing"},
        {"name": "Set08_Fibonacci_ST",    "st1": (8, 2.0),  "st2": (13, 2.5), "st3": (21, 3.0), "desc": "Fibonacci Supertrend"},
        {"name": "Set09_Smooth_Swing",    "st1": (10, 2.0), "st2": (15, 2.5), "st3": (20, 3.0), "desc": "Smooth Institutional"},
        {"name": "Set10_Wide_Vol_Band",   "st1": (7, 2.5),  "st2": (14, 2.5), "st3": (21, 3.5), "desc": "Wide Volatility Band"}
    ]

    target_sl_pairs = [
        {"tgt": 0.40, "sl": 0.80, "label": "40% Tgt / 80% SL"},
        {"tgt": 0.50, "sl": 0.80, "label": "50% Tgt / 80% SL"},
        {"tgt": 0.50, "sl": 1.00, "label": "50% Tgt / 100% SL"}
    ]

    # 1. Load Master Spot Data & compute 15m bars + Rolling Vol
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    print("1. Loading Master Spot Data...")
    df_spot = pd.read_parquet(spot_path)
    
    print("   Resampling to NSE-aligned 15-Minute Spot bars and calculating Rolling Volatility...")
    df_15m = df_spot.resample('15min', origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_15m['range'] = df_15m['high'] - df_15m['low']
    df_15m['is_green'] = df_15m['close'] > df_15m['open']
    df_15m['is_red'] = df_15m['close'] < df_15m['open']
    df_15m['close_time'] = df_15m.index + pd.Timedelta(minutes=15)

    # Compute rolling volatility on Daily bars for accurate Black-Scholes delta calculation
    df_daily = df_spot.resample('1D').agg({'close': 'last'}).dropna()
    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = df_daily['returns'].rolling(30).std() * np.sqrt(252)
    df_daily['rolling_vol'] = df_daily['rolling_vol'].bfill().fillna(0.18)
    daily_vol_map = {d.date(): float(v) for d, v in df_daily['rolling_vol'].items()}

    print(f"   Spot bars ready: 15-Min = {len(df_15m):,} bars.\n")

    # 2. Precompute all 10 Supertrend Configurations on 15m
    print("2. Precomputing all 10 Supertrend Configurations on 15-Minute Bars...")
    for cfg in st_configs:
        cname = cfg['name']
        p1, m1 = cfg['st1']
        p2, m2 = cfg['st2']
        p3, m3 = cfg['st3']
        df_15m[f"{cname}_st1"] = calculate_supertrend(df_15m, p1, m1)
        df_15m[f"{cname}_st2"] = calculate_supertrend(df_15m, p2, m2)
        df_15m[f"{cname}_st3"] = calculate_supertrend(df_15m, p3, m3)

    print("   All 10 Supertrend Triplets successfully computed!\n")

    # 3. Load Options Data
    # We use data/nifty_option_chain_1min_clean.parquet (36M rows covering 2024 to 2026)
    clean_path = Path("data/nifty_option_chain_1min_clean.parquet")
    print(f"3. Loading Real Options Vault ({clean_path})...")
    t0_opt = time.time()
    
    opt_table = pq.read_table(clean_path, columns=[
        'timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'delta', 'underlying_spot_price'
    ])
    df_opt = opt_table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['expiry_date'] = df_opt['expiry'].dt.date
    
    all_dates = sorted(df_opt['date'].unique())
    print(f"   Loaded options vault ({len(df_opt):,} rows across {len(all_dates)} trading sessions) in {time.time()-t0_opt:.2f}s!\n")

    # 4. Build Fast Daily Trajectory Cache
    print("   Pre-indexing daily strike trajectories for O(1) instantaneous lookup...")
    t_idx0 = time.time()
    daily_opt_cache = {}
    
    for dt, day_df in df_opt.groupby('date'):
        expiries = sorted([exp for exp in day_df['expiry_date'].unique() if exp > dt])
        if not expiries:
            continue
        target_exp = expiries[0]
        exp_df = day_df[day_df['expiry_date'] == target_exp]
        
        # Spot price map by timestamp
        ts_spot_map = dict(exp_df[['timestamp', 'underlying_spot_price']].drop_duplicates('timestamp').values)
        
        # Trajectories by (strike, option_type)
        trajectories = {}
        for (stk, otype), grp in exp_df.groupby(['strike_price', 'option_type']):
            grp_sorted = grp.sort_values('timestamp')
            trajectories[(int(stk), otype)] = {
                'times': grp_sorted['timestamp'].to_numpy(),
                'ltp': grp_sorted['ltp'].fillna(0.0).to_numpy(),
                'delta': grp_sorted['delta'].fillna(0.0).to_numpy() if 'delta' in grp_sorted.columns else None
            }
            
        daily_opt_cache[dt] = {
            'target_exp': target_exp,
            'ts_spot_map': ts_spot_map,
            'trajectories': trajectories,
            'strikes_pe': sorted([int(stk) for stk, otype in trajectories.keys() if otype == 'PE']),
            'strikes_ce': sorted([int(stk) for stk, otype in trajectories.keys() if otype == 'CE'])
        }
        
    print(f"   Pre-indexed {len(daily_opt_cache)} trading sessions into memory in {time.time()-t_idx0:.2f}s!\n")

    # 5. Execute Simulation Matrix: 10 Settings x 3 Target/SL Pairs = 30 Scenarios
    print("4. Executing Comprehensive Simulation Matrix across all 10 Triple Supertrend Settings...")
    print("   Testing: 10 Settings x 3 Target/SL Pairs = 30 Total Scenarios on 15-Minute Bars...\n")

    results_master = []
    champion_trades = []
    best_ret_dd = -999999.0

    tf_df_sub = df_15m[(df_15m['close_time'].dt.time >= pd.Timestamp('09:30:00').time()) & 
                       (df_15m['close_time'].dt.time <= pd.Timestamp('14:30:00').time()) &
                       (df_15m['range'] < 45.0)]

    t_sim0 = time.time()

    for cfg in st_configs:
        cname = cfg['name']
        
        for t_sl in target_sl_pairs:
            tgt_pct = t_sl['tgt']
            sl_pct = t_sl['sl']
            tsl_label = t_sl['label']

            trades = []

            for dt in all_dates:
                if dt not in daily_opt_cache:
                    continue
                day_cache = daily_opt_cache[dt]
                ts_spot_map = day_cache['ts_spot_map']
                trajectories = day_cache['trajectories']
                target_exp = day_cache['target_exp']
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

                    # Check 3 Supertrends
                    st1 = bar[f"{cname}_st1"]
                    st2 = bar[f"{cname}_st2"]
                    st3 = bar[f"{cname}_st3"]

                    bull_sig = (st1 == 1 and st2 == 1 and st3 == 1 and bar['is_green'])
                    bear_sig = (st1 == -1 and st2 == -1 and st3 == -1 and bar['is_red'])

                    if not (bull_sig or bear_sig):
                        continue

                    entry_ts = bar_close_ts + pd.Timedelta(minutes=1)
                    if entry_ts not in ts_spot_map:
                        continue

                    spot_entry = ts_spot_map[entry_ts]
                    dte_days = (target_exp - dt).days
                    dte_years = max(dte_days / 365.25, 0.001)
                    sigma = daily_vol_map.get(dt, 0.16)

                    # --- Strike Selection: 35Δ Short / 15Δ Long ---
                    if bull_sig:
                        # Bull Put Credit Spread (SELL 35Δ PE, BUY 15Δ PE)
                        spread_type = "BULL_PUT_SPREAD"
                        strikes_avail = day_cache['strikes_pe']
                        if len(strikes_avail) < 2:
                            continue
                        deltas = calc_bs_deltas(spot_entry, np.array(strikes_avail), dte_years, 0.07, sigma, 'PE')
                        # Find closest to -0.35 and -0.15
                        idx_35 = np.argmin(np.abs(deltas - (-0.35)))
                        idx_15 = np.argmin(np.abs(deltas - (-0.15)))
                        short_k = strikes_avail[idx_35]
                        long_k = strikes_avail[idx_15]
                        if short_k <= long_k:
                            # Short strike must be higher than long strike for Bull Put Spread
                            continue
                        key_s = (short_k, 'PE')
                        key_l = (long_k, 'PE')
                    else:
                        # Bear Call Credit Spread (SELL 35Δ CE, BUY 15Δ CE)
                        spread_type = "BEAR_CALL_SPREAD"
                        strikes_avail = day_cache['strikes_ce']
                        if len(strikes_avail) < 2:
                            continue
                        deltas = calc_bs_deltas(spot_entry, np.array(strikes_avail), dte_years, 0.07, sigma, 'CE')
                        # Find closest to +0.35 and +0.15
                        idx_35 = np.argmin(np.abs(deltas - 0.35))
                        idx_15 = np.argmin(np.abs(deltas - 0.15))
                        short_k = strikes_avail[idx_35]
                        long_k = strikes_avail[idx_15]
                        if short_k >= long_k:
                            # Short strike must be lower than long strike for Bear Call Spread
                            continue
                        key_s = (short_k, 'CE')
                        key_l = (long_k, 'CE')

                    if key_s not in trajectories or key_l not in trajectories:
                        continue

                    traj_s = trajectories[key_s]
                    traj_l = trajectories[key_l]

                    # Find entry index
                    idx_matches = np.where(traj_s['times'] == entry_ts)[0]
                    if len(idx_matches) == 0:
                        continue
                    e_idx_s = idx_matches[0]

                    idx_matches_l = np.where(traj_l['times'] == entry_ts)[0]
                    if len(idx_matches_l) == 0:
                        continue
                    e_idx_l = idx_matches_l[0]

                    s_entry_p = float(traj_s['ltp'][e_idx_s])
                    l_entry_p = float(traj_l['ltp'][e_idx_l])
                    net_credit = s_entry_p - l_entry_p

                    # Net credit filter: Must be at least 3.0 points to be worth trading
                    if net_credit <= 3.0:
                        continue

                    # Minute-by-minute execution tracking
                    tgt_thresh = net_credit * (1.0 - tgt_pct)
                    sl_thresh = net_credit * (1.0 + sl_pct)

                    exit_cost = None
                    exit_reason = None
                    exit_ts = None

                    ts_1515_np = np.datetime64(f"{dt}T15:15:00")
                    times_s = traj_s['times']
                    ltp_s = traj_s['ltp']
                    ltp_l = traj_l['ltp']
                    n_track = min(len(ltp_s), len(ltp_l))

                    for i in range(e_idx_s + 1, n_track):
                        curr_ts = times_s[i]
                        curr_cost = float(ltp_s[i] - ltp_l[i])

                        # 1. Stop-Loss Hit
                        if curr_cost >= sl_thresh:
                            exit_cost = curr_cost
                            exit_reason = f"SL_HIT (-{int(sl_pct*100)}%)"
                            exit_ts = pd.Timestamp(curr_ts)
                            break

                        # 2. Profit Target Hit
                        if curr_cost <= tgt_thresh:
                            exit_cost = curr_cost
                            exit_reason = f"TARGET (+{int(tgt_pct*100)}%)"
                            exit_ts = pd.Timestamp(curr_ts)
                            break

                        # 3. 15:15 Square-off
                        if curr_ts >= ts_1515_np:
                            exit_cost = curr_cost
                            exit_reason = "TIME_1515"
                            exit_ts = pd.Timestamp(curr_ts)
                            break

                    if exit_cost is None:
                        exit_cost = float(ltp_s[-1] - ltp_l[-1])
                        exit_reason = "CLOSE_EXIT"
                        exit_ts = pd.Timestamp(times_s[-1])

                    # Calculate PnL
                    points_pnl = net_credit - exit_cost
                    gross_pnl = points_pnl * lot_size
                    # Kotak Zero Brokerage: Rs. 0 + 0.15% slippage on entry and exit turnover
                    turnover = (s_entry_p + l_entry_p + abs(exit_cost)) * lot_size
                    slippage = turnover * 0.0015
                    net_pnl = gross_pnl - slippage

                    daily_trades += 1
                    last_exit_ts = exit_ts

                    trades.append({
                        'date': dt,
                        'config': cname,
                        'spread_type': spread_type,
                        'rr_profile': tsl_label,
                        'entry_ts': entry_ts,
                        'exit_ts': exit_ts,
                        'short_k': short_k,
                        'long_k': long_k,
                        'net_credit': net_credit,
                        'exit_cost': exit_cost,
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

            ret_dd_ratio = tot_pnl / (max_dd * 2000.0) if max_dd > 0 else 0.0

            results_master.append({
                'Setting': cname,
                'Description': cfg['desc'],
                'Target_SL': tsl_label,
                'Total_Trades': tot_trades,
                'Win_Rate_Pct': win_rate,
                'Gross_PnL_Rs': gross_tot,
                'Slippage_Rs': slippage_tot,
                'Net_PnL_Rs': tot_pnl,
                'Profit_Factor': profit_factor,
                'Max_DD_Pct': max_dd,
                'Ret_DD_Ratio': ret_dd_ratio,
                'Pos_Months': f"{pos_m}/{tot_m}",
                'Monthly_WR_Pct': monthly_wr
            })

            if ret_dd_ratio > best_ret_dd:
                best_ret_dd = ret_dd_ratio
                champion_trades = tdf.copy()

    print(f"   Evaluated all 30 Scenarios in {time.time()-t_sim0:.2f}s!\n")

    # Convert results to DataFrame and save
    res_df = pd.DataFrame(results_master)
    res_path = Path("results/triple_st_cs_10settings_all.csv")
    res_df.to_csv(res_path, index=False)
    print(f"Simulation completed! Saved full 30-scenario matrix to {res_path}.\n")

    # ----------------------------------------------------
    # REPORT SECTION 1: TOP 10 SETTINGS LEADERBOARD
    # ----------------------------------------------------
    print("=" * 115)
    print("                 TOP 10 TRIPLE SUPERTREND CREDIT SPREAD SETTINGS (RANKED BY RETURN-TO-DD)                 ")
    print("=" * 115)
    print(f"{'Rank':<5} | {'Setting Name':<22} | {'Target / SL':<18} | {'Trades':<7} | {'Win Rate %':<10} | {'Net PnL (Rs)':<14} | {'PF':<6} | {'Max DD (%)':<10} | {'Monthly WR %':<12}")
    print("-" * 115)
    top10 = res_df.sort_values(by='Ret_DD_Ratio', ascending=False).head(10).reset_index(drop=True)
    for idx, r in top10.iterrows():
        print(f"#{idx+1:<4} | {r['Setting']:<22} | {r['Target_SL']:<18} | {r['Total_Trades']:<7} | {r['Win_Rate_Pct']:>8.1f}% | Rs. {r['Net_PnL_Rs']:>10,.2f} | {r['Profit_Factor']:>4.2f} | {r['Max_DD_Pct']:>8.2f}% | {r['Monthly_WR_Pct']:>10.1f}%")
    print("=" * 115 + "\n")

    # ----------------------------------------------------
    # REPORT SECTION 2: TARGET & SL SENSITIVITY SUMMARY
    # ----------------------------------------------------
    print("=" * 115)
    print("             TARGET & STOP-LOSS SENSITIVITY AUDIT: 'SL CHOTA HO, TARGET BADA HO'             ")
    print("=" * 115)
    print(f"{'Target & SL Profile':<22} | {'Avg Trades':<12} | {'Win Rate %':<12} | {'Avg Net PnL (Rs)':<18} | {'Avg PF':<8} | {'Avg Max DD (%)':<14}")
    print("-" * 115)
    tsl_summary = res_df.groupby('Target_SL').agg({
        'Total_Trades': 'mean',
        'Win_Rate_Pct': 'mean',
        'Net_PnL_Rs': 'mean',
        'Profit_Factor': 'mean',
        'Max_DD_Pct': 'mean'
    })
    for tsl, r in tsl_summary.iterrows():
        print(f"{tsl:<22} | {r['Total_Trades']:>10.1f}   | {r['Win_Rate_Pct']:>10.1f}% | Rs. {r['Net_PnL_Rs']:>14,.2f} | {r['Profit_Factor']:>6.2f} | {r['Max_DD_Pct']:>12.2f}%")
    print("=" * 115 + "\n")

    # ----------------------------------------------------
    # REPORT SECTION 3: BULL PUT SPREAD vs BEAR CALL SPREAD DEEP AUDIT FOR CHAMPION
    # ----------------------------------------------------
    if not champion_trades.empty:
        champ_path = Path("results/triple_st_cs_champion_trades.csv")
        champion_trades.to_csv(champ_path, index=False)
        print(f"Saved Champion Setting trade ledger to {champ_path}.\n")

        champ_setting = champion_trades['config'].iloc[0]
        champ_rr = champion_trades['rr_profile'].iloc[0]

        print("=" * 115)
        print(f"      CHAMPION SETTING FORENSIC: {champ_setting} ({champ_rr})      ")
        print("=" * 115)
        print(f"{'Spread Type':<22} | {'Trades':<8} | {'Win Rate %':<12} | {'Gross PnL (Rs)':<16} | {'Slippage (Rs)':<16} | {'Net PnL (Rs)':<16}")
        print("-" * 115)
        for stype, s_df in champion_trades.groupby('spread_type'):
            s_trades = len(s_df)
            s_wr = (s_df['net_pnl'] > 0).mean() * 100.0
            s_gross = s_df['gross_pnl'].sum()
            s_slip = (s_df['gross_pnl'] - s_df['net_pnl']).sum()
            s_net = s_df['net_pnl'].sum()
            print(f"{stype:<22} | {s_trades:>6d}   | {s_wr:>10.1f}% | Rs. {s_gross:>12,.2f} | Rs. {s_slip:>12,.2f} | Rs. {s_net:>12,.2f}")
        print("-" * 115)
        tot_tr = len(champion_trades)
        tot_wr = (champion_trades['net_pnl'] > 0).mean() * 100.0
        tot_gross = champion_trades['gross_pnl'].sum()
        tot_slip = (champion_trades['gross_pnl'] - champion_trades['net_pnl']).sum()
        tot_net = champion_trades['net_pnl'].sum()
        print(f"{'TOTAL':<22} | {tot_tr:>6d}   | {tot_wr:>10.1f}% | Rs. {tot_gross:>12,.2f} | Rs. {tot_slip:>12,.2f} | Rs. {tot_net:>12,.2f}")
        print("=" * 115 + "\n")

        # Year-by-Year breakdown
        print("=" * 115)
        print(f"                  YEAR-BY-YEAR PnL BREAKDOWN FOR CHAMPION ({champ_setting})                  ")
        print("=" * 115)
        print(f"{'Year':<8} | {'Trades':<8} | {'Win Rate %':<12} | {'Gross PnL (Rs)':<16} | {'Slippage (Rs)':<16} | {'Net PnL (Rs)':<16}")
        print("-" * 115)
        champion_trades['year'] = pd.to_datetime(champion_trades['date']).dt.year
        for yr, y_df in champion_trades.groupby('year'):
            y_trades = len(y_df)
            y_wr = (y_df['net_pnl'] > 0).mean() * 100.0
            y_gross = y_df['gross_pnl'].sum()
            y_slip = (y_df['gross_pnl'] - y_df['net_pnl']).sum()
            y_net = y_df['net_pnl'].sum()
            print(f"{yr:<8} | {y_trades:>6d}   | {y_wr:>10.1f}% | Rs. {y_gross:>12,.2f} | Rs. {y_slip:>12,.2f} | Rs. {y_net:>12,.2f}")
        print("-" * 115)
        print(f"{'TOTAL':<8} | {tot_tr:>6d}   | {tot_wr:>10.1f}% | Rs. {tot_gross:>12,.2f} | Rs. {tot_slip:>12,.2f} | Rs. {tot_net:>12,.2f}")
        print("=" * 115 + "\n")

        # Month-by-Month breakdown
        print("=" * 115)
        print(f"                 MONTH-BY-MONTH PnL BREAKDOWN FOR CHAMPION ({champ_setting})                 ")
        print("=" * 115)
        print(f"{'Month':<10} | {'Trades':<8} | {'Wins':<6} | {'Losses':<8} | {'Win Rate %':<12} | {'Monthly Net PnL (Rs)':<22} | {'Status':<10}")
        print("-" * 115)
        champion_trades['month'] = pd.to_datetime(champion_trades['date']).dt.to_period('M')
        for m, m_df in champion_trades.groupby('month'):
            m_tr = len(m_df)
            m_w = (m_df['net_pnl'] > 0).sum()
            m_l = (m_df['net_pnl'] < 0).sum()
            m_wr = (m_w / m_tr) * 100.0 if m_tr > 0 else 0.0
            m_net = m_df['net_pnl'].sum()
            status = "[PROFIT]" if m_net >= 0 else "[LOSS]"
            print(f"{str(m):<10} | {m_tr:>6d}   | {m_w:>4d} | {m_l:>6d}   | {m_wr:>10.1f}% | Rs. {m_net:>18,.2f} | {status:<10}")
        print("=" * 115)

if __name__ == "__main__":
    run_study()
