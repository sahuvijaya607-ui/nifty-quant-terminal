"""
scripts/generate_vikalp2_full_postmortem.py
Exhaustive 6-Year Trade-by-Trade Postmortem of Vikalp 2 (Rs 2,800 Hard SL Cap)
Extracts Spot Entry/Exit, Option Leg Prices, Spreads, MFE, MAE, Slippage, and Exit Reasons.
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

def run_vikalp2_postmortem():
    t_start = time.time()
    print("=" * 110)
    print("     GENERATING EXHAUSTIVE 6-YEAR TRADE-BY-TRADE POSTMORTEM FOR VIKALP 2 (RS 2,800 HARD SL)     ")
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

    options_path = "data/nifty_options_master_2020_2026_1min.parquet"
    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

    valid_days = ['Tuesday', 'Wednesday', 'Thursday', 'Friday'] # Model 4 schedule
    short_d = 0.50
    long_d = 0.10
    target_val = 0.40 # 40% decay
    sl_pct = 0.60 # 60% expansion

    trades = []
    trade_id_counter = 1

    for yr in years:
        t0 = time.time()
        print(f"Loading and processing Year {yr}...", flush=True)

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

        in_pos = False
        pos_exit = None

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

            dte_days = (target_exp - dt).days
            dte_years = max(dte_days / 365.25, 0.001)
            sigma = daily_vol_map.get(dt, 0.16)
            d_trend = daily_trend_map.get(dt, 'NEUTRAL')

            for bar_ts, bar in day_bars.iterrows():
                if daily_trades >= 2: break
                if day_had_loss: break # 1-Loss Circuit Breaker

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

                # VIKALP 2 EXACT FORMULA:
                # 60% expansion capped at Rs 2800 hard stop
                hard_pts = 2800.0 / lot_size
                pct_stop_pts = net_credit * sl_pct
                stop_spread = min(net_credit * (1.0 + sl_pct), net_credit + hard_pts)
                is_hard_capped = (hard_pts < pct_stop_pts)

                be_threshold = net_credit * 0.75
                be_activated = False

                t_end = np.datetime64(pd.Timestamp(f"{dt} 15:15:00"))
                exit_time = None
                exit_spread = None
                exit_s_price = None
                exit_l_price = None
                exit_reason = "TIME_EXIT"

                idx_cur_s = pos_s + 1
                idx_cur_l = pos_l + 1

                min_spread_seen = net_credit
                max_spread_seen = net_credit

                while idx_cur_s < len(times_s) and idx_cur_l < len(times_l):
                    ts_s = times_s[idx_cur_s]
                    ts_l = times_l[idx_cur_l]
                    if ts_s != ts_l:
                        if ts_s < ts_l: idx_cur_s += 1
                        else: idx_cur_l += 1
                        continue

                    cur_s = ltp_s[idx_cur_s]
                    cur_l = ltp_l[idx_cur_l]
                    if cur_s <= 0 or cur_l <= 0:
                        idx_cur_s += 1
                        idx_cur_l += 1
                        continue

                    cur_spread = cur_s - cur_l
                    if cur_spread < min_spread_seen:
                        min_spread_seen = cur_spread
                    if cur_spread > max_spread_seen:
                        max_spread_seen = cur_spread

                    if ts_s >= t_end:
                        exit_time = pd.Timestamp(ts_s)
                        exit_spread = cur_spread
                        exit_s_price = cur_s
                        exit_l_price = cur_l
                        exit_reason = "TIME_EXIT"
                        break

                    if not be_activated and cur_spread <= be_threshold:
                        be_activated = True

                    if cur_spread <= target_spread:
                        exit_time = pd.Timestamp(ts_s)
                        exit_spread = target_spread
                        exit_s_price = cur_s
                        exit_l_price = cur_l
                        exit_reason = "TARGET"
                        break

                    if be_activated and cur_spread >= net_credit:
                        exit_time = pd.Timestamp(ts_s)
                        exit_spread = net_credit
                        exit_s_price = cur_s
                        exit_l_price = cur_l
                        exit_reason = "BREAKEVEN_SL"
                        break

                    if not be_activated and cur_spread >= stop_spread:
                        exit_time = pd.Timestamp(ts_s)
                        exit_spread = stop_spread
                        exit_s_price = cur_s
                        exit_l_price = cur_l
                        exit_reason = "STOP_LOSS"
                        break

                    idx_cur_s += 1
                    idx_cur_l += 1

                if exit_time is None:
                    exit_time = pd.Timestamp(f"{dt} 15:15:00")
                    last_s = ltp_s[min(idx_cur_s, len(ltp_s)-1)]
                    last_l = ltp_l[min(idx_cur_l, len(ltp_l)-1)]
                    exit_spread = last_s - last_l
                    exit_s_price = last_s
                    exit_l_price = last_l
                    exit_reason = "TIME_EXIT"

                spot_exit = float(spot_price_map.get(exit_time, spot_entry))

                points_won = net_credit - exit_spread
                gross_pnl = points_won * lot_size
                slippage = (net_credit * 0.005 + exit_spread * 0.005) * lot_size + 20.0 + (exit_spread * lot_size * 0.000625)
                net_pnl = gross_pnl - slippage

                if net_pnl <= 0:
                    day_had_loss = True

                duration_mins = int((exit_time - entry_time).total_seconds() / 60)
                mfe_points = round(net_credit - min_spread_seen, 2)
                mae_points = round(max_spread_seen - net_credit, 2)

                trades.append({
                    'trade_no': trade_id_counter,
                    'date': str(dt),
                    'year': dt.year,
                    'year_month': f"{dt.year}-{dt.month:02d}",
                    'day': day_name,
                    'strategy': strat_type,
                    'option_type': otype,
                    'target_expiry': str(target_exp),
                    'dte_days': dte_days,
                    'entry_time': str(entry_time),
                    'exit_time': str(exit_time),
                    'duration_mins': duration_mins,
                    'spot_entry': round(spot_entry, 2),
                    'spot_exit': round(spot_exit, 2),
                    'short_strike': int(short_k),
                    'long_strike': int(long_k),
                    'short_entry_price': round(float(entry_s_price), 2),
                    'long_entry_price': round(float(entry_l_price), 2),
                    'net_credit': round(float(net_credit), 2),
                    'short_exit_price': round(float(exit_s_price), 2),
                    'long_exit_price': round(float(exit_l_price), 2),
                    'exit_spread': round(float(exit_spread), 2),
                    'target_spread': round(float(target_spread), 2),
                    'stop_spread': round(float(stop_spread), 2),
                    'is_hard_capped': is_hard_capped,
                    'lot_size': int(lot_size),
                    'points_won': round(float(points_won), 2),
                    'gross_pnl': round(float(gross_pnl), 2),
                    'brokerage_and_charges': round(float(slippage), 2),
                    'net_pnl': round(float(net_pnl), 2),
                    'exit_reason': exit_reason,
                    'mfe_pts': mfe_points,
                    'mae_pts': mae_points,
                    'daily_vol_ann': round(float(sigma), 4)
                })

                trade_id_counter += 1
                daily_trades += 1
                in_pos = True
                pos_exit = exit_time

        print(f"Finished Year {yr} in {time.time() - t0:.1f}s")

    df_out = pd.DataFrame(trades)
    out_csv = "results/vikalp2_full_967_trades_postmortem.csv"
    df_out.to_csv(out_csv, index=False)
    print(f"\nSaved {len(df_out)} verified trades to {out_csv} in {time.time() - t_start:.1f}s!")

    # Summary Statistics
    print("\n" + "=" * 90)
    print("VIKALP 2 FULL POSTMORTEM AUDIT SUMMARY")
    print("=" * 90)
    pnl = df_out['net_pnl']
    wins = df_out[pnl > 0]
    losses = df_out[pnl <= 0]
    print(f"Total Trades: {len(df_out)}")
    print(f"Total Net PnL: Rs {pnl.sum():,.2f}")
    print(f"Win Rate: {len(wins) / len(df_out) * 100:.2f}% ({len(wins)} wins / {len(losses)} losses)")
    
    eq = pnl.cumsum()
    peak = eq.cummax()
    max_dd = (eq - peak).min()
    print(f"Max Drawdown: Rs {max_dd:,.2f}")
    print(f"Avg Trade PnL: Rs {pnl.mean():,.2f}")
    print(f"Avg Winning Trade: Rs {wins['net_pnl'].mean():,.2f}")
    print(f"Avg Losing Trade: Rs {losses['net_pnl'].mean():,.2f}")
    print(f"Total Charges & Slippage: Rs {df_out['brokerage_and_charges'].sum():,.2f}")

    # Exit reason distribution
    print("\n--- Exit Reason Breakdown ---")
    for reason, grp in df_out.groupby('exit_reason'):
        print(f"  • {reason:<14}: {len(grp):>3} trades ({len(grp)/len(df_out)*100:>5.1f}%) | Total PnL: Rs {grp['net_pnl'].sum():>10,.2f} | Avg: Rs {grp['net_pnl'].mean():>8,.2f}")

    # Annual breakdown
    print("\n--- Year-by-Year Performance ---")
    yr_grp = df_out.groupby('year').agg(
        trades=('trade_no', 'count'),
        wins=('net_pnl', lambda x: (x > 0).sum()),
        win_rate=('net_pnl', lambda x: (x > 0).mean() * 100),
        net_pnl=('net_pnl', 'sum'),
        charges=('brokerage_and_charges', 'sum')
    )
    print(yr_grp.to_string())

if __name__ == '__main__':
    run_vikalp2_postmortem()
