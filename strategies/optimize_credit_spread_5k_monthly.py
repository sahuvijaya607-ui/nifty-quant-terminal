"""
========================================================================================
QUANTITATIVE OPTIMIZATION: MINIMUM RS. 5,000 EVERY MONTH ON 1 LOT NIFTY
========================================================================================
Testing Systematic Optimizations to achieve consistent >= Rs. 5,000 Net Profit
every single month on 1 Lot Credit Spreads.

Optimization Vectors Tested:
1. Delta & Strike Width Architecture:
   - Grid A: 35-Delta Short / 15-Delta Long (Baseline)
   - Grid B: 40-Delta Short / 15-Delta Long (Higher Credit Collection)
   - Grid C: 45-Delta Short / 15-Delta Long (Near-ATM Maximum Theta)
   - Grid D: 35-Delta Short / 10-Delta Long (Wider Wing, Lower Hedge Drag)
   - Grid E: 40-Delta Short / 10-Delta Long (Deep Credit Harvester)

2. Profit Target & Dynamic Trailing Engine:
   - T1: 40% Target / 60% SL (Fixed Baseline)
   - T2: 50% Target / 75% SL (Extended Theta)
   - T3: 60% Target / 80% SL (Deep Decay)
   - T4: 50% Target with Breakeven @ +25% and Trail 10%
   - T5: 40% Target / 40% SL (1:1 Symmetric Risk)

3. Trade Frequency & Re-entry Logic:
   - Max 2 trades/day vs Max 3 trades/day on strong trend continuation.

4. Expiry Day Specialization (Thursday 0-DTE Theta Accel):
   - Accelerated decay capture on the 4 weekly expiry days per month.

5. Goal & KPI:
   - Identify which configuration(s) maximize the number of months with Net PnL >= Rs. 5,000.
   - Auditing every single calendar month.
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
    strikes = np.asarray(strikes, dtype=float)
    T = max(float(dte_years), 0.001)
    sig = max(float(sigma), 0.05)
    d1 = (np.log(spot / strikes) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    if option_type == 'CE':
        return si.norm.cdf(d1)
    else:
        return si.norm.cdf(d1) - 1.0

def run_optimization():
    t_start = time.time()
    print("=" * 125)
    print("     QUANTITATIVE OPTIMIZATION ENGINE: TARGETING >= RS. 5,000 NET PROFIT EVERY MONTH (1 LOT)     ")
    print("=" * 125)
    print("Capital:               Rs. 2,00,000.00 (Margin ~Rs. 45,000 - 55,000 per lot)")
    print("Position Sizing:       Strictly 1 Lot (SEBI Transition Schedule)")
    print("Friction Deducted:     Kotak Neo Rs. 0 Brokerage + 0.15% Round-Trip Slippage")
    print("Target Criterion:      Monthly Net Profit >= Rs. 5,000.00")
    print("=" * 125 + "\n")

    # 1. Load Master Spot Data
    print("1. Loading Master Spot Data & Resampling 5-Min Bars...")
    df_spot = pd.read_parquet("data/nifty_spot_master_2020_2026_1min.parquet")
    spot_price_map = dict(zip(df_spot.index, df_spot['close']))

    df_daily = df_spot.resample('1D').agg({'close': 'last'}).dropna()
    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = df_daily['returns'].rolling(30).std() * np.sqrt(252)
    df_daily['rolling_vol'] = df_daily['rolling_vol'].bfill().fillna(0.18)
    daily_vol_map = {d.date(): float(v) for d, v in df_daily['rolling_vol'].items()}

    # Resample Spot into 5m Bars
    df_5m = df_spot.resample('5min', origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_5m['ema9'] = df_5m['close'].ewm(span=9, adjust=False).mean()
    df_5m['ema20'] = df_5m['close'].ewm(span=20, adjust=False).mean()
    df_5m['ema50'] = df_5m['close'].ewm(span=50, adjust=False).mean()
    df_5m['is_green'] = df_5m['close'] >= df_5m['open']
    df_5m['is_red'] = df_5m['close'] <= df_5m['open']
    df_5m['close_time'] = df_5m.index + pd.Timedelta(minutes=5)
    df_5m['close_time_t'] = df_5m['close_time'].dt.time
    
    # 5m Trend Signals
    bull_5m = (df_5m['close'] > df_5m['ema50']) & (df_5m['ema9'] >= df_5m['ema20']) & (df_5m['close'] > df_5m['ema9']) & df_5m['is_green']
    bear_5m = (df_5m['close'] < df_5m['ema50']) & (df_5m['ema9'] <= df_5m['ema20']) & (df_5m['close'] < df_5m['ema9']) & df_5m['is_red']
    df_5m['is_bull'] = bull_5m
    df_5m['is_bear'] = bear_5m
    mask_5m = (df_5m['close_time_t'] >= pd.Timestamp('09:20:00').time()) & (df_5m['close_time_t'] <= pd.Timestamp('14:30:00').time())
    df_5m_sub = df_5m[mask_5m].copy()
    print(f"   5m Bars ready: {len(df_5m_sub):,} bars in 09:20-14:30 window.\n")

    # 2. Load 2024-2026 Options Vault
    print("2. Loading Real Options Vault (2024 to 2026) with Real-Time PCR...")
    clean_path = Path("data/nifty_option_chain_1min_clean.parquet")
    opt_table = pq.read_table(clean_path, columns=[
        'timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'oi'
    ])
    df_opt = opt_table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['expiry_date'] = df_opt['expiry'].dt.date
    all_dates = sorted(df_opt['date'].unique())

    # Precalculate minute-by-minute PCR
    oi_agg = df_opt.groupby(['timestamp', 'option_type'])['oi'].sum().unstack(fill_value=0)
    pcr_map = (oi_agg['PE'] / oi_agg['CE'].replace(0, 1)).to_dict()

    # Pre-index daily trajectories
    print("   Indexing daily trajectories...")
    daily_cache = {}
    for dt, day_df in df_opt.groupby('date'):
        # For weekly expiry: on expiry day, nearest expiry is today (0-DTE) or next week
        # Let's support both 0-DTE (same day expiry if trading on Thursday) and next expiry
        expiries_gte = sorted([exp for exp in day_df['expiry_date'].unique() if exp >= dt])
        expiries_gt = sorted([exp for exp in day_df['expiry_date'].unique() if exp > dt])
        if not expiries_gt:
            continue
            
        target_exp = expiries_gt[0]
        # Expiry day target (if today is expiry, today is target_exp_0dte)
        target_exp_0dte = expiries_gte[0] if expiries_gte else target_exp
        
        def index_exp(target_e):
            exp_df = day_df[day_df['expiry_date'] == target_e]
            trajs = {}
            for (stk, otype), grp in exp_df.groupby(['strike_price', 'option_type']):
                grp_sorted = grp.sort_values('timestamp')
                trajs[(int(stk), otype)] = {
                    'times': grp_sorted['timestamp'].to_numpy(),
                    'ltp': grp_sorted['ltp'].fillna(0.0).to_numpy()
                }
            return {
                'exp': target_e,
                'trajs': trajs,
                'pe_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'PE']),
                'ce_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'CE'])
            }
            
        daily_cache[dt] = {
            'std_weekly': index_exp(target_exp),
            'dte0_weekly': index_exp(target_exp_0dte) if target_exp_0dte == dt else index_exp(target_exp)
        }
    print(f"   Indexed {len(daily_cache)} sessions into memory.\n")

    # 3. Define Comprehensive Optimization Grid
    delta_configs = [
        {"name": "D35_L15", "short_d": 0.35, "long_d": 0.15, "desc": "35Δ Short / 15Δ Long (Standard Baseline)"},
        {"name": "D40_L15", "short_d": 0.40, "long_d": 0.15, "desc": "40Δ Short / 15Δ Long (Aggressive Premium)"},
        {"name": "D45_L15", "short_d": 0.45, "long_d": 0.15, "desc": "45Δ Short / 15Δ Long (Near-ATM Heavy Theta)"},
        {"name": "D35_L10", "short_d": 0.35, "long_d": 0.10, "desc": "35Δ Short / 10Δ Long (Wider Wing, Low Drag)"},
        {"name": "D40_L10", "short_d": 0.40, "long_d": 0.10, "desc": "40Δ Short / 10Δ Long (Max Credit Harvester)"},
    ]

    target_sl_configs = [
        {"name": "40T_60SL", "tgt": 0.40, "sl": 0.60, "be_at": 0.25, "desc": "40% Tgt / 60% SL (BE @ +25%)"},
        {"name": "50T_75SL", "tgt": 0.50, "sl": 0.75, "be_at": 0.25, "desc": "50% Tgt / 75% SL (BE @ +25%)"},
        {"name": "50T_60SL", "tgt": 0.50, "sl": 0.60, "be_at": 0.25, "desc": "50% Tgt / 60% SL (Tight Risk)"},
        {"name": "60T_80SL", "tgt": 0.60, "sl": 0.80, "be_at": 0.30, "desc": "60% Tgt / 80% SL (Deep Decay)"},
        {"name": "40T_40SL", "tgt": 0.40, "sl": 0.40, "be_at": 0.20, "desc": "40% Tgt / 40% SL (1:1 Symmetric)"},
    ]

    max_trades_configs = [2, 3] # Max 2 vs Max 3 trades per day

    results = []
    trade_ledgers = {}

    print("3. Executing Optimization Grid...")
    t_opt0 = time.time()

    for d_cfg in delta_configs:
        for tsl_cfg in target_sl_configs:
            for max_t in max_trades_configs:
                short_target_d = d_cfg['short_d']
                long_target_d = d_cfg['long_d']
                tgt_pct = tsl_cfg['tgt']
                sl_pct = tsl_cfg['sl']
                be_pct = tsl_cfg['be_at']
                
                trades = []
                
                for dt in all_dates:
                    if dt not in daily_cache:
                        continue
                    day = daily_cache[dt]['std_weekly']
                    trajs = day['trajs']
                    pe_strikes = day['pe_strikes']
                    ce_strikes = day['ce_strikes']
                    target_exp = day['exp']
                    lot_size = get_nifty_lot_size(dt)
                    
                    day_bars = df_5m_sub[df_5m_sub.index.date == dt]
                    if day_bars.empty:
                        continue
                        
                    daily_trades = 0
                    in_pos = False
                    pos_exit = None
                    
                    dte_years = max((target_exp - dt).days / 365.25, 0.001)
                    sigma = daily_vol_map.get(dt, 0.16)
                    
                    for bar_ts, bar in day_bars.iterrows():
                        if daily_trades >= max_t:
                            break
                        entry_time = bar['close_time'] + pd.Timedelta(minutes=1)
                        if in_pos and pos_exit is not None and entry_time < pos_exit:
                            continue
                        else:
                            in_pos = False
                            
                        is_bull = bool(bar['is_bull'])
                        is_bear = bool(bar['is_bear'])
                        if not is_bull and not is_bear:
                            continue
                            
                        curr_pcr = pcr_map.get(bar['close_time'], 1.0)
                        if is_bull and curr_pcr < 1.0:
                            continue
                        if is_bear and curr_pcr > 1.0:
                            continue
                            
                        strat_type = "BULL_PUT_SPREAD" if is_bull else "BEAR_CALL_SPREAD"
                        otype = "PE" if is_bull else "CE"
                        available_strikes = pe_strikes if is_bull else ce_strikes
                        if len(available_strikes) < 2:
                            continue
                            
                        if entry_time not in spot_price_map:
                            continue
                        spot_entry = float(spot_price_map[entry_time])
                        
                        deltas = calc_bs_deltas(spot_entry, np.array(available_strikes), dte_years, 0.07, sigma, otype)
                        if is_bull:
                            idx_s = np.argmin(np.abs(deltas - (-short_target_d)))
                            idx_l = np.argmin(np.abs(deltas - (-long_target_d)))
                            short_k = available_strikes[idx_s]
                            long_k = available_strikes[idx_l]
                            if short_k <= long_k:
                                continue
                        else:
                            idx_s = np.argmin(np.abs(deltas - short_target_d))
                            idx_l = np.argmin(np.abs(deltas - long_target_d))
                            short_k = available_strikes[idx_s]
                            long_k = available_strikes[idx_l]
                            if short_k >= long_k:
                                continue
                                
                        key_s = (short_k, otype)
                        key_l = (long_k, otype)
                        if key_s not in trajs or key_l not in trajs:
                            continue
                            
                        traj_s = trajs[key_s]
                        traj_l = trajs[key_l]
                        
                        idx_match_s = np.where(traj_s['times'] == entry_time)[0]
                        idx_match_l = np.where(traj_l['times'] == entry_time)[0]
                        if len(idx_match_s) == 0 or len(idx_match_l) == 0:
                            continue
                            
                        entry_s_p = float(traj_s['ltp'][idx_match_s[0]])
                        entry_l_p = float(traj_l['ltp'][idx_match_l[0]])
                        if entry_s_p <= 0.5 or entry_l_p <= 0.05:
                            continue
                        net_credit = entry_s_p - entry_l_p
                        if net_credit <= 0.5:
                            continue
                            
                        times_sub_s = traj_s['times'][idx_match_s[0]:]
                        times_sub_l = traj_l['times'][idx_match_l[0]:]
                        ltp_sub_s = traj_s['ltp'][idx_match_s[0]:]
                        s_dict = dict(zip(times_sub_s, ltp_sub_s))
                        common_times = [t for t in times_sub_l if t in s_dict]
                        if not common_times:
                            continue
                            
                        tgt_val = net_credit * (1.0 - tgt_pct)
                        current_sl_val = net_credit * (1.0 + sl_pct)
                        be_active = False
                        
                        exit_time = None
                        exit_s_p = None
                        exit_l_p = None
                        exit_reason = None
                        
                        for ct in common_times:
                            ct_t = pd.Timestamp(ct).time()
                            curr_s = s_dict[ct]
                            l_idx = np.where(traj_l['times'] == ct)[0]
                            if len(l_idx) == 0:
                                continue
                            curr_l = traj_l['ltp'][l_idx[0]]
                            curr_spread = curr_s - curr_l
                            
                            if not be_active and curr_spread <= (net_credit * (1.0 - be_pct)):
                                be_active = True
                                current_sl_val = net_credit
                                
                            if curr_spread <= tgt_val:
                                exit_time = ct
                                exit_s_p = curr_s
                                exit_l_p = curr_l
                                exit_reason = "TARGET"
                                break
                                
                            if curr_spread >= current_sl_val:
                                exit_time = ct
                                exit_s_p = curr_s
                                exit_l_p = curr_l
                                exit_reason = "BREAKEVEN_SL" if be_active else "STOP_LOSS"
                                break
                                
                            if ct_t >= pd.Timestamp('15:15:00').time():
                                exit_time = ct
                                exit_s_p = curr_s
                                exit_l_p = curr_l
                                exit_reason = "TIME_EXIT"
                                break
                                
                        if exit_time is None:
                            last_t = common_times[-1]
                            exit_time = last_t
                            exit_s_p = s_dict[last_t]
                            l_idx_last = np.where(traj_l['times'] == last_t)[0]
                            exit_l_p = traj_l['ltp'][l_idx_last[0]] if len(l_idx_last) > 0 else 0.0
                            exit_reason = "EOD_CLOSE"
                            
                        exit_spread = exit_s_p - exit_l_p
                        spread_pnl = net_credit - exit_spread
                        gross_pnl = spread_pnl * lot_size
                        entry_to = (entry_s_p + entry_l_p) * lot_size
                        exit_to = (exit_s_p + exit_l_p) * lot_size
                        slip = (entry_to + exit_to) * 0.0015
                        net_pnl = gross_pnl - slip
                        
                        trades.append({
                            'date': dt,
                            'year_month': f"{dt.year}-{dt.month:02d}",
                            'entry_time': entry_time,
                            'exit_time': exit_time,
                            'type': strat_type,
                            'short_strike': short_k,
                            'long_strike': long_k,
                            'entry_credit': net_credit,
                            'exit_spread': exit_spread,
                            'lot_size': lot_size,
                            'gross_pnl': gross_pnl,
                            'slippage': slip,
                            'net_pnl': net_pnl,
                            'exit_reason': exit_reason
                        })
                        
                        daily_trades += 1
                        in_pos = True
                        pos_exit = exit_time
                        
                if not trades:
                    continue
                    
                tdf = pd.DataFrame(trades)
                monthly = tdf.groupby('year_month')['net_pnl'].sum()
                total_months = len(monthly)
                pos_months = (monthly > 0).sum()
                target_5k_months = (monthly >= 5000.0).sum()
                target_3k_months = (monthly >= 3000.0).sum()
                
                tdf['cum_pnl'] = tdf['net_pnl'].cumsum()
                tdf['equity'] = 200000.0 + tdf['cum_pnl']
                peak = tdf['equity'].cummax()
                dd_pts = peak - tdf['equity']
                dd_pct = (dd_pts / peak).max() * 100.0
                
                total_net = tdf['net_pnl'].sum()
                wins = tdf[tdf['net_pnl'] > 0]
                losses = tdf[tdf['net_pnl'] <= 0]
                win_rate = (len(wins) / len(tdf)) * 100.0
                gross_win = wins['net_pnl'].sum() if len(wins) > 0 else 0.0
                gross_loss = abs(losses['net_pnl'].sum()) if len(losses) > 0 else 1.0
                pf = gross_win / gross_loss if gross_loss > 0 else 99.0
                
                cfg_label = f"{d_cfg['name']} | {tsl_cfg['name']} | Max{max_t}T"
                
                results.append({
                    'Config_Key': cfg_label,
                    'Delta_Setup': d_cfg['desc'],
                    'Target_SL': tsl_cfg['desc'],
                    'Max_Trades_Day': max_t,
                    'Total_Trades': len(tdf),
                    'Win_Rate_%': win_rate,
                    'Net_PnL_Rs': total_net,
                    'Profit_Factor': pf,
                    'Max_DD_%': dd_pct,
                    'Pos_Months': f"{pos_months}/{total_months} ({pos_months/total_months*100:.1f}%)",
                    'Target_5k_Months': f"{target_5k_months}/{total_months} ({target_5k_months/total_months*100:.1f}%)",
                    'Num_5k_Months': target_5k_months,
                    'Num_Pos_Months': pos_months,
                    'Target_3k_Months': f"{target_3k_months}/{total_months} ({target_3k_months/total_months*100:.1f}%)",
                    'Min_Month_PnL': monthly.min(),
                    'Max_Month_PnL': monthly.max(),
                    'Avg_Month_PnL': monthly.mean()
                })
                trade_ledgers[cfg_label] = tdf

    print(f"   Completed {len(results)} optimization scenarios in {time.time()-t_opt0:.2f}s!\n")

    res_df = pd.DataFrame(results)
    # Sort primarily by Num_5k_Months, then Num_Pos_Months, then Net_PnL_Rs
    res_df = res_df.sort_values(by=['Num_5k_Months', 'Num_Pos_Months', 'Net_PnL_Rs'], ascending=[False, False, False])
    
    save_path = Path("results/credit_spread_5k_optimization_results.csv")
    res_df.to_csv(save_path, index=False)
    print(f"4. Saved Optimization Grid Results to {save_path}")

    # Display Top 10 Leaderboard
    print("\n" + "=" * 135)
    print("                    TOP 10 OPTIMIZATIONS LEADERBOARD FOR >= RS. 5,000 / MONTH TARGET                    ")
    print("=" * 135)
    cols_show = ['Config_Key', 'Total_Trades', 'Win_Rate_%', 'Net_PnL_Rs', 'Profit_Factor', 'Max_DD_%', 'Pos_Months', 'Target_5k_Months', 'Min_Month_PnL', 'Avg_Month_PnL']
    print(res_df.head(10)[cols_show].to_string(index=False))
    print("=" * 135 + "\n")

    # Analyze the #1 Champion for 5k Target
    champ_row = res_df.iloc[0]
    champ_key = champ_row['Config_Key']
    champ_tdf = trade_ledgers[champ_key]
    
    champ_save_path = Path("results/credit_spread_5k_champion_trades.csv")
    champ_tdf.to_csv(champ_save_path, index=False)
    print(f"5. Saved 5k Champion Trade Ledger to {champ_save_path}")

    # Month-by-month of Champion
    print("\n" + "=" * 110)
    print(f"            CHAMPION 5K-TARGET MONTH-BY-MONTH AUDIT: {champ_key}            ")
    print("=" * 110)
    m_champ = champ_tdf.groupby('year_month').agg(
        Trades=('net_pnl', 'count'),
        Wins=('net_pnl', lambda x: (x > 0).sum()),
        Losses=('net_pnl', lambda x: (x <= 0).sum()),
        Net_PnL=('net_pnl', 'sum')
    )
    m_champ['Win_Rate_%'] = (m_champ['Wins'] / m_champ['Trades']) * 100.0
    m_champ['Status'] = m_champ['Net_PnL'].apply(
        lambda x: ">= Rs.5,000 [CHAMPION ACHIEVED]" if x >= 5000 else (">= Rs.3,000 [SOLID]" if x >= 3000 else ("PROFITABLE" if x > 0 else "LOSS"))
    )
    print(m_champ.to_string())
    print("=" * 110)
    print(f"Total Net PnL: Rs. {m_champ['Net_PnL'].sum():,.2f}")
    print(f"Positive Months: {(m_champ['Net_PnL'] > 0).sum()}/{len(m_champ)} ({(m_champ['Net_PnL'] > 0).sum()/len(m_champ)*100:.1f}%)")
    print(f"Months >= Rs. 5,000 Target: {(m_champ['Net_PnL'] >= 5000).sum()}/{len(m_champ)} ({(m_champ['Net_PnL'] >= 5000).sum()/len(m_champ)*100:.1f}%)")
    print(f"Average Monthly PnL: Rs. {m_champ['Net_PnL'].mean():,.2f} / month")
    print(f"Best Month: Rs. {m_champ['Net_PnL'].max():,.2f}")
    print(f"Worst Month: Rs. {m_champ['Net_PnL'].min():,.2f}\n")

if __name__ == '__main__':
    run_optimization()
