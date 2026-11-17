"""
scripts/audit_hard_sl_real_backtest.py
Forensic Audit: Original 60% SL vs Real Minute-by-Minute In-Trade Rs 2,800 Hard SL
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

def run_audit():
    print("=" * 100)
    print("FORENSIC AUDIT: ORIGINAL 60% SL VS REAL IN-TRADE RS 2,800 HARD SL (MINUTE-BY-MINUTE)")
    print("=" * 100)

    # 1. Spot Setup
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    df_spot = pd.read_parquet(spot_path)
    spot_price_map = dict(zip(df_spot.index, df_spot['close']))

    df_daily = df_spot.resample('1D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_daily['ema20'] = df_daily['close'].ewm(span=20, adjust=False).mean()
    df_daily['daily_bull'] = df_daily['close'] > df_daily['ema20']
    df_daily['daily_bear'] = df_daily['close'] < df_daily['ema20']
    daily_trend_map = {d.date(): 'BULL' if b else ('BEAR' if r else 'NEUTRAL') for d, b, r in zip(df_daily.index, df_daily['daily_bull'], df_daily['daily_bear'])}

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

    options_path = "data/nifty_options_master_2020_2026_1min.parquet"
    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

    valid_days = ['Tuesday', 'Wednesday', 'Thursday', 'Friday'] # Model 4 days
    short_d = 0.50
    long_d = 0.10
    target_val = 0.40 # 40% decay
    sl_pct = 0.60 # 60% expansion

    trades_original = []
    trades_hard_sl = []

    for yr in years:
        t0 = time.time()
        print(f"Loading and simulating Year {yr}...", flush=True)

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

        yr_dates = sorted(daily_cache_yr.keys())

        # Simulate both variants independently across the year
        for variant in ['ORIGINAL', 'REAL_HARD_2800']:
            in_pos = False
            pos_exit = None
            daily_trades = 0
            day_had_loss = False

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
                day_had_loss = False
                in_pos = False
                pos_exit = None

                dte_years = max((target_exp - dt).days / 365.25, 0.001)
                sigma = daily_vol_map.get(dt, 0.16)
                d_trend = daily_trend_map.get(dt, 'NEUTRAL')

                for bar_ts, bar in day_bars.iterrows():
                    if daily_trades >= 2: break
                    if day_had_loss: break # Circuit breaker

                    entry_time = bar['close_time'] + pd.Timedelta(minutes=1)
                    if in_pos and pos_exit is not None and entry_time < pos_exit:
                        continue
                    else:
                        in_pos = False

                    is_bull = bool(bar['is_bull'])
                    is_bear = bool(bar['is_bear'])
                    if not is_bull and not is_bear: continue

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

                    target_spread = net_credit * (1.0 - target_val)

                    # KEY DIFFERENCE UNDER AUDIT:
                    if variant == 'ORIGINAL':
                        stop_spread = net_credit * (1.0 + sl_pct)
                    else: # REAL_HARD_2800:
                        # Real active cap at Rs 2800 loss: (stop_spread - net_credit) * lot_size <= 2800
                        hard_pts = 2800.0 / lot_size
                        stop_spread = min(net_credit * (1.0 + sl_pct), net_credit + hard_pts)

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

                    trade_rec = {
                        'date': dt,
                        'year': dt.year,
                        'year_month': f"{dt.year}-{dt.month:02d}",
                        'day': day_name,
                        'entry_time': str(entry_time),
                        'net_credit': round(net_credit, 2),
                        'exit_time': str(exit_time),
                        'exit_spread': round(exit_spread, 2),
                        'lot_size': lot_size,
                        'net_pnl': round(net_pnl, 2),
                        'exit_reason': exit_reason
                    }

                    if variant == 'ORIGINAL':
                        trades_original.append(trade_rec)
                    else:
                        trades_hard_sl.append(trade_rec)

                    daily_trades += 1
                    in_pos = True
                    pos_exit = exit_time

        print(f"Completed Year {yr} in {time.time() - t0:.1f}s")

    df_orig = pd.DataFrame(trades_original)
    df_hard = pd.DataFrame(trades_hard_sl)

    df_orig.to_csv("results/audit_original_trades.csv", index=False)
    df_hard.to_csv("results/audit_real_hard2800_trades.csv", index=False)

    print("\n" + "=" * 100)
    print("FINAL 6-YEAR AUDIT RESULTS (100% REAL MINUTE-BY-MINUTE SIMULATION)")
    print("=" * 100)

    for name, df in [('Original Model 4 (60% SL)', df_orig), ('Real In-Trade Rs 2,800 Hard SL', df_hard)]:
        pnl = df['net_pnl']
        wins = df[pnl > 0]
        losses = df[pnl <= 0]
        win_rate = len(wins) / len(df) * 100.0
        tot_pnl = pnl.sum()

        eq = pnl.cumsum()
        peak = eq.cummax()
        dd = (eq - peak).min()

        m2020 = df[df['year_month'] == '2020-03']['net_pnl'].sum()
        sl_exits = df[df['exit_reason'] == 'STOP_LOSS']

        print(f"\n--- {name} ---")
        print(f"Total Trades: {len(df)}")
        print(f"Winning Trades: {len(wins)} ({win_rate:.2f}%)")
        print(f"Losing Trades: {len(losses)} ({100 - win_rate:.2f}%)")
        print(f"STOP_LOSS Exits: {len(sl_exits)}")
        print(f"TARGET Exits: {len(df[df['exit_reason'] == 'TARGET'])}")
        print(f"BREAKEVEN Exits: {len(df[df['exit_reason'] == 'BREAKEVEN_SL'])}")
        print(f"Total Net PnL: Rs {tot_pnl:,.2f}")
        print(f"Max Drawdown: Rs {dd:,.2f}")
        print(f"Avg Win: Rs {wins['net_pnl'].mean():,.2f}")
        print(f"Avg Loss: Rs {losses['net_pnl'].mean():,.2f}")
        print(f"Avg SL Exit Loss: Rs {sl_exits['net_pnl'].mean():,.2f}")
        print(f"March 2020 (COVID) PnL: Rs {m2020:,.2f}")

if __name__ == '__main__':
    run_audit()
