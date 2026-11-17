"""
strategies/tournament_full_6yr_all_strategies.py
Runs ALL major competing Credit Spread strategies across the FULL 6-YEAR MASTER DATASET (2020 to 2026):
1. Strat A: Original Baseline (35-Delta Short / 15-Delta Wing, All 5 Days)
2. Strat B: Strategy 2 - Pure ATM Asymmetric (50-Delta Short / 10-Delta Wing, Tue-Thu-Fri)
3. Strat C: Strategy 3 - Pure ATM Moderate Wing (50-Delta Short / 15-Delta Wing, Tue-Thu-Fri)
4. Strat D: Strategy 4 - Balanced Asymmetric (45-Delta Short / 10-Delta Wing, Tue-Thu-Fri)
5. Strat E: Strategy 5 - Balanced Moderate (45-Delta Short / 15-Delta Wing, Tue-Thu-Fri)
6. Strat F: Strategy 1 - 4-Day ATM Engine (50-Delta Short / 10-Delta Wing, Tue-Wed-Thu-Fri with Wed PCR filter)

Single-pass execution over 74.3M rows for maximum speed and memory efficiency.
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

def run_6yr_tournament():
    t_start = time.time()
    print("=" * 110)
    print("      COMPREHENSIVE 6-YEAR ALL-STRATEGY MASTER TOURNAMENT (2020 TO 2026 - 74.3M ROWS)      ")
    print("=" * 110)

    # 1. Spot Data Setup
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    df_spot = pd.read_parquet(spot_path)
    spot_price_map = dict(zip(df_spot.index, df_spot['close']))

    df_daily = df_spot.resample('1D').agg({'close': 'last'}).dropna()
    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = df_daily['returns'].rolling(30).std() * np.sqrt(252)
    df_daily['rolling_vol'] = df_daily['rolling_vol'].bfill().fillna(0.18)
    daily_vol_map = {d.date(): float(v) for d, v in df_daily['rolling_vol'].items()}

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

    # Strategy Configurations
    configs = {
        'Strat_A_Base_35D_15D_AllDays': {'short_d': 0.35, 'long_d': 0.15, 'days': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'], 'wed_pcr': False},
        'Strat_B_Pure_ATM_D50_L10_3Day': {'short_d': 0.50, 'long_d': 0.10, 'days': ['Tuesday', 'Thursday', 'Friday'], 'wed_pcr': False},
        'Strat_C_Pure_ATM_D50_L15_3Day': {'short_d': 0.50, 'long_d': 0.15, 'days': ['Tuesday', 'Thursday', 'Friday'], 'wed_pcr': False},
        'Strat_D_Balanced_D45_L10_3Day': {'short_d': 0.45, 'long_d': 0.10, 'days': ['Tuesday', 'Thursday', 'Friday'], 'wed_pcr': False},
        'Strat_E_Balanced_D45_L15_3Day': {'short_d': 0.45, 'long_d': 0.15, 'days': ['Tuesday', 'Thursday', 'Friday'], 'wed_pcr': False},
        'Strat_F_4Day_ATM_D50_L10':      {'short_d': 0.50, 'long_d': 0.10, 'days': ['Tuesday', 'Wednesday', 'Thursday', 'Friday'], 'wed_pcr': True},
    }

    results = {k: [] for k in configs.keys()}

    options_path = "data/nifty_options_master_2020_2026_1min.parquet"
    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

    for yr in years:
        t_yr0 = time.time()
        print(f"Loading and processing Year {yr} across all 6 strategies...", flush=True)

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

        # Now simulate all configs for this year
        for cfg_name, cfg in configs.items():
            valid_days = set(cfg['days'])
            short_delta = cfg['short_d']
            long_delta = cfg['long_d']
            is_wed_pcr = cfg['wed_pcr']
            tgt_pct = 0.40
            sl_pct = 0.60

            yr_cfg_pnl = 0.0
            yr_cfg_trades = 0

            for dt in yr_dates:
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
                in_pos = False
                pos_exit = None

                dte_years = max((target_exp - dt).days / 365.25, 0.001)
                sigma = daily_vol_map.get(dt, 0.16)

                for bar_ts, bar in day_bars.iterrows():
                    if daily_trades >= 2:
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

                    curr_pcr = pcr_map_yr.get(bar['close_time'], 1.0)
                    if day_name == 'Wednesday' and is_wed_pcr:
                        if is_bull and curr_pcr < 1.15: continue
                        if is_bear and curr_pcr > 0.85: continue
                    else:
                        if is_bull and curr_pcr < 1.0: continue
                        if is_bear and curr_pcr > 1.0: continue

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
                        idx_s = np.argmin(np.abs(deltas - (-short_delta)))
                        idx_l = np.argmin(np.abs(deltas - (-long_delta)))
                        short_k = available_strikes[idx_s]
                        long_k = available_strikes[idx_l]
                        if short_k <= long_k:
                            continue
                    else:
                        idx_s = np.argmin(np.abs(deltas - short_delta))
                        idx_l = np.argmin(np.abs(deltas - long_delta))
                        short_k = available_strikes[idx_s]
                        long_k = available_strikes[idx_l]
                        if short_k >= long_k:
                            continue

                    key_s = (short_k, otype)
                    key_l = (long_k, otype)
                    if key_s not in trajs or key_l not in trajs:
                        continue

                    times_s = trajs[key_s]['times']
                    ltp_s = trajs[key_s]['ltp']
                    times_l = trajs[key_l]['times']
                    ltp_l = trajs[key_l]['ltp']

                    pos_s = np.searchsorted(times_s, np.datetime64(entry_time))
                    pos_l = np.searchsorted(times_l, np.datetime64(entry_time))
                    if pos_s >= len(times_s) or pos_l >= len(times_l):
                        continue

                    entry_s_price = ltp_s[pos_s]
                    entry_l_price = ltp_l[pos_l]
                    if entry_s_price <= 0.0 or entry_l_price <= 0.0 or entry_s_price <= entry_l_price:
                        continue

                    net_credit = entry_s_price - entry_l_price
                    if net_credit < 5.0:
                        continue

                    target_spread = net_credit * (1.0 - tgt_pct)
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

                    results[cfg_name].append({
                        'date': dt,
                        'year': dt.year,
                        'year_month': f"{dt.year}-{dt.month:02d}",
                        'day': day_name,
                        'entry_time': entry_time.strftime("%H:%M"),
                        'net_credit': round(net_credit, 2),
                        'exit_time': exit_time.strftime("%H:%M"),
                        'exit_spread': round(exit_spread, 2),
                        'lot_size': lot_size,
                        'gross_pnl': round(gross_pnl, 2),
                        'slippage': round(slippage, 2),
                        'net_pnl': round(net_pnl, 2),
                        'exit_reason': exit_reason
                    })

                    daily_trades += 1
                    yr_cfg_trades += 1
                    yr_cfg_pnl += net_pnl
                    in_pos = True
                    pos_exit = exit_time

            print(f"      [{cfg_name}]: {yr_cfg_trades} trades | PnL: Rs. {yr_cfg_pnl:,.2f}", flush=True)

        print(f"   Finished Year {yr} in {time.time()-t_yr0:.1f}s\n", flush=True)
        gc.collect()

    # Summarize All Results
    summary_list = []
    yearly_dict = {}

    for cfg_name, trades in results.items():
        df_t = pd.DataFrame(trades)
        df_t.to_csv(f"results/6yr_trades_{cfg_name}.csv", index=False)

        tot_trades = len(df_t)
        wins = (df_t['net_pnl'] > 0).sum()
        win_rate = round(wins / tot_trades * 100, 2) if tot_trades > 0 else 0
        tot_pnl = round(df_t['net_pnl'].sum(), 2)

        monthly = df_t.groupby('year_month')['net_pnl'].sum().reset_index()
        avg_mo = round(monthly['net_pnl'].mean(), 2)
        pos_mo = (monthly['net_pnl'] > 0).sum()
        mo_gt_5k = (monthly['net_pnl'] >= 5000).sum()

        yearly = df_t.groupby('year')['net_pnl'].sum().to_dict()
        yearly_dict[cfg_name] = yearly

        # Max drawdown
        df_t['cum_pnl'] = df_t['net_pnl'].cumsum()
        df_t['peak'] = df_t['cum_pnl'].cummax()
        df_t['dd'] = df_t['cum_pnl'] - df_t['peak']
        max_dd = round(df_t['dd'].min(), 2)

        summary_list.append({
            'Strategy': cfg_name,
            'Total_Trades': tot_trades,
            'Win_Rate_%': win_rate,
            'Total_Net_PnL': tot_pnl,
            'Avg_Monthly_PnL': avg_mo,
            'Profitable_Months': f"{pos_mo}/{len(monthly)} ({pos_mo/len(monthly)*100:.1f}%)",
            'Months_GE_5K': f"{mo_gt_5k}/{len(monthly)} ({mo_gt_5k/len(monthly)*100:.1f}%)",
            'Max_Drawdown_Rs': max_dd
        })

    df_sum = pd.DataFrame(summary_list)
    df_sum = df_sum.sort_values('Total_Net_PnL', ascending=False)
    df_sum.to_csv("results/all_strategies_6yr_master_summary.csv", index=False)

    df_yr = pd.DataFrame(yearly_dict).T
    df_yr.to_csv("results/all_strategies_6yr_yearly_matrix.csv")

    print("=" * 110)
    print("      ALL-STRATEGY 6-YEAR TOURNAMENT COMPLETED SUCCESSFULLY      ")
    print("=" * 110)
    print(df_sum.to_string(index=False))
    print("\nYEAR-BY-YEAR PnL MATRIX:")
    print(df_yr.to_string())

if __name__ == "__main__":
    run_6yr_tournament()
