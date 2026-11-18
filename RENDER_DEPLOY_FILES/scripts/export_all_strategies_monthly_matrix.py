"""
scripts/export_all_strategies_monthly_matrix.py
Generates exact Month-by-Month Net PnL matrix for ALL 7 Credit Spread Strategies on Real Parquet Vault.
Saves to results/all_strategies_monthly_matrix.csv and generates trade ledgers.
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

def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().bfill()
    hl2 = (high + low) / 2.0
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)
    direction = np.ones(len(df), dtype=int)
    for i in range(1, len(df)):
        if close.iloc[i] > upper_band.iloc[i-1]:
            direction[i] = 1
        elif close.iloc[i] < lower_band.iloc[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
            if direction[i] == 1 and lower_band.iloc[i] < lower_band.iloc[i-1]:
                lower_band.iloc[i] = lower_band.iloc[i-1]
            if direction[i] == -1 and upper_band.iloc[i] > upper_band.iloc[i-1]:
                upper_band.iloc[i] = upper_band.iloc[i-1]
    return pd.Series(direction, index=df.index)

def run_export():
    print("=" * 120)
    print("      EXPORTING MONTH-BY-MONTH BACKTEST DATA ACROSS ALL STRATEGIES (REAL PARQUET VAULT)      ")
    print("=" * 120)

    # Spot Data
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

    df_5m['tp'] = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
    df_5m['vwap'] = df_5m.groupby('date')['tp'].transform(lambda s: s.expanding().mean())

    st1 = compute_supertrend(df_5m, period=7, multiplier=2.0)
    st2 = compute_supertrend(df_5m, period=10, multiplier=3.0)
    st3 = compute_supertrend(df_5m, period=14, multiplier=4.0)
    df_5m['st_bull'] = (st1 == 1) & (st2 == 1) & (st3 == 1)
    df_5m['st_bear'] = (st1 == -1) & (st2 == -1) & (st3 == -1)

    time_mask = (df_5m['close_time_t'] >= pd.Timestamp('09:20:00').time()) & (df_5m['close_time_t'] <= pd.Timestamp('14:30:00').time())
    df_5m_sub = df_5m[time_mask].copy()

    # Options Data
    clean_path = Path("data/nifty_option_chain_1min_clean.parquet")
    opt_table = pq.read_table(clean_path, columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'oi'])
    df_opt = opt_table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['expiry_date'] = df_opt['expiry'].dt.date
    all_dates = sorted(df_opt['date'].unique())

    oi_agg = df_opt.groupby(['timestamp', 'option_type'])['oi'].sum().unstack(fill_value=0)
    pcr_map = (oi_agg['PE'] / oi_agg['CE'].replace(0, 1)).to_dict()

    daily_cache = {}
    for dt, day_df in df_opt.groupby('date'):
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
                'ltp': grp_sorted['ltp'].fillna(0.0).to_numpy()
            }
        daily_cache[dt] = {
            'exp': target_exp,
            'trajs': trajs,
            'pe_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'PE']),
            'ce_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'CE'])
        }

    def simulate_strategy_trades(strat_type, short_delta, long_delta, days_filter, wed_strict=False):
        trades = []
        equity = 200000.0

        for dt in all_dates:
            day_name = pd.to_datetime(dt).day_name()
            if day_name not in days_filter:
                continue
            if dt not in daily_cache:
                continue
            day = daily_cache[dt]
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
                if daily_trades >= 2:
                    break
                entry_time = bar['close_time'] + pd.Timedelta(minutes=1)
                if in_pos and pos_exit is not None and entry_time < pos_exit:
                    continue
                else:
                    in_pos = False

                curr_pcr = pcr_map.get(bar['close_time'], 1.0)
                is_bull = False
                is_bear = False

                if strat_type == 'EMA_PULLBACK':
                    c_bull = (bar['close'] > bar['ema50']) & (bar['ema9'] >= bar['ema20']) & (bar['close'] > bar['ema9']) & bar['is_green']
                    c_bear = (bar['close'] < bar['ema50']) & (bar['ema9'] <= bar['ema20']) & (bar['close'] < bar['ema9']) & bar['is_red']
                    if day_name == 'Wednesday' and wed_strict:
                        if c_bull and curr_pcr >= 1.15:
                            is_bull = True
                        elif c_bear and curr_pcr <= 0.85:
                            is_bear = True
                    else:
                        if c_bull and curr_pcr >= 1.0:
                            is_bull = True
                        elif c_bear and curr_pcr <= 1.0:
                            is_bear = True

                elif strat_type == 'TRIPLE_CONFLUENCE':
                    c_bull = bar['st_bull'] and (bar['close'] > bar['ema50']) and (bar['ema9'] >= bar['ema20']) & bar['is_green']
                    c_bear = bar['st_bear'] and (bar['close'] < bar['ema50']) and (bar['ema9'] <= bar['ema20']) & bar['is_red']
                    if c_bull and curr_pcr >= 1.0:
                        is_bull = True
                    elif c_bear and curr_pcr <= 1.0:
                        is_bear = True

                elif strat_type == 'SUPERTREND_VWAP':
                    st_bull = bar['st_bull'] and (bar['close'] > bar['vwap']) and bar['is_green']
                    st_bear = bar['st_bear'] and (bar['close'] < bar['vwap']) and bar['is_red']
                    if st_bull and curr_pcr >= 0.95:
                        is_bull = True
                    elif st_bear and curr_pcr <= 1.05:
                        is_bear = True

                if not is_bull and not is_bear:
                    continue

                if entry_time not in spot_price_map:
                    continue
                spot_entry = float(spot_price_map[entry_time])

                otype = "PE" if is_bull else "CE"
                available_strikes = pe_strikes if is_bull else ce_strikes
                if len(available_strikes) < 2:
                    continue

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
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'type': "BULL_PUT_SPREAD" if is_bull else "BEAR_CALL_SPREAD",
                    'short_strike': short_k,
                    'long_strike': long_k,
                    'entry_credit': round(net_credit, 2),
                    'exit_spread': round(exit_spread, 2),
                    'lot_size': lot_size,
                    'gross_pnl': round(gross_pnl, 2),
                    'slippage': round(slip, 2),
                    'net_pnl': round(net_pnl, 2),
                    'exit_reason': exit_reason,
                    'equity': round(equity, 2)
                })
                daily_trades += 1
                in_pos = True
                pos_exit = exit_time

        return pd.DataFrame(trades)

    lineup = [
        ("Strat_1_4Day_ATM_D50_L10", 'EMA_PULLBACK', 0.50, 0.10, ['Tuesday', 'Wednesday', 'Thursday', 'Friday'], True),
        ("Strat_2_Pure_ATM_D50_L10", 'EMA_PULLBACK', 0.50, 0.10, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat_3_Pure_ATM_D50_L15", 'EMA_PULLBACK', 0.50, 0.15, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat_4_Deep_Wing_D45_L10", 'EMA_PULLBACK', 0.45, 0.10, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat_5_Benchmark_D45_L15", 'EMA_PULLBACK', 0.45, 0.15, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat_6_Triple_ST_EMA",     'TRIPLE_CONFLUENCE', 0.45, 0.15, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat_7_SuperTrend_AllDays",'SUPERTREND_VWAP', 0.45, 0.15, ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'], False),
    ]

    monthly_dict = {}
    all_months = None

    for code, stype, sd, ld, days, w_strict in lineup:
        print(f"Generating full backtest ledger for {code}...")
        df_trd = simulate_strategy_trades(stype, sd, ld, days, w_strict)
        if df_trd is not None and not df_trd.empty:
            out_trd = Path(f"results/trades_{code}.csv")
            df_trd.to_csv(out_trd, index=False)
            monthly = df_trd.groupby('year_month')['net_pnl'].sum().round(2)
            monthly_dict[code] = monthly
            if all_months is None:
                all_months = sorted(list(monthly.index))

    # Build monthly matrix dataframe
    matrix_df = pd.DataFrame(index=all_months)
    for code, mseries in monthly_dict.items():
        matrix_df[code] = mseries

    matrix_df['TOTAL_SUM'] = matrix_df.sum(axis=1).round(2)
    matrix_csv = Path("results/all_strategies_monthly_matrix.csv")
    matrix_df.to_csv(matrix_csv)
    print(f"\nSaved Monthly Matrix to {matrix_csv}")
    print("\nMONTHLY NET PNL MATRIX (1 LOT FIXED REAL DATA):")
    print(matrix_df.to_string())

if __name__ == '__main__':
    run_export()
