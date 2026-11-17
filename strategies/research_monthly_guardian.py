"""
strategies/research_monthly_guardian.py
Diagnosing and engineering the "Monthly Guardian" rules:
Testing Daily Trend Alignment, Max 1 Loss/Day, Breakeven Trailing, and Monthly Floor Lock.
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

# 1. Spot Data Setup
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

# Test across 2021 and 2022 (the toughest years) with various filter permutations
years = [2021, 2022]
options_path = "data/nifty_options_master_2020_2026_1min.parquet"

# Variations to test:
# Var 1: Baseline (Unfiltered, All 5 days, 35D/15D)
# Var 2: Daily Trend Filter (Only Bull trades on Bull days, Bear trades on Bear days)
# Var 3: Max 1 Loss Per Day (If Trade 1 is loss -> stop day)
# Var 4: Monthly Lock at Rs 3,500 (If month reaches 3.5k -> preserve floor at 2.5k)
# Var 5: Asymmetric ATM (50D/10D) + Daily Trend Filter + 1 Loss/Day Circuit Breaker

print("Setting up daily caches for 2021-2022...")
daily_caches = {}
pcr_maps = {}

for yr in years:
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
    pcr_maps[yr] = pcr_series.to_dict()
    del oi_agg

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
        daily_caches[dt] = {
            'yr': yr,
            'exp': target_exp,
            'trajs': trajs,
            'pe_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'PE']),
            'ce_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'CE'])
        }
    del df_yr
    gc.collect()

print(f"Loaded {len(daily_caches)} sessions for 2021-2022.")

def test_engine(short_d=0.35, long_d=0.15, use_daily_trend=False, max_loss_day=False, monthly_lock=0, tgt_pct=0.40, sl_pct=0.60):
    all_trades = []
    
    # Track monthly PnL for monthly lock
    curr_month = None
    month_pnl = 0.0
    month_locked = False

    for dt in sorted(daily_caches.keys()):
        m_str = f"{dt.year}-{dt.month:02d}"
        if m_str != curr_month:
            curr_month = m_str
            month_pnl = 0.0
            month_locked = False

        if monthly_lock > 0 and month_locked:
            continue

        day_cache = daily_caches[dt]
        yr = day_cache['yr']
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
            if max_loss_day and day_had_loss: break # Circuit breaker: Stop after 1 loss

            entry_time = bar['close_time'] + pd.Timedelta(minutes=1)
            if in_pos and pos_exit is not None and entry_time < pos_exit:
                continue
            else:
                in_pos = False

            is_bull = bool(bar['is_bull'])
            is_bear = bool(bar['is_bear'])
            if not is_bull and not is_bear: continue

            # Daily Trend Alignment Filter
            if use_daily_trend:
                if is_bull and d_trend != 'BULL': continue # Skip bull if daily trend is not bull
                if is_bear and d_trend != 'BEAR': continue # Skip bear if daily trend is not bear

            curr_pcr = pcr_maps[yr].get(bar['close_time'], 1.0)
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

            if net_pnl <= 0:
                day_had_loss = True

            month_pnl += net_pnl
            if monthly_lock > 0 and month_pnl >= monthly_lock:
                month_locked = True

            all_trades.append({
                'date': dt,
                'year_month': m_str,
                'net_pnl': net_pnl
            })

            daily_trades += 1
            in_pos = True
            pos_exit = exit_time

    df_t = pd.DataFrame(all_trades)
    monthly = df_t.groupby('year_month')['net_pnl'].sum()
    return {
        'total_pnl': df_t['net_pnl'].sum(),
        'trades': len(df_t),
        'win_rate': (df_t['net_pnl'] > 0).mean() * 100,
        'pos_months': (monthly > 0).sum(),
        'months_ge_3k': (monthly >= 3000).sum(),
        'months_ge_5k': (monthly >= 5000).sum(),
        'total_months': len(monthly),
        'min_month_pnl': monthly.min(),
        'avg_month_pnl': monthly.mean()
    }

print("\n--- EXPERIMENT RESULTS ON 2021-2022 (TOUGHEST YEARS) ---")
print("1. Baseline (35D/15D, No Filter):")
print(test_engine(short_d=0.35, long_d=0.15, use_daily_trend=False, max_loss_day=False))

print("\n2. + Daily Trend Filter (Align with Daily 20 EMA):")
print(test_engine(short_d=0.35, long_d=0.15, use_daily_trend=True, max_loss_day=False))

print("\n3. + Daily Trend Filter + 1 Loss/Day Circuit Breaker:")
print(test_engine(short_d=0.35, long_d=0.15, use_daily_trend=True, max_loss_day=True))

print("\n4. ATM 50D/10D + Daily Trend Filter + 1 Loss/Day Circuit Breaker:")
print(test_engine(short_d=0.50, long_d=0.10, use_daily_trend=True, max_loss_day=True))

print("\n5. Balanced 45D/15D + Daily Trend Filter + 1 Loss/Day Circuit Breaker:")
print(test_engine(short_d=0.45, long_d=0.15, use_daily_trend=True, max_loss_day=True))

print("\n6. Balanced 40D/15D + Daily Trend Filter + 1 Loss/Day + Monthly Lock at Rs 4,000:")
print(test_engine(short_d=0.40, long_d=0.15, use_daily_trend=True, max_loss_day=True, monthly_lock=4000))
