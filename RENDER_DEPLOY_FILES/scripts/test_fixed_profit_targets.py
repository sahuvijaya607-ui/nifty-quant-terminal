"""
scripts/test_fixed_profit_targets.py
Tests fixed rupee profit targets per lot (Rs 850, Rs 900, Rs 950, Rs 1000)
vs Vikalp 2 Baseline (40% decay) across 6 Full Years (2020 - 2026).
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

def simulate_day_for_config(config, dt, day_bars, day_cache, spot_price_map, daily_vol_map, daily_trend_map, pcr_map_yr):
    """
    Simulates trades for a single day for a given configuration.
    """
    target_mode = config['target_mode'] # 'PERCENT' or 'FIXED_INR'
    target_param = config['target_param'] # 0.40 or fixed rupees (e.g. 850, 900, 950, 1000)
    be_mode = config['be_mode'] # 'PCT_DECAY' or 'PROP_TARGET'
    
    trajs = day_cache['trajs']
    pe_strikes = day_cache['pe_strikes']
    ce_strikes = day_cache['ce_strikes']
    target_exp = day_cache['exp']
    lot_size = get_nifty_lot_size(dt)

    dte_days = (target_exp - dt).days
    dte_years = max(dte_days / 365.25, 0.001)
    sigma = daily_vol_map.get(dt, 0.16)
    d_trend = daily_trend_map.get(dt, 'NEUTRAL')

    short_d = 0.50
    long_d = 0.10
    sl_pct = 0.60
    hard_sl_rupees = 2800.0

    daily_trades = 0
    day_had_loss = False
    in_pos = False
    pos_exit = None

    day_trades = []

    for bar_ts, bar in day_bars.iterrows():
        if daily_trades >= 2: break
        if day_had_loss: break

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

        # Target Spread Definition
        if target_mode == 'PERCENT':
            target_spread = net_credit * (1.0 - target_param)
        else: # FIXED_INR per lot
            target_pts = target_param / lot_size
            target_spread = max(0.05, net_credit - target_pts)

        # Trailing Breakeven Threshold
        if be_mode == 'PCT_DECAY':
            be_threshold = net_credit * 0.75 # 25% decay
        elif be_mode == 'PROP_TARGET':
            # 50% of the target profit
            if target_mode == 'PERCENT':
                be_threshold = net_credit * (1.0 - (target_param * 0.5))
            else:
                be_pts = (target_param * 0.5) / lot_size
                be_threshold = max(0.05, net_credit - be_pts)
        elif be_mode == 'NONE':
            be_threshold = -9999.0 # Never activate BE

        # Stop Spread Definition (Hard SL ₹2800)
        hard_pts = hard_sl_rupees / lot_size
        pct_stop_pts = net_credit * sl_pct
        stop_spread = min(net_credit * (1.0 + sl_pct), net_credit + hard_pts)

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

        day_trades.append({
            'date': str(dt),
            'year': dt.year,
            'entry_time': entry_time,
            'exit_time': exit_time,
            'duration_mins': int((exit_time - entry_time).total_seconds() / 60),
            'net_credit': round(net_credit, 2),
            'exit_spread': round(exit_spread, 2),
            'lot_size': lot_size,
            'gross_pnl': round(gross_pnl, 2),
            'net_pnl': net_pnl,
            'exit_reason': exit_reason
        })

    return day_trades

def main():
    t_start = time.time()
    print("=" * 115)
    print("   QUANTITATIVE AUDIT: FIXED RUPEE TARGETS (Rs 850 - Rs 1000/LOT) vs 40% BASELINE (6 YEARS)")
    print("=" * 115)

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

    # Configurations to test:
    configs = [
        {'id': 'baseline_40pct', 'label': 'Baseline (40% Decay, 25% BE)', 'target_mode': 'PERCENT', 'target_param': 0.40, 'be_mode': 'PCT_DECAY'},
        {'id': 'fixed_850_prop', 'label': 'Target Rs 850/lot (BE 50%)', 'target_mode': 'FIXED_INR', 'target_param': 850.0, 'be_mode': 'PROP_TARGET'},
        {'id': 'fixed_850_25be', 'label': 'Target Rs 850/lot (BE 25% Dec)', 'target_mode': 'FIXED_INR', 'target_param': 850.0, 'be_mode': 'PCT_DECAY'},
        {'id': 'fixed_900_prop', 'label': 'Target Rs 900/lot (BE 50%)', 'target_mode': 'FIXED_INR', 'target_param': 900.0, 'be_mode': 'PROP_TARGET'},
        {'id': 'fixed_950_prop', 'label': 'Target Rs 950/lot (BE 50%)', 'target_mode': 'FIXED_INR', 'target_param': 950.0, 'be_mode': 'PROP_TARGET'},
        {'id': 'fixed_1000_prop', 'label': 'Target Rs 1,000/lot (BE 50%)', 'target_mode': 'FIXED_INR', 'target_param': 1000.0, 'be_mode': 'PROP_TARGET'},
        {'id': 'fixed_1000_25be', 'label': 'Target Rs 1,000/lot (BE 25% Dec)', 'target_mode': 'FIXED_INR', 'target_param': 1000.0, 'be_mode': 'PCT_DECAY'},
    ]

    results = {c['id']: [] for c in configs}

    for yr in years:
        t0 = time.time()
        print(f"Processing Year {yr} across {len(configs)} configurations...", flush=True)

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

        for dt in yr_dates:
            day_name = pd.to_datetime(dt).day_name()
            if day_name not in valid_days:
                continue

            day_cache = daily_cache_yr[dt]
            day_bars = df_5m_sub[df_5m_sub.index.date == dt]
            if day_bars.empty:
                continue

            for cfg in configs:
                trades_sim = simulate_day_for_config(
                    cfg, dt, day_bars, day_cache, spot_price_map, daily_vol_map, daily_trend_map, pcr_map_yr
                )
                results[cfg['id']].extend(trades_sim)

        print(f"Year {yr} completed in {time.time() - t0:.1f}s.")

    # Generate Metrics Summary
    summary_rows = []
    dfs = {}

    for cfg in configs:
        c_id = cfg['id']
        t_list = results[c_id]
        if not t_list: continue
        df_c = pd.DataFrame(t_list)
        dfs[c_id] = df_c

        net_pnl = df_c['net_pnl'].sum()
        total_trades = len(df_c)
        wins = (df_c['net_pnl'] > 0).sum()
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0

        # Drawdown
        df_c['cum_pnl'] = df_c['net_pnl'].cumsum()
        df_c['peak'] = df_c['cum_pnl'].cummax()
        df_c['dd'] = df_c['cum_pnl'] - df_c['peak']
        max_dd = df_c['dd'].min()

        gw = df_c[df_c['net_pnl'] > 0]['net_pnl'].sum()
        gl = abs(df_c[df_c['net_pnl'] <= 0]['net_pnl'].sum())
        pf = gw / gl if gl > 0 else 99.0

        targets_hit = (df_c['exit_reason'] == 'TARGET').sum()
        sl_hit = (df_c['exit_reason'] == 'STOP_LOSS').sum()
        be_hit = (df_c['exit_reason'] == 'BREAKEVEN_SL').sum()
        time_exit = (df_c['exit_reason'] == 'TIME_EXIT').sum()
        avg_dur = df_c['duration_mins'].mean()
        avg_win = df_c[df_c['net_pnl'] > 0]['net_pnl'].mean()
        avg_loss = df_c[df_c['net_pnl'] <= 0]['net_pnl'].mean()

        summary_rows.append({
            'Config ID': c_id,
            'Strategy': cfg['label'],
            'Net Profit (Rs)': round(net_pnl, 2),
            'Win Rate (%)': round(win_rate, 2),
            'Max Drawdown (Rs)': round(max_dd, 2),
            'Return / Max DD': round(net_pnl / abs(max_dd), 2) if abs(max_dd) > 0 else 0,
            'Profit Factor': round(pf, 2),
            'Total Trades': total_trades,
            'Target Hits': targets_hit,
            'SL Hits': sl_hit,
            'BE Hits': be_hit,
            'Time Exits': time_exit,
            'Avg Duration (min)': round(avg_dur, 1),
            'Avg Win (Rs)': round(avg_win, 2),
            'Avg Loss (Rs)': round(avg_loss, 2)
        })

    summary_df = pd.DataFrame(summary_rows)

    print("\n" + "=" * 120)
    print("                      HEAD-TO-HEAD AUDIT SUMMARY (2020 - 2026)")
    print("=" * 120)
    print(summary_df[['Strategy', 'Net Profit (Rs)', 'Win Rate (%)', 'Max Drawdown (Rs)', 'Return / Max DD', 'Profit Factor', 'Total Trades', 'Avg Win (Rs)', 'Avg Loss (Rs)']].to_string(index=False))

    # Year-by-Year breakdown for Baseline vs Rs 850 vs Rs 1000
    print("\n" + "=" * 120)
    print("YEAR-BY-YEAR NET PROFIT (RS) COMPARISON:")
    print("=" * 120)
    compare_ids = ['baseline_40pct', 'fixed_850_prop', 'fixed_900_prop', 'fixed_950_prop', 'fixed_1000_prop']
    yr_matrix = []
    for yr in years:
        row = {'Year': yr}
        for cid in compare_ids:
            if cid in dfs:
                sub = dfs[cid][dfs[cid]['year'] == yr]
                row[cid] = round(sub['net_pnl'].sum(), 2)
            else:
                row[cid] = 0.0
        yr_matrix.append(row)
    yr_df = pd.DataFrame(yr_matrix)
    print(yr_df.to_string(index=False))

    summary_df.to_csv('results/fixed_profit_targets_summary.csv', index=False)
    yr_df.to_csv('results/fixed_profit_targets_yearly.csv', index=False)
    print(f"\nAudit completed in {time.time() - t_start:.1f}s. Files saved to results/")

if __name__ == '__main__':
    main()
