"""
scripts/test_max2_trades_unconditional.py
Compares Vikalp 2 with:
1. Stop on 1 Loss (Baseline): daily_trades <= 2, stop if day had a loss.
2. Max 2 Trades Unconditional: daily_trades <= 2, allow Trade 2 even if Trade 1 was a loss.
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

def run_test():
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
    valid_days = ['Tuesday', 'Wednesday', 'Thursday', 'Friday']

    trades_baseline = [] # stop on 1 loss
    trades_unconditional = [] # max 2 trades regardless of loss

    for yr in years:
        t0 = time.time()
        print(f"Testing Year {yr}...", flush=True)

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
            if not expiries: continue
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

        for allow_after_loss in [False, True]:
            target_list = trades_unconditional if allow_after_loss else trades_baseline

            for dt in yr_dates:
                day_name = pd.to_datetime(dt).day_name()
                if day_name not in valid_days: continue

                day_cache = daily_cache_yr[dt]
                trajs = day_cache['trajs']
                pe_strikes = day_cache['pe_strikes']
                ce_strikes = day_cache['ce_strikes']
                target_exp = day_cache['exp']
                lot_size = get_nifty_lot_size(dt)

                day_bars = df_5m_sub[df_5m_sub.index.date == dt]
                if day_bars.empty: continue

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
                    if not allow_after_loss and day_had_loss: break

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
                        idx_s = np.argmin(np.abs(deltas - (-0.50)))
                        idx_l = np.argmin(np.abs(deltas - (-0.10)))
                        short_k = available_strikes[idx_s]
                        long_k = available_strikes[idx_l]
                        if short_k <= long_k: continue
                    else:
                        idx_s = np.argmin(np.abs(deltas - 0.50))
                        idx_l = np.argmin(np.abs(deltas - 0.10))
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

                    target_spread = net_credit * 0.60
                    hard_pts = 2800.0 / lot_size
                    stop_spread = min(net_credit * 1.60, net_credit + hard_pts)
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

                        cur_s = ltp_s[idx_cur_s]
                        cur_l = ltp_l[idx_cur_l]
                        if cur_s <= 0 or cur_l <= 0:
                            idx_cur_s += 1
                            idx_cur_l += 1
                            continue

                        cur_spread = cur_s - cur_l
                        if ts_s >= t_end:
                            exit_time = pd.Timestamp(ts_s)
                            exit_spread = cur_spread
                            exit_reason = "TIME_EXIT"
                            break

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
                        last_s = ltp_s[min(idx_cur_s, len(ltp_s)-1)]
                        last_l = ltp_l[min(idx_cur_l, len(ltp_l)-1)]
                        exit_spread = last_s - last_l
                        exit_reason = "TIME_EXIT"

                    points_won = net_credit - exit_spread
                    gross_pnl = points_won * lot_size
                    slippage = (net_credit * 0.005 + exit_spread * 0.005) * lot_size + 20.0 + (exit_spread * lot_size * 0.000625)
                    net_pnl = round(gross_pnl - slippage, 2)

                    if net_pnl <= 0:
                        day_had_loss = True

                    in_pos = True
                    pos_exit = exit_time
                    daily_trades += 1

                    target_list.append({
                        'date': str(dt),
                        'year': dt.year,
                        'entry_time': str(entry_time),
                        'exit_time': str(exit_time),
                        'trade_of_day': daily_trades,
                        'strategy': strat_type,
                        'net_credit': round(net_credit, 2),
                        'exit_spread': round(exit_spread, 2),
                        'lot_size': lot_size,
                        'net_pnl': net_pnl,
                        'exit_reason': exit_reason
                    })

    df_base = pd.DataFrame(trades_baseline)
    df_uncond = pd.DataFrame(trades_unconditional)

    df_base.to_csv('results/vikalp2_stop_on_1loss.csv', index=False)
    df_uncond.to_csv('results/vikalp2_max2_unconditional.csv', index=False)

    print("\n" + "=" * 100)
    print("COMPARISON: STOP ON 1 LOSS (BASELINE) vs MAX 2 TRADES UNCONDITIONAL (ALLOW TRADE 2 AFTER LOSS)")
    print("=" * 100)
    print(f"{'Metric':<32} | {'Stop on 1 Loss (Baseline)':<26} | {'Max 2 Trades Unconditional':<26}")
    print("-" * 100)
    print(f"{'Total Net Profit':<32} | Rs {df_base['net_pnl'].sum():>23,.2f} | Rs {df_uncond['net_pnl'].sum():>23,.2f}")
    print(f"{'Total Trades':<32} | {len(df_base):>26} | {len(df_uncond):>26}")
    
    base_wins = (df_base['net_pnl'] > 0).sum()
    uncond_wins = (df_uncond['net_pnl'] > 0).sum()
    print(f"{'Win Rate':<32} | {(base_wins / len(df_base))*100:>25.2f}% | {(uncond_wins / len(df_uncond))*100:>25.2f}%")

    base_eq = df_base['net_pnl'].cumsum()
    base_dd = (base_eq - base_eq.cummax()).min()
    uncond_eq = df_uncond['net_pnl'].cumsum()
    uncond_dd = (uncond_eq - uncond_eq.cummax()).min()

    print(f"{'Max Drawdown':<32} | Rs {base_dd:>23,.2f} | Rs {uncond_dd:>23,.2f}")
    print(f"{'Return / Max Drawdown':<32} | {df_base['net_pnl'].sum() / abs(base_dd):>25.2f}x | {df_uncond['net_pnl'].sum() / abs(uncond_dd):>25.2f}x")

    base_gw = df_base[df_base['net_pnl'] > 0]['net_pnl'].sum()
    base_gl = abs(df_base[df_base['net_pnl'] <= 0]['net_pnl'].sum())
    base_pf = base_gw / base_gl if base_gl > 0 else 99.0

    uncond_gw = df_uncond[df_uncond['net_pnl'] > 0]['net_pnl'].sum()
    uncond_gl = abs(df_uncond[df_uncond['net_pnl'] <= 0]['net_pnl'].sum())
    uncond_pf = uncond_gw / uncond_gl if uncond_gl > 0 else 99.0

    print(f"{'Profit Factor':<32} | {base_pf:>26.2f} | {uncond_pf:>26.2f}")

    # Microscopic analysis of Trade 2 after Trade 1 loss
    print("\n" + "=" * 100)
    print("MICROSCOPIC FORENSIC AUDIT: WHAT HAPPENED WHEN TRADE 2 WAS TAKEN AFTER A LOSS?")
    print("=" * 100)

    days_multi = df_uncond.groupby('date').filter(lambda g: len(g) >= 2)
    after_loss_records = []

    for dt_val, grp in days_multi.groupby('date'):
        t1 = grp.iloc[0]
        t2 = grp.iloc[1]
        if t1['net_pnl'] <= 0:
            after_loss_records.append({
                'date': dt_val,
                'year': t1['year'],
                't1_pnl': t1['net_pnl'],
                't1_reason': t1['exit_reason'],
                't2_pnl': t2['net_pnl'],
                't2_reason': t2['exit_reason'],
                'day_total_pnl': t1['net_pnl'] + t2['net_pnl'],
                't2_won': t2['net_pnl'] > 0,
                'recovered_day': (t1['net_pnl'] + t2['net_pnl']) >= 0
            })

    df_al = pd.DataFrame(after_loss_records)
    print(f"Total Days where Trade 1 was a LOSS and Trade 2 entered : {len(df_al)} days")
    print(f"  • Trade 2 WINS (Green)                                : {(df_al['t2_won']).sum()} days ({(df_al['t2_won']).mean()*100:.1f}% win rate)")
    print(f"  • Trade 2 LOSSES (Double Loss Days)                   : {(~df_al['t2_won']).sum()} days")
    print(f"  • Days fully converted from Negative to Positive/Flat  : {(df_al['recovered_day']).sum()} days")
    print(f"  • Net PnL generated by Trade 2 on these recovery days : Rs {df_al['t2_pnl'].sum():>15,.2f}")
    print(f"  • Average Trade 2 PnL per attempt                     : Rs {df_al['t2_pnl'].mean():>15,.2f}")

    # Yearly comparison
    print("\n" + "=" * 100)
    print("YEARLY NET PROFIT COMPARISON:")
    print("=" * 100)
    print(f"{'Year':<6} | {'Baseline (Stop on 1 Loss)':<26} | {'Max 2 Trades Unconditional':<26} | {'Diff (Rs)':<12} | {'Winner'}")
    print("-" * 100)
    for yr in years:
        b_pnl = df_base[df_base['year'] == yr]['net_pnl'].sum()
        u_pnl = df_uncond[df_uncond['year'] == yr]['net_pnl'].sum()
        diff = u_pnl - b_pnl
        winner = "MAX 2 TRADES" if diff > 0 else "STOP ON 1 LOSS"
        print(f"{yr:<6} | Rs {b_pnl:>22,.2f} | Rs {u_pnl:>22,.2f} | Rs {diff:>9,.2f} | {winner}")

    df_al.to_csv('results/trade2_after_loss_forensics.csv', index=False)
    print(f"\nSaved forensics to results/trade2_after_loss_forensics.csv")

if __name__ == '__main__':
    run_test()
