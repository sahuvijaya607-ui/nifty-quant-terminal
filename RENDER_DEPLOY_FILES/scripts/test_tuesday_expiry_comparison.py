"""
scripts/test_tuesday_expiry_comparison.py
Real Data Quantitative Audit: Tuesday Expiry Day Mechanics
Comparing:
Option A: Same Day 0 DTE Expiry on Tuesday (High Gamma Risk)
Option B: Next Weekly Expiry on Tuesday (Safe Linear Theta Decay)
"""

import os
import sys
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

def run_tuesday_audit():
    print("=" * 120)
    print("          TUESDAY EXPIRY REAL DATA AUDIT: 0-DTE SAME DAY VS NEXT WEEKLY EXPIRY          ")
    print("=" * 120)

    # 1. Spot Data
    df_spot = pd.read_parquet("data/nifty_spot_master_2020_2026_1min.parquet")
    spot_price_map = dict(zip(df_spot.index, df_spot['close']))

    df_daily = df_spot.resample('1D').agg({'close': 'last'}).dropna()
    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = df_daily['returns'].rolling(30).std() * np.sqrt(252)
    df_daily['rolling_vol'] = df_daily['rolling_vol'].bfill().fillna(0.18)
    daily_vol_map = {d.date(): float(v) for d, v in df_daily['rolling_vol'].items()}

    df_5m = df_spot.resample('5min', origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_5m['close_time'] = df_5m.index + pd.Timedelta(minutes=5)
    df_5m['close_time_t'] = df_5m['close_time'].dt.time
    df_5m['date'] = df_5m.index.date

    df_5m['ema9'] = df_5m['close'].ewm(span=9, adjust=False).mean()
    df_5m['ema20'] = df_5m['close'].ewm(span=20, adjust=False).mean()
    df_5m['ema50'] = df_5m['close'].ewm(span=50, adjust=False).mean()
    df_5m['is_green'] = df_5m['close'] >= df_5m['open']
    df_5m['is_red'] = df_5m['close'] <= df_5m['open']

    df_5m['is_bull'] = (df_5m['close'] > df_5m['ema50']) & (df_5m['ema9'] >= df_5m['ema20']) & (df_5m['close'] > df_5m['ema9']) & df_5m['is_green']
    df_5m['is_bear'] = (df_5m['close'] < df_5m['ema50']) & (df_5m['ema9'] <= df_5m['ema20']) & (df_5m['close'] < df_5m['ema9']) & df_5m['is_red']

    time_mask = (df_5m['close_time_t'] >= pd.Timestamp('09:20:00').time()) & (df_5m['close_time_t'] <= pd.Timestamp('14:30:00').time())
    df_5m_sub = df_5m[time_mask].copy()

    # 2. Options Data
    clean_path = Path("data/nifty_option_chain_1min_clean.parquet")
    opt_table = pq.read_table(clean_path, columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'oi'])
    df_opt = opt_table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['expiry_date'] = df_opt['expiry'].dt.date
    all_dates = sorted(df_opt['date'].unique())

    oi_agg = df_opt.groupby(['timestamp', 'option_type'])['oi'].sum().unstack(fill_value=0)
    pcr_map = (oi_agg['PE'] / oi_agg['CE'].replace(0, 1)).to_dict()

    # Build cache with BOTH same-day expiry and next-weekly expiry for Tuesdays
    daily_cache = {}
    for dt, day_df in df_opt.groupby('date'):
        day_name = pd.to_datetime(dt).day_name()
        if day_name != 'Tuesday':
            continue

        expiries = sorted(day_df['expiry_date'].unique())
        same_day_exp = [exp for exp in expiries if exp == dt]
        next_week_exp = [exp for exp in expiries if exp > dt]

        trajs_same = {}
        if same_day_exp:
            exp_df = day_df[day_df['expiry_date'] == same_day_exp[0]]
            for (stk, otype), grp in exp_df.groupby(['strike_price', 'option_type']):
                grp_sorted = grp.sort_values('timestamp')
                trajs_same[(int(stk), otype)] = {
                    'times': grp_sorted['timestamp'].to_numpy(),
                    'ltp': grp_sorted['ltp'].fillna(0.0).to_numpy()
                }

        trajs_next = {}
        if next_week_exp:
            exp_df = day_df[day_df['expiry_date'] == next_week_exp[0]]
            for (stk, otype), grp in exp_df.groupby(['strike_price', 'option_type']):
                grp_sorted = grp.sort_values('timestamp')
                trajs_next[(int(stk), otype)] = {
                    'times': grp_sorted['timestamp'].to_numpy(),
                    'ltp': grp_sorted['ltp'].fillna(0.0).to_numpy()
                }

        daily_cache[dt] = {
            'same_exp': same_day_exp[0] if same_day_exp else None,
            'next_exp': next_week_exp[0] if next_week_exp else None,
            'trajs_same': trajs_same,
            'trajs_next': trajs_next,
            'pe_strikes_same': sorted([int(stk) for stk, otype in trajs_same.keys() if otype == 'PE']),
            'ce_strikes_same': sorted([int(stk) for stk, otype in trajs_same.keys() if otype == 'CE']),
            'pe_strikes_next': sorted([int(stk) for stk, otype in trajs_next.keys() if otype == 'PE']),
            'ce_strikes_next': sorted([int(stk) for stk, otype in trajs_next.keys() if otype == 'CE']),
        }

    print(f"Indexed {len(daily_cache)} Tuesday sessions.\n")

    def simulate_tuesday(use_same_day=False):
        trades = []
        equity = 200000.0

        for dt, day in daily_cache.items():
            if use_same_day:
                target_exp = day['same_exp']
                trajs = day['trajs_same']
                pe_strikes = day['pe_strikes_same']
                ce_strikes = day['ce_strikes_same']
            else:
                target_exp = day['next_exp']
                trajs = day['trajs_next']
                pe_strikes = day['pe_strikes_next']
                ce_strikes = day['ce_strikes_next']

            if target_exp is None or not trajs:
                continue

            lot_size = get_nifty_lot_size(dt)
            day_bars = df_5m_sub[df_5m_sub.index.date == dt]
            if day_bars.empty:
                continue

            daily_trades = 0
            in_pos = False
            pos_exit = None
            dte_years = max((target_exp - dt).days / 365.25, 0.0005 if use_same_day else 0.001)
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

                curr_pcr = pcr_map.get(bar['close_time'], 1.0)
                if is_bull and curr_pcr < 1.0:
                    continue
                if is_bear and curr_pcr > 1.0:
                    continue

                otype = "PE" if is_bull else "CE"
                available_strikes = pe_strikes if is_bull else ce_strikes
                if len(available_strikes) < 2:
                    continue

                if entry_time not in spot_price_map:
                    continue
                spot_entry = float(spot_price_map[entry_time])

                # Deltas: 45 short, 15 long
                deltas = calc_bs_deltas(spot_entry, np.array(available_strikes), dte_years, 0.07, sigma, otype)
                if is_bull:
                    idx_s = np.argmin(np.abs(deltas - (-0.45)))
                    idx_l = np.argmin(np.abs(deltas - (-0.15)))
                    short_k = available_strikes[idx_s]
                    long_k = available_strikes[idx_l]
                    if short_k <= long_k:
                        continue
                else:
                    idx_s = np.argmin(np.abs(deltas - 0.45))
                    idx_l = np.argmin(np.abs(deltas - 0.15))
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
                ltp_sub_l = traj_l['ltp'][idx_match_l[0]:]
                s_dict = dict(zip(times_sub_s, ltp_sub_s))
                common_times = [t for t in times_sub_l if t in s_dict]
                if not common_times:
                    continue

                tgt_val = net_credit * 0.60
                current_sl_val = net_credit * 1.60
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

                    if not be_active and curr_spread <= (net_credit * 0.75):
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
                equity += net_pnl

                trades.append({
                    'date': dt,
                    'year_month': f"{dt.year}-{dt.month:02d}",
                    'net_pnl': net_pnl,
                    'gross_pnl': gross_pnl,
                    'slippage': slip,
                    'exit_reason': exit_reason
                })
                daily_trades += 1
                in_pos = True
                pos_exit = exit_time

        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return None

        total_net = float(tdf['net_pnl'].sum())
        wins = tdf[tdf['net_pnl'] > 0]
        losses = tdf[tdf['net_pnl'] <= 0]
        win_rate = (len(wins) / len(tdf)) * 100.0
        gross_win = wins['net_pnl'].sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses['net_pnl'].sum()) if len(losses) > 0 else 1.0
        pf = gross_win / gross_loss if gross_loss > 0 else 99.0

        tdf['cum_pnl'] = tdf['net_pnl'].cumsum()
        tdf['eq_curve'] = 200000.0 + tdf['cum_pnl']
        peak = tdf['eq_curve'].cummax()
        dd_pts = peak - tdf['eq_curve']
        dd_pct = (dd_pts / peak).max() * 100.0

        monthly = tdf.groupby('year_month')['net_pnl'].sum()

        return {
            'trades': len(tdf),
            'win_rate': round(win_rate, 2),
            'net_pnl': round(total_net, 2),
            'profit_factor': round(pf, 2),
            'max_dd_pct': round(dd_pct, 2),
            'avg_monthly': round(monthly.mean(), 2)
        }

    res_same = simulate_tuesday(use_same_day=True)
    res_next = simulate_tuesday(use_same_day=False)

    print("RESULTS COMPARISON ON TUESDAY TRADES (1 LOT FIXED):")
    if res_same:
        print(f"Option A: 0-DTE Same Day Tuesday Expiry:")
        print(f"   Net PnL = Rs. {res_same['net_pnl']:+,.2f} | WinRate = {res_same['win_rate']}% | Max DD = {res_same['max_dd_pct']}% | Avg/Mo = Rs. {res_same['avg_monthly']:+,.2f}")
    else:
        print("Option A: No same day contracts active.")

    if res_next:
        print(f"\nOption B: Next Weekly Expiry on Tuesday (Our Golden Rule):")
        print(f"   Net PnL = Rs. {res_next['net_pnl']:+,.2f} | WinRate = {res_next['win_rate']}% | Max DD = {res_next['max_dd_pct']}% | Avg/Mo = Rs. {res_next['avg_monthly']:+,.2f}")

if __name__ == '__main__':
    run_tuesday_audit()
