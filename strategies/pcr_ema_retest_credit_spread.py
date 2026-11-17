"""
========================================================================================
QUANT STRATEGY: PCR + 50 EMA & 9 EMA RETEST OPTION SELLING STRATEGY
========================================================================================
Comprehensive Multi-Timeframe (5m vs 15m vs 30m) & Multi-Expiry (Weekly vs Monthly)
Optimization & Monthly Consistency Audit.

Core Rules & Logic:
1. Signal Setup (Spot Index):
   - EMA 50 (Trend Baseline), EMA 20 (Intermediate), EMA 9 (Dynamic Momentum/Retest).
   - Bullish Trend: Spot Close > EMA 50 AND EMA 9 >= EMA 20.
     - Retest: Low <= EMA 9 AND Close > EMA 9 AND Close >= Open (Green Candle).
     - Without Retest: Close > EMA 9 AND Close >= Open (Green Candle).
   - Bearish Trend: Spot Close < EMA 50 AND EMA 9 <= EMA 20.
     - Retest: High >= EMA 9 AND Close < EMA 9 AND Close <= Open (Red Candle).
     - Without Retest: Close < EMA 9 AND Close <= Open (Red Candle).
2. PCR (Put-Call Ratio) Filter:
   - Extracted from real-time 1-min Open Interest (PE OI / CE OI).
   - Bullish Confirmation: PCR >= 1.0 (Put writers dominant).
   - Bearish Confirmation: PCR <= 1.0 (Call writers dominant).
3. Option Sizing & Structure:
   - 1 Lot (SEBI historical transitions via utils.nifty_lot_size.get_nifty_lot_size).
   - Credit Spread Architecture: Sell 35-Delta, Buy 15-Delta Hedge.
4. Execution Window:
   - 09:20:00 to 14:30:00 IST.
   - Max 2 trades per day (1 active position at a time).
   - Forced EOD square-off at 15:15:00.
5. Target & SL Matrix:
   - 40% Tgt / 80% SL, 50% Tgt / 80% SL, 50% Tgt / 100% SL, 40% Tgt / 60% SL.
   - Breakeven trailing at +25% profit.
   - Kotak Neo Rs. 0 Brokerage + 0.15% Turnover Slippage.
6. Monthly Goal:
   - Auditing every single month to find setups delivering >= Rs. 3,000 / month on 1 Lot.
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

def run_study():
    t_start = time.time()
    print("=" * 115)
    print("      QUANT RESEARCH: PCR + 50 EMA & 9 EMA RETEST OPTION SELLING COMPREHENSIVE STUDY      ")
    print("=" * 115)
    print("Capital:               Rs. 2,00,000.00 (Margin ~Rs. 45,000 per lot)")
    print("Position Sizing:       1 Lot (Exact SEBI Historical Transitions)")
    print("Execution Window:      09:20:00 to 14:30:00 (Entries), 15:15:00 (Exit)")
    print("Max Entries:           Max 2 Trades per Day (1 active position at a time)")
    print("Brokerage & Slippage:  Kotak Neo Rs. 0 Brokerage + 0.15% Turnover Slippage")
    print("Timeframes Tested:     5-Minute, 15-Minute, 30-Minute")
    print("Modes Tested:          WITH RETEST vs WITHOUT RETEST")
    print("PCR Filters:           Active PCR Filter (>=1.0 / <=1.0) vs Strict PCR vs No PCR")
    print("Expiries Tested:       Weekly Expiry vs Monthly Expiry")
    print("Target/SL Grid:        40/80, 50/80, 50/100, 40/60 (with Breakeven Trailing @ +25%)")
    print("Target Benchmark:      Monthly Net Profit >= Rs. 3,000 per Lot")
    print("=" * 115 + "\n")

    # 1. Load Spot Data
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    print("1. Loading Master Spot Data...")
    df_spot = pd.read_parquet(spot_path)
    print(f"   Loaded {len(df_spot):,} spot 1-min bars.\n")

    # Compute rolling volatility on Daily bars for accurate Black-Scholes delta calculation
    df_daily = df_spot.resample('1D').agg({'close': 'last'}).dropna()
    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = df_daily['returns'].rolling(30).std() * np.sqrt(252)
    df_daily['rolling_vol'] = df_daily['rolling_vol'].bfill().fillna(0.18)
    daily_vol_map = {d.date(): float(v) for d, v in df_daily['rolling_vol'].items()}

    # Resample Spot for 5m, 15m, 30m
    print("2. Resampling Spot into 5m, 15m, and 30m Bars & Calculating 9, 20, 50 EMAs...")
    tf_data = {}
    timeframes = [
        ('5m', '5min', '15min', pd.Timedelta(minutes=5)),
        ('15m', '15min', '15min', pd.Timedelta(minutes=15)),
        ('30m', '30min', '15min', pd.Timedelta(minutes=30))
    ]

    for tf_name, rule, offset, bar_delta in timeframes:
        df_tf = df_spot.resample(rule, origin='start_day', offset=offset).agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
        }).dropna()
        
        df_tf['ema9'] = df_tf['close'].ewm(span=9, adjust=False).mean()
        df_tf['ema20'] = df_tf['close'].ewm(span=20, adjust=False).mean()
        df_tf['ema50'] = df_tf['close'].ewm(span=50, adjust=False).mean()
        
        df_tf['range'] = df_tf['high'] - df_tf['low']
        df_tf['is_green'] = df_tf['close'] >= df_tf['open']
        df_tf['is_red'] = df_tf['close'] <= df_tf['open']
        df_tf['close_time'] = df_tf.index + bar_delta
        df_tf['close_time_t'] = df_tf['close_time'].dt.time
        
        # Bullish Trend
        bull_trend = (df_tf['close'] > df_tf['ema50']) & (df_tf['ema9'] >= df_tf['ema20'])
        # Bullish Retest: Low touched or penetrated 9 EMA, Close rebounded above 9 EMA, Green candle
        df_tf['bull_retest'] = bull_trend & (df_tf['low'] <= df_tf['ema9']) & (df_tf['close'] > df_tf['ema9']) & df_tf['is_green']
        # Bullish Without Retest (Direct Continuation)
        df_tf['bull_direct'] = bull_trend & (df_tf['close'] > df_tf['ema9']) & df_tf['is_green']
        
        # Bearish Trend
        bear_trend = (df_tf['close'] < df_tf['ema50']) & (df_tf['ema9'] <= df_tf['ema20'])
        # Bearish Retest: High touched or penetrated 9 EMA, Close rejected below 9 EMA, Red candle
        df_tf['bear_retest'] = bear_trend & (df_tf['high'] >= df_tf['ema9']) & (df_tf['close'] < df_tf['ema9']) & df_tf['is_red']
        # Bearish Without Retest (Direct Continuation)
        df_tf['bear_direct'] = bear_trend & (df_tf['close'] < df_tf['ema9']) & df_tf['is_red']
        
        # Filter for entry window: 09:20:00 to 14:30:00
        time_mask = (df_tf['close_time_t'] >= pd.Timestamp('09:20:00').time()) & (df_tf['close_time_t'] <= pd.Timestamp('14:30:00').time())
        df_tf_sub = df_tf[time_mask].copy()
        
        tf_data[tf_name] = df_tf_sub
        print(f"   {tf_name}: {len(df_tf_sub):,} bars in 09:20-14:30 window | Bull Retest: {df_tf_sub['bull_retest'].sum():,} | Bear Retest: {df_tf_sub['bear_retest'].sum():,}")

    print("\n3. Loading Real Options Vault & Computing Real-Time PCR...")
    clean_path = Path("data/nifty_option_chain_1min_clean.parquet")
    t0_opt = time.time()
    
    # Read table with necessary columns
    opt_table = pq.read_table(clean_path, columns=[
        'timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'delta', 'underlying_spot_price', 'oi'
    ])
    df_opt = opt_table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['expiry_date'] = df_opt['expiry'].dt.date
    
    all_dates = sorted(df_opt['date'].unique())
    print(f"   Loaded {len(df_opt):,} option rows across {len(all_dates)} trading sessions in {time.time()-t0_opt:.2f}s!")

    # 4. Compute Real-Time Minute PCR Map
    print("   Computing 1-minute real-time PCR (Total Put OI / Total Call OI)...")
    t0_pcr = time.time()
    oi_agg = df_opt.groupby(['timestamp', 'option_type'])['oi'].sum().unstack(fill_value=0)
    oi_agg['pcr'] = oi_agg['PE'] / oi_agg['CE'].replace(0, 1)
    pcr_map = oi_agg['pcr'].to_dict()
    print(f"   Precomputed PCR across {len(pcr_map):,} timestamps in {time.time()-t0_pcr:.2f}s!\n")

    # 5. Build Daily Trajectory Cache for both Weekly and Monthly Expiries
    print("4. Pre-indexing Daily Trajectories for Weekly & Monthly Expiries...")
    t_idx0 = time.time()
    daily_opt_cache = {}

    for dt, day_df in df_opt.groupby('date'):
        expiries = sorted([exp for exp in day_df['expiry_date'].unique() if exp > dt])
        if not expiries:
            continue
        
        # Nearest Weekly Expiry
        weekly_exp = expiries[0]
        
        # Monthly Expiry: Last Thursday of current month
        same_month_exps = [e for e in expiries if e.year == dt.year and e.month == dt.month]
        if same_month_exps:
            monthly_exp = same_month_exps[-1]
        else:
            monthly_exp = expiries[-1]
            
        def build_exp_cache(exp_target):
            exp_df = day_df[day_df['expiry_date'] == exp_target]
            ts_spot = dict(exp_df[['timestamp', 'underlying_spot_price']].drop_duplicates('timestamp').values)
            trajs = {}
            for (stk, otype), grp in exp_df.groupby(['strike_price', 'option_type']):
                grp_sorted = grp.sort_values('timestamp')
                trajs[(int(stk), otype)] = {
                    'times': grp_sorted['timestamp'].to_numpy(),
                    'ltp': grp_sorted['ltp'].fillna(0.0).to_numpy()
                }
            return {
                'exp': exp_target,
                'ts_spot': ts_spot,
                'trajs': trajs,
                'pe_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'PE']),
                'ce_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'CE'])
            }
            
        daily_opt_cache[dt] = {
            'weekly': build_exp_cache(weekly_exp),
            'monthly': build_exp_cache(monthly_exp)
        }

    print(f"   Indexed {len(daily_opt_cache)} trading sessions into memory in {time.time()-t_idx0:.2f}s!\n")

    # 6. Simulation Engine
    print("5. Executing Systematic Backtest across All Scenarios...")
    
    tsl_grid = [
        {"tgt": 0.40, "sl": 0.80, "name": "40% Tgt / 80% SL"},
        {"tgt": 0.50, "sl": 0.80, "name": "50% Tgt / 80% SL"},
        {"tgt": 0.50, "sl": 1.00, "name": "50% Tgt / 100% SL"},
        {"tgt": 0.40, "sl": 0.60, "name": "40% Tgt / 60% SL"}
    ]

    configs_to_run = [
        # --- 15-Minute Base & Retest Variants ---
        {"tf": "15m", "mode": "Retest",         "pcr": "Active_PCR", "exp": "Weekly",  "desc": "15m Retest + Active PCR (Weekly)"},
        {"tf": "15m", "mode": "Without_Retest", "pcr": "Active_PCR", "exp": "Weekly",  "desc": "15m Without Retest + Active PCR (Weekly)"},
        {"tf": "15m", "mode": "Retest",         "pcr": "Strict_PCR", "exp": "Weekly",  "desc": "15m Retest + Strict PCR (Weekly)"},
        {"tf": "15m", "mode": "Retest",         "pcr": "No_PCR",     "exp": "Weekly",  "desc": "15m Retest [NO PCR Baseline]"},
        
        # --- 15-Minute Monthly Expiry ---
        {"tf": "15m", "mode": "Retest",         "pcr": "Active_PCR", "exp": "Monthly", "desc": "15m Retest + Active PCR (MONTHLY Expiry)"},
        {"tf": "15m", "mode": "Without_Retest", "pcr": "Active_PCR", "exp": "Monthly", "desc": "15m Without Retest + Active PCR (MONTHLY Expiry)"},

        # --- 5-Minute Timeframe Variants ---
        {"tf": "5m",  "mode": "Retest",         "pcr": "Active_PCR", "exp": "Weekly",  "desc": "5m Retest + Active PCR (Weekly)"},
        {"tf": "5m",  "mode": "Without_Retest", "pcr": "Active_PCR", "exp": "Weekly",  "desc": "5m Without Retest + Active PCR (Weekly)"},

        # --- 30-Minute Timeframe Variants ---
        {"tf": "30m", "mode": "Retest",         "pcr": "Active_PCR", "exp": "Weekly",  "desc": "30m Retest + Active PCR (Weekly)"},
        {"tf": "30m", "mode": "Without_Retest", "pcr": "Active_PCR", "exp": "Weekly",  "desc": "30m Without Retest + Active PCR (Weekly)"},
    ]

    all_results = []
    trade_ledgers = {}

    def simulate(cfg, tsl):
        tf = cfg['tf']
        mode = cfg['mode']
        pcr_type = cfg['pcr']
        exp_type = cfg['exp']
        tgt_pct = tsl['tgt']
        sl_pct = tsl['sl']
        
        df_bars = tf_data[tf]
        trades = []
        
        for dt in all_dates:
            if dt not in daily_opt_cache:
                continue
            day_cache = daily_opt_cache[dt][exp_type.lower()]
            trajs = day_cache['trajs']
            pe_strikes = day_cache['pe_strikes']
            ce_strikes = day_cache['ce_strikes']
            ts_spot = day_cache['ts_spot']
            target_exp = day_cache['exp']
            lot_size = get_nifty_lot_size(dt)
            
            day_bars = df_bars[df_bars.index.date == dt]
            if day_bars.empty:
                continue
                
            daily_trades = 0
            in_position = False
            pos_exit_time = None
            
            dte_days = (target_exp - dt).days
            dte_years = max(dte_days / 365.25, 0.001)
            sigma = daily_vol_map.get(dt, 0.16)

            for bar_ts, bar in day_bars.iterrows():
                if daily_trades >= 2:
                    break
                    
                entry_exec_time = bar['close_time'] + pd.Timedelta(minutes=1)
                if in_position and pos_exit_time is not None and entry_exec_time < pos_exit_time:
                    continue
                else:
                    in_position = False
                    
                # Determine Signal
                is_bull = False
                is_bear = False
                
                if mode == 'Retest':
                    is_bull = bool(bar['bull_retest'])
                    is_bear = bool(bar['bear_retest'])
                else:
                    is_bull = bool(bar['bull_direct'])
                    is_bear = bool(bar['bear_direct'])
                    
                if not is_bull and not is_bear:
                    continue
                    
                # PCR Filter Check at bar close
                bar_close_ts = bar['close_time']
                curr_pcr = pcr_map.get(bar_close_ts, 1.0)
                
                if pcr_type == 'Active_PCR':
                    if is_bull and curr_pcr < 1.0:
                        continue # Put writers not dominant
                    if is_bear and curr_pcr > 1.0:
                        continue # Call writers not dominant
                elif pcr_type == 'Strict_PCR':
                    if is_bull and curr_pcr < 1.10:
                        continue
                    if is_bear and curr_pcr > 0.90:
                        continue
                        
                # Determine Strategy Legs: Bull Put or Bear Call
                strat_type = "BULL_PUT_SPREAD" if is_bull else "BEAR_CALL_SPREAD"
                otype = "PE" if is_bull else "CE"
                available_strikes = pe_strikes if is_bull else ce_strikes
                if len(available_strikes) < 2:
                    continue
                    
                if entry_exec_time not in ts_spot:
                    continue
                spot_entry = float(ts_spot[entry_exec_time])
                
                # Fast Vectorized Black-Scholes Delta Strike Selection
                deltas = calc_bs_deltas(spot_entry, np.array(available_strikes), dte_years, 0.07, sigma, otype)
                
                if is_bull:
                    # Bull Put Spread: Target Short ~ -0.35, Target Long ~ -0.15
                    idx_35 = np.argmin(np.abs(deltas - (-0.35)))
                    idx_15 = np.argmin(np.abs(deltas - (-0.15)))
                    short_k = available_strikes[idx_35]
                    long_k = available_strikes[idx_15]
                    if short_k <= long_k:
                        continue
                else:
                    # Bear Call Spread: Target Short ~ 0.35, Target Long ~ 0.15
                    idx_35 = np.argmin(np.abs(deltas - 0.35))
                    idx_15 = np.argmin(np.abs(deltas - 0.15))
                    short_k = available_strikes[idx_35]
                    long_k = available_strikes[idx_15]
                    if short_k >= long_k:
                        continue
                        
                key_s = (short_k, otype)
                key_l = (long_k, otype)
                if key_s not in trajs or key_l not in trajs:
                    continue
                    
                traj_s = trajs[key_s]
                traj_l = trajs[key_l]
                
                # Check entry execution timestamp match
                idx_matches_s = np.where(traj_s['times'] == entry_exec_time)[0]
                idx_matches_l = np.where(traj_l['times'] == entry_exec_time)[0]
                
                if len(idx_matches_s) == 0 or len(idx_matches_l) == 0:
                    continue
                    
                e_idx_s = idx_matches_s[0]
                e_idx_l = idx_matches_l[0]
                
                entry_short_price = float(traj_s['ltp'][e_idx_s])
                entry_long_price = float(traj_l['ltp'][e_idx_l])
                
                if entry_short_price <= 0.5 or entry_long_price <= 0.05:
                    continue
                net_credit = entry_short_price - entry_long_price
                if net_credit <= 0.5:
                    continue
                    
                # Slice Trajectories from entry to 15:15:00
                times_sub_s = traj_s['times'][e_idx_s:]
                times_sub_l = traj_l['times'][e_idx_l:]
                ltp_sub_s = traj_s['ltp'][e_idx_s:]
                ltp_sub_l = traj_l['ltp'][e_idx_l:]
                
                s_dict = dict(zip(times_sub_s, ltp_sub_s))
                common_times = [t for t in times_sub_l if t in s_dict]
                if not common_times:
                    continue
                    
                # Walk forward minute by minute
                target_val = net_credit * (1.0 - tgt_pct)
                current_sl_val = net_credit * (1.0 + sl_pct)
                be_active = False # Breakeven trailing flag
                
                exit_time = None
                exit_short_p = None
                exit_long_p = None
                exit_reason = None
                
                for ct in common_times:
                    ct_time = pd.Timestamp(ct).time()
                    curr_s = s_dict[ct]
                    # Direct lookup from long dict or search
                    l_idx = np.where(traj_l['times'] == ct)[0]
                    if len(l_idx) == 0:
                        continue
                    curr_l = traj_l['ltp'][l_idx[0]]
                    curr_spread = curr_s - curr_l
                    
                    # Check Breakeven activation: reached +25% profit
                    if not be_active and curr_spread <= (net_credit * 0.75):
                        be_active = True
                        current_sl_val = net_credit # Move SL to entry price
                        
                    # Check Target Hit
                    if curr_spread <= target_val:
                        exit_time = ct
                        exit_short_p = curr_s
                        exit_long_p = curr_l
                        exit_reason = "TARGET"
                        break
                        
                    # Check Stop Loss Hit
                    if curr_spread >= current_sl_val:
                        exit_time = ct
                        exit_short_p = curr_s
                        exit_long_p = curr_l
                        exit_reason = "BREAKEVEN_SL" if be_active else "STOP_LOSS"
                        break
                        
                    # Intraday Time Exit @ 15:15:00
                    if ct_time >= pd.Timestamp('15:15:00').time():
                        exit_time = ct
                        exit_short_p = curr_s
                        exit_long_p = curr_l
                        exit_reason = "TIME_EXIT"
                        break
                        
                if exit_time is None:
                    last_t = common_times[-1]
                    exit_time = last_t
                    exit_short_p = s_dict[last_t]
                    l_idx_last = np.where(traj_l['times'] == last_t)[0]
                    exit_long_p = traj_l['ltp'][l_idx_last[0]] if len(l_idx_last) > 0 else 0.0
                    exit_reason = "EOD_CLOSE"
                    
                exit_spread = exit_short_p - exit_long_p
                spread_pnl_pts = net_credit - exit_spread
                gross_pnl = spread_pnl_pts * lot_size
                
                # Turnover slippage: 0.15% on entry turnover + exit turnover
                entry_turnover = (entry_short_price + entry_long_price) * lot_size
                exit_turnover = (exit_short_p + exit_long_p) * lot_size
                slippage_cost = (entry_turnover + exit_turnover) * 0.0015
                net_pnl = gross_pnl - slippage_cost
                
                trades.append({
                    'date': dt,
                    'year_month': f"{dt.year}-{dt.month:02d}",
                    'entry_time': entry_exec_time,
                    'exit_time': exit_time,
                    'type': strat_type,
                    'short_strike': short_k,
                    'long_strike': long_k,
                    'entry_credit': net_credit,
                    'exit_spread': exit_spread,
                    'pnl_pts': spread_pnl_pts,
                    'lot_size': lot_size,
                    'gross_pnl': gross_pnl,
                    'slippage': slippage_cost,
                    'net_pnl': net_pnl,
                    'exit_reason': exit_reason,
                    'pcr_at_signal': curr_pcr
                })
                
                daily_trades += 1
                in_position = True
                pos_exit_time = exit_time
                
        # Metrics Calculation
        if not trades:
            return None, []
            
        tdf = pd.DataFrame(trades)
        total_trades = len(tdf)
        wins = tdf[tdf['net_pnl'] > 0]
        losses = tdf[tdf['net_pnl'] <= 0]
        win_rate = (len(wins) / total_trades) * 100.0
        
        gross_pnl_sum = tdf['gross_pnl'].sum()
        slippage_sum = tdf['slippage'].sum()
        net_pnl_sum = tdf['net_pnl'].sum()
        
        gross_win = wins['net_pnl'].sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses['net_pnl'].sum()) if len(losses) > 0 else 1.0
        pf = gross_win / gross_loss if gross_loss > 0 else 99.0
        
        # Monthly Analysis
        monthly_grp = tdf.groupby('year_month')['net_pnl'].sum()
        total_months = len(monthly_grp)
        pos_months = (monthly_grp > 0).sum()
        target_3k_months = (monthly_grp >= 3000.0).sum()
        monthly_wr = (pos_months / total_months) * 100.0 if total_months > 0 else 0.0
        pct_3k = (target_3k_months / total_months) * 100.0 if total_months > 0 else 0.0
        min_monthly_pnl = monthly_grp.min()
        max_monthly_pnl = monthly_grp.max()
        avg_monthly_pnl = monthly_grp.mean()
        
        # Drawdown
        tdf['cum_pnl'] = tdf['net_pnl'].cumsum()
        tdf['equity'] = 200000.0 + tdf['cum_pnl']
        peak = tdf['equity'].cummax()
        dd_pts = peak - tdf['equity']
        dd_pct = (dd_pts / peak).max() * 100.0
        ret_dd = (net_pnl_sum / (200000.0 * (dd_pct / 100.0))) if dd_pct > 0 else 0.0
        
        summary = {
            'Config_Desc': cfg['desc'],
            'Timeframe': tf,
            'Mode': mode,
            'PCR_Filter': pcr_type,
            'Expiry_Type': exp_type,
            'Target_SL': tsl['name'],
            'Total_Trades': total_trades,
            'Win_Rate_Pct': win_rate,
            'Net_PnL_Rs': net_pnl_sum,
            'Slippage_Rs': slippage_sum,
            'Profit_Factor': pf,
            'Max_DD_Pct': dd_pct,
            'Ret_DD_Ratio': ret_dd,
            'Total_Months': total_months,
            'Pos_Months': f"{pos_months}/{total_months}",
            'Monthly_WR_Pct': monthly_wr,
            'Target_3k_Months': f"{target_3k_months}/{total_months} ({pct_3k:.1f}%)",
            'Num_3k_Months': target_3k_months,
            'Min_Month_PnL': min_monthly_pnl,
            'Max_Month_PnL': max_monthly_pnl,
            'Avg_Month_PnL': avg_monthly_pnl
        }
        
        return summary, trades

    print("   Running simulation across all configuration combinations...")
    t_sim_start = time.time()
    
    for cfg in configs_to_run:
        for tsl in tsl_grid:
            summary, trades = simulate(cfg, tsl)
            if summary:
                all_results.append(summary)
                key = f"{cfg['desc']} | {tsl['name']}"
                trade_ledgers[key] = trades

    print(f"   Completed all {len(all_results)} simulations in {time.time()-t_sim_start:.2f}s!\n")

    # Save Results
    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values(by=['Num_3k_Months', 'Net_PnL_Rs', 'Ret_DD_Ratio'], ascending=[False, False, False])
    results_csv_path = Path("results/pcr_ema_retest_all_scenarios.csv")
    results_df.to_csv(results_csv_path, index=False)
    print(f"6. Saved complete simulation results to {results_csv_path}")

    # Print Top 10 Configurations Leaderboard
    print("\n" + "=" * 135)
    print("                    TOP 10 CONFIGURATIONS LEADERBOARD (RANKED BY MONTHLY CONSISTENCY & NET PNL)                    ")
    print("=" * 135)
    cols_show = ['Config_Desc', 'Target_SL', 'Total_Trades', 'Win_Rate_Pct', 'Net_PnL_Rs', 'Profit_Factor', 'Max_DD_Pct', 'Pos_Months', 'Target_3k_Months', 'Min_Month_PnL', 'Avg_Month_PnL']
    top10 = results_df.head(10)[cols_show]
    print(top10.to_string(index=False))
    print("=" * 135 + "\n")

    # Detailed Analysis of Champion Configuration
    champion = results_df.iloc[0]
    champ_key = f"{champion['Config_Desc']} | {champion['Target_SL']}"
    champ_trades = trade_ledgers[champ_key]
    champ_df = pd.DataFrame(champ_trades)
    
    champ_csv_path = Path("results/pcr_ema_retest_champion_trades.csv")
    champ_df.to_csv(champ_csv_path, index=False)
    print(f"7. Saved Champion Trade Ledger to {champ_csv_path}")

    # Print Month-by-Month Breakdown of Champion
    print("\n" + "=" * 110)
    print(f"            CHAMPION MONTH-BY-MONTH PnL AUDIT: {champ_key}            ")
    print("=" * 110)
    monthly_summary = champ_df.groupby('year_month').agg(
        Trades=('net_pnl', 'count'),
        Wins=('net_pnl', lambda x: (x > 0).sum()),
        Losses=('net_pnl', lambda x: (x <= 0).sum()),
        Gross_PnL=('gross_pnl', 'sum'),
        Slippage=('slippage', 'sum'),
        Net_PnL=('net_pnl', 'sum')
    )
    monthly_summary['Win_Rate_%'] = (monthly_summary['Wins'] / monthly_summary['Trades']) * 100.0
    monthly_summary['Target_>=3k_Achieved'] = monthly_summary['Net_PnL'].apply(lambda x: "YES (+Rs.3K+)" if x >= 3000 else ("PROFITABLE" if x > 0 else "LOSS"))
    
    print(monthly_summary[['Trades', 'Wins', 'Losses', 'Win_Rate_%', 'Net_PnL', 'Target_>=3k_Achieved']].to_string())
    print("=" * 110)
    
    total_net = monthly_summary['Net_PnL'].sum()
    print(f"\nTotal Net PnL: Rs. {total_net:,.2f} | Positive Months: {(monthly_summary['Net_PnL'] > 0).sum()}/{len(monthly_summary)} ({(monthly_summary['Net_PnL'] > 0).sum()/len(monthly_summary)*100:.1f}%)")
    print(f"Months meeting >= Rs. 3,000 Target: {(monthly_summary['Net_PnL'] >= 3000).sum()}/{len(monthly_summary)} ({(monthly_summary['Net_PnL'] >= 3000).sum()/len(monthly_summary)*100:.1f}%)")
    print(f"Study finished in {time.time()-t_start:.2f} seconds.\n")

if __name__ == '__main__':
    run_study()
