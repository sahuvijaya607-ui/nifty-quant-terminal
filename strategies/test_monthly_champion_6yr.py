"""
strategies/test_monthly_champion_6yr.py
Full 6-Year (2020 to 2026 - 75 Months) Master Search for the "Monthly Consistent ₹3k-₹5k Champion":
Evaluates:
Candidate 1: Trend-Aligned ATM 50D_10D + 1-Loss Circuit Breaker + BE Trailing @ 25% + 40% Target
Candidate 2: Trend-Aligned Balanced 45D_15D + 1-Loss Circuit Breaker + BE Trailing @ 25% + 40% Target
Candidate 3: Trend-Aligned ATM 50D_10D + Fixed Rs. 900 Target Exit + 1-Loss Circuit Breaker
Candidate 4: Dual-Engine Ensemble (Trend Spread + Tuesday Expiry Harvester)
Candidate 5: Trend-Aligned 45D_15D + Monthly Profit Lock (Lock at Rs 4,000)

Output: Complete month-by-month distribution, % months >= Rs 3,000, % months >= Rs 5,000, Max DD.
"""

import os
import sys
import gc
import time
from pathlib import Path
import pandas as pd
import numpy as np
import scipy.stats as si
import pyarrow.parquet as pq

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

def run_monthly_champion_search():
    t_start = time.time()
    print("=" * 110)
    print("      SEARCHING FOR THE ULTIMATE MONTHLY CONSISTENT CHAMPION (2020-2026: 75 MONTHS)      ")
    print("=" * 110)

    # 1. Spot Setup
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    df_spot = pd.read_parquet(spot_path)
    spot_price_map = dict(zip(df_spot.index, df_spot['close']))

    # Daily Trend Filter
    df_daily = df_spot.resample('1D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_daily['ema20'] = df_daily['close'].ewm(span=20, adjust=False).mean()
    df_daily['daily_bull'] = df_daily['close'] > df_daily['ema20']
    df_daily['daily_bear'] = df_daily['close'] < df_daily['ema20']
    daily_trend_map = {d.date(): 'BULL' if b else ('BEAR' if r else 'NEUTRAL') for d, b, r in zip(df_daily.index, df_daily['daily_bull'], df_daily['daily_bear'])}

    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = df_daily['returns'].rolling(30).std() * np.sqrt(252)
    df_daily['rolling_vol'] = df_daily['rolling_vol'].bfill().fillna(0.18)
    daily_vol_map = {d.date(): float(v) for d, v in df_daily['rolling_vol'].items()}

    # 5m Bars
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

    df_5m['is_bull'] = (df_5m['close'] > df_5m['ema50']) & (df_5m['ema9'] >= df_5m['ema20']) & (df_5m['close'] > df_5m['ema9']) & df_5m['is_green']
    df_5m['is_bear'] = (df_5m['close'] < df_5m['ema50']) & (df_5m['ema9'] <= df_5m['ema20']) & (df_5m['close'] < df_5m['ema9']) & df_5m['is_red']

    time_mask = (df_5m['close_time_t'] >= pd.Timestamp('09:20:00').time()) & (df_5m['close_time_t'] <= pd.Timestamp('14:30:00').time())
    df_5m_sub = df_5m[time_mask].copy()

    # Candidate Models
    candidates = {
        'Model_1_ATM_Trend_CircuitBreaker': {
            'short_d': 0.50, 'long_d': 0.10, 'use_trend': True, 'circuit_breaker': True, 'target_mode': 'pct', 'target_val': 0.40, 'days': ['Tuesday', 'Thursday', 'Friday'], 'monthly_lock': 0
        },
        'Model_2_Balanced45_Trend_CircuitBreaker': {
            'short_d': 0.45, 'long_d': 0.15, 'use_trend': True, 'circuit_breaker': True, 'target_mode': 'pct', 'target_val': 0.40, 'days': ['Tuesday', 'Thursday', 'Friday'], 'monthly_lock': 0
        },
        'Model_3_ATM_FixedTarget900': {
            'short_d': 0.50, 'long_d': 0.10, 'use_trend': True, 'circuit_breaker': True, 'target_mode': 'fixed_rs', 'target_val': 900.0, 'days': ['Tuesday', 'Thursday', 'Friday'], 'monthly_lock': 0
        },
        'Model_4_4Day_ATM_Trend_CircuitBreaker': {
            'short_d': 0.50, 'long_d': 0.10, 'use_trend': True, 'circuit_breaker': True, 'target_mode': 'pct', 'target_val': 0.40, 'days': ['Tuesday', 'Wednesday', 'Thursday', 'Friday'], 'monthly_lock': 0
        },
        'Model_5_Balanced45_MonthlyLock4000': {
            'short_d': 0.45, 'long_d': 0.15, 'use_trend': True, 'circuit_breaker': True, 'target_mode': 'pct', 'target_val': 0.40, 'days': ['Tuesday', 'Thursday', 'Friday'], 'monthly_lock': 4000.0
        },
    }

    results = {k: [] for k in candidates.keys()}
    monthly_pnl_tracker = {k: {} for k in candidates.keys()}

    options_path = "data/nifty_options_master_2020_2026_1min.parquet"
    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

    for yr in years:
        t_yr0 = time.time()
        print(f"Loading and testing Year {yr} across all 5 candidate models...", flush=True)

        yr_table = pq.read_table(options_path, filters=[
            ('timestamp', '>=', pd.Timestamp(f'{yr}-01-01 00:00:00')),
            ('timestamp', '<=', pd.Timestamp(f'{yr}-12-31 23:59:59'))
        ], columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'close', 'oi'])
        df_yr = yr_table.to_pandas()
        df_yr['date'] = df_yr['timestamp'].dt.date
        df_yr['expiry_date'] = df_yr['expiry'].dt.date
        del yr_table

        oi_agg = df_yr.groupby(['timestamp', 'option_type'])['oi'].sum().unstack(fill_value=0)
        pcr_series = oi_agg['PE'] / oi_agg['CE'].replace(0, 1)
        pcr_map_yr = pcr_series.to_dict()
        del oi_agg

        daily_cache_yr = {}
        for dt, day_df in df_yr.groupby('date'):
            expiries = sorted([exp for exp in day_df['expiry_date'].unique() if exp > dt])
            if not expiries:
                continue
            target_exp = expiries[0]
            exp_df = day_df[day_df['expiry_date'] == target_exp]

            trajs = {}
            for (stk, otype), grp in exp_df.groupby(['strike_price', 'option_type']):
                grp_sorted = grp.sort_values('timestamp')
                trajs[(int(stk), otype)] = {
                    'times': grp_sorted['timestamp'].to_numpy(),
                    'ltp': grp_sorted['close'].fillna(0.0).to_numpy()
                }
            daily_cache_yr[dt] = {
                'exp': target_exp,
                'trajs': trajs,
                'pe_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'PE']),
                'ce_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'CE'])
            }

        del df_yr
        gc.collect()

        yr_dates = sorted(daily_cache_yr.keys())

        for c_name, cfg in candidates.items():
            valid_days = set(cfg['days'])
            short_d = cfg['short_d']
            long_d = cfg['long_d']
            use_trend = cfg['use_trend']
            circuit_breaker = cfg['circuit_breaker']
            target_mode = cfg['target_mode']
            target_val = cfg['target_val']
            monthly_lock = cfg['monthly_lock']

            sl_pct = 0.60

            yr_trades = 0
            yr_pnl = 0.0

            curr_m = None
            mo_cum_pnl = 0.0
            mo_locked = False

            for dt in yr_dates:
                m_str = f"{dt.year}-{dt.month:02d}"
                if m_str != curr_m:
                    curr_m = m_str
                    mo_cum_pnl = 0.0
                    mo_locked = False

                if monthly_lock > 0 and mo_locked:
                    continue

                day_name = pd.to_datetime(dt).day_name()
                if day_name not in valid_days:
                    continue

                day_cache = daily_cache_yr[dt]
                trajs = day_cache['trajs']
                pe_strikes = day_cache['pe_strikes']
                ce_strikes = day_cache['ce_strikes']
                target_exp = day_cache['exp']
                lot_size = get_nifty_lot_size(dt)

                day_bars = df_5m_sub[df_5m_sub.index.date == dt]
                if day_bars.empty:
                    continue

                daily_trades = 0
                day_had_loss = False
                in_pos = False
                pos_exit = None

                dte_years = max((target_exp - dt).days / 365.25, 0.001)
                sigma = daily_vol_map.get(dt, 0.16)
                d_trend = daily_trend_map.get(dt, 'NEUTRAL')

                for bar_ts, bar in day_bars.iterrows():
                    if daily_trades >= 2: break
                    if circuit_breaker and day_had_loss: break # No revenge trade after loss

                    entry_time = bar['close_time'] + pd.Timedelta(minutes=1)
                    if in_pos and pos_exit is not None and entry_time < pos_exit:
                        continue
                    else:
                        in_pos = False

                    is_bull = bool(bar['is_bull'])
                    is_bear = bool(bar['is_bear'])
                    if not is_bull and not is_bear: continue

                    # Multi-timeframe trend filter
                    if use_trend:
                        if is_bull and d_trend != 'BULL': continue
                        if is_bear and d_trend != 'BEAR': continue

                    curr_pcr = pcr_map_yr.get(bar['close_time'], 1.0)
                    if is_bull and curr_pcr < 1.0: continue
                    if is_bear and curr_pcr > 1.0: continue

                    strat_type = "BULL_PUT_SPREAD" if is_bull else "BEAR_CALL_SPREAD"
                    otype = "PE" if is_bull else "CE"
                    available_strikes = pe_strikes if is_bull else ce_strikes
                    if len(available_strikes) < 2: continue

                    if entry_time not in spot_price_map: continue
                    spot_entry = float(spot_price_map[entry_time])

                    deltas = calc_bs_deltas(spot_entry, np.array(available_strikes), dte_years, 0.07, sigma, otype)
                    if is_bull:
                        idx_s = np.argmin(np.abs(deltas - (-short_d)))
                        idx_l = np.argmin(np.abs(deltas - (-long_d)))
                        short_k = available_strikes[idx_s]
                        long_k = available_strikes[idx_l]
                        if short_k <= long_k: continue
                    else:
                        idx_s = np.argmin(np.abs(deltas - short_d))
                        idx_l = np.argmin(np.abs(deltas - long_d))
                        short_k = available_strikes[idx_s]
                        long_k = available_strikes[idx_l]
                        if short_k >= long_k: continue

                    key_s = (short_k, otype)
                    key_l = (long_k, otype)
                    if key_s not in trajs or key_l not in trajs: continue

                    times_s = trajs[key_s]['times']
                    ltp_s = trajs[key_s]['ltp']
                    times_l = trajs[key_l]['times']
                    ltp_l = trajs[key_l]['ltp']

                    pos_s = np.searchsorted(times_s, np.datetime64(entry_time))
                    pos_l = np.searchsorted(times_l, np.datetime64(entry_time))
                    if pos_s >= len(times_s) or pos_l >= len(times_l): continue

                    entry_s_price = ltp_s[pos_s]
                    entry_l_price = ltp_l[pos_l]
                    if entry_s_price <= 0.0 or entry_l_price <= 0.0 or entry_s_price <= entry_l_price: continue

                    net_credit = entry_s_price - entry_l_price
                    if net_credit < 5.0: continue

                    # Target Spread
                    if target_mode == 'pct':
                        target_spread = net_credit * (1.0 - target_val)
                    else:
                        target_spread = max(net_credit - (target_val / lot_size), 0.0)

                    stop_spread = net_credit * (1.0 + sl_pct)
                    be_threshold = net_credit * 0.75
                    be_activated = False

                    t_end = np.datetime64(pd.Timestamp(f"{dt} 15:15:00"))
                    exit_time = None
                    exit_spread = None
                    exit_reason = "TIME_EXIT"

                    idx_cur_s = pos_s + 1
                    idx_cur_l = pos_l + 1

                    while idx_cur_s < len(times_s) and idx_cur_l < len(times_l):
                        ts_s = times_s[idx_cur_s]
                        ts_l = times_l[idx_cur_l]
                        if ts_s != ts_l:
                            if ts_s < ts_l: idx_cur_s += 1
                            else: idx_cur_l += 1
                            continue

                        if ts_s >= t_end:
                            exit_time = pd.Timestamp(ts_s)
                            exit_spread = ltp_s[idx_cur_s] - ltp_l[idx_cur_l]
                            exit_reason = "TIME_EXIT"
                            break

                        cur_s = ltp_s[idx_cur_s]
                        cur_l = ltp_l[idx_cur_l]
                        if cur_s <= 0 or cur_l <= 0:
                            idx_cur_s += 1
                            idx_cur_l += 1
                            continue

                        cur_spread = cur_s - cur_l
                        if not be_activated and cur_spread <= be_threshold:
                            be_activated = True

                        if cur_spread <= target_spread:
                            exit_time = pd.Timestamp(ts_s)
                            exit_spread = target_spread
                            exit_reason = "TARGET"
                            break

                        if be_activated and cur_spread >= net_credit:
                            exit_time = pd.Timestamp(ts_s)
                            exit_spread = net_credit
                            exit_reason = "BREAKEVEN_SL"
                            break

                        if not be_activated and cur_spread >= stop_spread:
                            exit_time = pd.Timestamp(ts_s)
                            exit_spread = stop_spread
                            exit_reason = "STOP_LOSS"
                            break

                        idx_cur_s += 1
                        idx_cur_l += 1

                    if exit_time is None:
                        exit_time = pd.Timestamp(f"{dt} 15:15:00")
                        exit_spread = ltp_s[min(idx_cur_s, len(ltp_s)-1)] - ltp_l[min(idx_cur_l, len(ltp_l)-1)]
                        exit_reason = "TIME_EXIT"

                    points_won = net_credit - exit_spread
                    gross_pnl = points_won * lot_size
                    slippage = (net_credit * 0.005 + exit_spread * 0.005) * lot_size + 20.0 + (exit_spread * lot_size * 0.000625)
                    net_pnl = gross_pnl - slippage

                    if net_pnl <= 0:
                        day_had_loss = True

                    mo_cum_pnl += net_pnl
                    if monthly_lock > 0 and mo_cum_pnl >= monthly_lock:
                        mo_locked = True

                    results[c_name].append({
                        'date': dt,
                        'year': dt.year,
                        'year_month': m_str,
                        'day': day_name,
                        'entry_time': entry_time.strftime("%H:%M"),
                        'short_strike': short_k,
                        'long_strike': long_k,
                        'net_credit': round(net_credit, 2),
                        'exit_time': exit_time.strftime("%H:%M"),
                        'exit_spread': round(exit_spread, 2),
                        'lot_size': lot_size,
                        'net_pnl': round(net_pnl, 2),
                        'exit_reason': exit_reason
                    })

                    daily_trades += 1
                    yr_trades += 1
                    yr_pnl += net_pnl
                    in_pos = True
                    pos_exit = exit_time

            print(f"      [{c_name}]: {yr_trades} trades | PnL: Rs. {yr_pnl:,.2f}", flush=True)

        print(f"   Finished Year {yr} in {time.time()-t_yr0:.1f}s\n", flush=True)
        gc.collect()

    # Summarize Monthly Consistency
    summary = []
    yearly_mat = {}

    for c_name, trades in results.items():
        df_t = pd.DataFrame(trades)
        df_t.to_csv(f"results/monthly_champ_trades_{c_name}.csv", index=False)

        monthly = df_t.groupby('year_month')['net_pnl'].sum().reset_index()
        monthly.to_csv(f"results/monthly_champ_mo_{c_name}.csv", index=False)

        tot_trades = len(df_t)
        tot_pnl = round(df_t['net_pnl'].sum(), 2)
        win_rate = round((df_t['net_pnl'] > 0).mean() * 100, 2)
        
        pos_mo = (monthly['net_pnl'] > 0).sum()
        mo_ge_3k = (monthly['net_pnl'] >= 3000).sum()
        mo_ge_5k = (monthly['net_pnl'] >= 5000).sum()
        tot_mo = len(monthly)

        avg_mo_pnl = round(monthly['net_pnl'].mean(), 2)
        worst_mo = round(monthly['net_pnl'].min(), 2)
        best_mo = round(monthly['net_pnl'].max(), 2)

        df_t['cum_pnl'] = df_t['net_pnl'].cumsum()
        df_t['peak'] = df_t['cum_pnl'].cummax()
        df_t['dd'] = df_t['cum_pnl'] - df_t['peak']
        max_dd = round(df_t['dd'].min(), 2)

        yr_dict = df_t.groupby('year')['net_pnl'].sum().to_dict()
        yearly_mat[c_name] = yr_dict

        summary.append({
            'Model': c_name,
            'Total_6Yr_PnL': tot_pnl,
            'Total_Trades': tot_trades,
            'Win_Rate_%': win_rate,
            'Profitable_Months': f"{pos_mo}/{tot_mo} ({pos_mo/tot_mo*100:.1f}%)",
            'Months_GE_3K': f"{mo_ge_3k}/{tot_mo} ({mo_ge_3k/tot_mo*100:.1f}%)",
            'Months_GE_5K': f"{mo_ge_5k}/{tot_mo} ({mo_ge_5k/tot_mo*100:.1f}%)",
            'Avg_Monthly_PnL': avg_mo_pnl,
            'Worst_Month_PnL': worst_mo,
            'Best_Month_PnL': best_mo,
            'Max_Drawdown_Rs': max_dd
        })

    df_sum = pd.DataFrame(summary).sort_values('Months_GE_3K', ascending=False)
    df_sum.to_csv("results/monthly_champion_6yr_summary.csv", index=False)

    df_ymat = pd.DataFrame(yearly_mat).T
    df_ymat.to_csv("results/monthly_champion_6yr_yearly_matrix.csv")

    print("=" * 110)
    print("      6-YEAR MONTHLY CONSISTENCY TOURNAMENT FINISHED      ")
    print("=" * 110)
    print(df_sum.to_string(index=False))
    print("\nYEAR-BY-YEAR MATRIX:")
    print(df_ymat.to_string())

if __name__ == "__main__":
    run_monthly_champion_search()
