"""
scripts/compare_live_vs_backtest_pcr.py
========================================================================================
FULL 6-YEAR RIGOROUS COMPARATIVE BACKTEST (2020 TO 2026):
VARIANT 1: Original Backtest Rules (PCR >= 1.0 / <= 1.0, 5m Close vs 50 EMA & 9 >= 20 EMA)
VARIANT 2: LIVE Engine Rules (PCR >= 1.05 / <= 0.85 Dead Zone, Daily 20 EMA, 9 > 20 > 50 EMA Cascade)
========================================================================================
"""

import os
import sys
import time
import json
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

def run_comparison():
    print("=" * 110)
    print("  6-YEAR MASTER QUANT COMPARATIVE AUDIT: BACKTEST THRESHOLDS VS LIVE PRODUCTION RULES")
    print("=" * 110)
    print("Underlying:            NIFTY 50 Index Options (NSE India)")
    print("Historical Coverage:   January 1, 2020 to March 16, 2026 (75 Months, 1,540+ Sessions)")
    print("Dataset:               Real Options Master Parquet (74.3M rows) + Spot Parquet")
    print("Risk Controls:         40% Target Profit | 25% Auto-Trail to BE | Rs 2,800/Lot Hard SL | Max 2 Trades/Day")
    print("Slippage & Costs:      Kotak Neo Rs 0 Brokerage + Exchange/STT/GST + 0.5% Slippage per leg")
    print("=" * 110 + "\n")

    # 1. Load Spot Data
    print("1. Loading Master Spot Data & Resampling 5m Bars...")
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    df_spot = pd.read_parquet(spot_path)
    spot_price_map = dict(zip(df_spot.index, df_spot['close']))

    # Daily EMA 20 for LIVE Macro Direction Filter
    df_daily = df_spot.resample('1D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_daily['ema20'] = df_daily['close'].ewm(span=20, adjust=False).mean()
    df_daily['daily_bull'] = df_daily['close'] > df_daily['ema20']
    df_daily['daily_bear'] = df_daily['close'] < df_daily['ema20']
    daily_trend_map = {d.date(): 'BULL' if b else ('BEAR' if r else 'NEUTRAL') for d, b, r in zip(df_daily.index, df_daily['daily_bull'], df_daily['daily_bear'])}

    # Daily Volatility for Delta Sizing
    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = df_daily['returns'].rolling(30).std() * np.sqrt(252)
    df_daily['rolling_vol'] = df_daily['rolling_vol'].bfill().fillna(0.18)
    daily_vol_map = {d.date(): float(v) for d, v in df_daily['rolling_vol'].items()}

    # Resample 5m Bars
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

    # Configuration 1: Backtest Original Rules
    # Bull: Close > 50 EMA & EMA 9 >= 20 EMA & Green Candle
    # Bear: Close < 50 EMA & EMA 9 <= 20 EMA & Red Candle
    df_5m['bt_bull'] = (df_5m['close'] > df_5m['ema50']) & (df_5m['ema9'] >= df_5m['ema20']) & (df_5m['close'] > df_5m['ema9']) & df_5m['is_green']
    df_5m['bt_bear'] = (df_5m['close'] < df_5m['ema50']) & (df_5m['ema9'] <= df_5m['ema20']) & (df_5m['close'] < df_5m['ema9']) & df_5m['is_red']

    # Configuration 2: LIVE Production Rules
    # Bull: EMA 9 > EMA 20 > EMA 50 & Green Candle
    # Bear: EMA 9 < EMA 20 < EMA 50 & Red Candle
    df_5m['live_bull'] = (df_5m['ema9'] > df_5m['ema20']) & (df_5m['ema20'] > df_5m['ema50']) & df_5m['is_green']
    df_5m['live_bear'] = (df_5m['ema9'] < df_5m['ema20']) & (df_5m['ema20'] < df_5m['ema50']) & df_5m['is_red']

    options_path = "data/nifty_options_master_2020_2026_1min.parquet"
    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    valid_days = ['Tuesday', 'Wednesday', 'Thursday', 'Friday'] # Model 4 active trading days

    short_d = 0.50
    long_d = 0.10
    target_val = 0.40 # 40% decay
    be_val = 0.25     # 25% decay triggers breakeven trailing

    results = {
        'BACKTEST_RULES': [],
        'LIVE_RULES': []
    }

    print("2. Iterating Year-by-Year through Real Options Trajectories (2020-2026)...\n")

    for yr in years:
        t0 = time.time()
        print(f"Loading and analyzing Year {yr}...", flush=True)

        yr_table = pq.read_table(options_path, filters=[
            ('timestamp', '>=', pd.Timestamp(f'{yr}-01-01 00:00:00')),
            ('timestamp', '<=', pd.Timestamp(f'{yr}-12-31 23:59:59'))
        ], columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'close', 'oi'])
        df_yr = yr_table.to_pandas()
        df_yr['date'] = df_yr['timestamp'].dt.date
        df_yr['expiry_date'] = df_yr['expiry'].dt.date
        del yr_table

        # Precompute minute-by-minute PCR
        oi_agg = df_yr.groupby(['timestamp', 'option_type'])['oi'].sum().unstack(fill_value=0)
        pcr_series = oi_agg['PE'] / oi_agg['CE'].replace(0, 1)
        pcr_map_yr = pcr_series.to_dict()
        del oi_agg

        # Group trajectories by expiry & strike
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

        for config in ['BACKTEST_RULES', 'LIVE_RULES']:
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

                day_bars = df_5m[df_5m.index.date == dt]
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
                    if day_had_loss: break

                    close_t = bar['close_time_t']

                    # Window comparison:
                    # Backtest: 09:20 - 14:30
                    # LIVE: 09:30 - 14:30
                    if config == 'BACKTEST_RULES':
                        if not (pd.Timestamp('09:20:00').time() <= close_t <= pd.Timestamp('14:30:00').time()):
                            continue
                        is_bull = bool(bar['bt_bull'])
                        is_bear = bool(bar['bt_bear'])
                    else: # LIVE_RULES
                        if not (pd.Timestamp('09:30:00').time() <= close_t <= pd.Timestamp('14:30:00').time()):
                            continue
                        is_bull = bool(bar['live_bull'])
                        is_bear = bool(bar['live_bear'])

                    if not is_bull and not is_bear:
                        continue

                    # Macro Filter:
                    # In LIVE: Spot > Daily 20 EMA for Bull, Spot < Daily 20 EMA for Bear
                    if config == 'LIVE_RULES':
                        if is_bull and d_trend != 'BULL': continue
                        if is_bear and d_trend != 'BEAR': continue

                    # PCR Filter Comparison:
                    # Backtest: Bull PCR >= 1.0, Bear PCR <= 1.0 (No dead zone)
                    # LIVE: Bull PCR >= 1.05, Bear PCR <= 0.85 (Dead zone 0.85 - 1.05 skipped)
                    curr_pcr = pcr_map_yr.get(bar['close_time'], 1.0)
                    if config == 'BACKTEST_RULES':
                        if is_bull and curr_pcr < 1.0: continue
                        if is_bear and curr_pcr > 1.0: continue
                    else: # LIVE_RULES
                        if is_bull and curr_pcr < 1.05: continue
                        if is_bear and curr_pcr > 0.85: continue

                    entry_time = bar['close_time'] + pd.Timedelta(minutes=1)
                    if in_pos and pos_exit is not None and entry_time < pos_exit:
                        continue
                    else:
                        in_pos = False

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

                    # Targets & Risk controls
                    target_spread = net_credit * (1.0 - target_val)
                    be_threshold = net_credit * (1.0 - be_val)
                    
                    # Hard SL: min(60% net credit, Rs 2,800/lot)
                    max_loss_pts = 2800.0 / lot_size
                    stop_spread = net_credit + min(net_credit * 0.60, max_loss_pts)

                    be_activated = False
                    t_end = np.datetime64(f"{dt} 15:15:00")

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
                    # Realistic brokerage & statutory taxes
                    slippage = (net_credit * 0.005 + exit_spread * 0.005) * lot_size + 20.0 + (exit_spread * lot_size * 0.000625)
                    net_pnl = gross_pnl - slippage

                    if net_pnl <= 0:
                        day_had_loss = True

                    trade_rec = {
                        'date': dt,
                        'year': dt.year,
                        'entry_time': str(entry_time),
                        'exit_time': str(exit_time),
                        'direction': strat_type,
                        'net_credit': round(net_credit, 2),
                        'exit_spread': round(exit_spread, 2),
                        'lot_size': lot_size,
                        'gross_pnl': round(gross_pnl, 2),
                        'net_pnl': round(net_pnl, 2),
                        'exit_reason': exit_reason
                    }
                    results[config].append(trade_rec)

                    daily_trades += 1
                    in_pos = True
                    pos_exit = exit_time

        print(f"Completed Year {yr} in {time.time() - t0:.1f}s")

    # Generate Statistical Comparison Table
    print("\n" + "=" * 110)
    print("                    6-YEAR REAL PERFORMANCE AUDIT SUMMARY (2020 - 2026)")
    print("=" * 110)

    summary_rows = []
    for cfg in ['BACKTEST_RULES', 'LIVE_RULES']:
        tdf = pd.DataFrame(results[cfg])
        if tdf.empty:
            continue
        total_trades = len(tdf)
        wins = tdf[tdf['net_pnl'] > 0]
        losses = tdf[tdf['net_pnl'] <= 0]
        win_rate = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0
        total_net_pnl = tdf['net_pnl'].sum()
        total_gross_pnl = tdf['gross_pnl'].sum()
        avg_trade = total_net_pnl / total_trades if total_trades > 0 else 0.0

        # Cumulative Equity & Max Drawdown
        tdf['cum_pnl'] = tdf['net_pnl'].cumsum()
        tdf['peak'] = tdf['cum_pnl'].cummax()
        tdf['drawdown'] = tdf['cum_pnl'] - tdf['peak']
        max_dd = tdf['drawdown'].min()

        # Profit Factor
        gross_profit = wins['gross_pnl'].sum()
        gross_loss = abs(losses['gross_pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.nan

        summary_rows.append({
            'Configuration': 'Original Backtest (PCR>=1.0/<=1.0, 5m 50 EMA)' if cfg == 'BACKTEST_RULES' else 'LIVE Engine (PCR>=1.05/<=0.85, 9>20>50 Cascade, Daily 20 EMA)',
            'Total Trades': total_trades,
            'Win Rate (%)': f"{win_rate:.1f}%",
            'Gross PnL (Rs)': f"Rs {total_gross_pnl:,.2f}",
            'Net PnL (Rs)': f"Rs {total_net_pnl:,.2f}",
            'Max Drawdown (Rs)': f"Rs {max_dd:,.2f}",
            'Profit Factor': f"{profit_factor:.2f}",
            'Avg Trade (Rs)': f"Rs {avg_trade:,.2f}"
        })

    summary_df = pd.DataFrame(summary_rows)
    print(summary_df.to_string(index=False))
    print("=" * 110)

    # Save detailed JSON summary
    out_file = Path("results/backtest_comparison_results.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "summary": summary_rows,
            "backtest_trades_count": len(results['BACKTEST_RULES']),
            "live_trades_count": len(results['LIVE_RULES'])
        }, f, indent=2)
    print(f"\nDetailed metrics saved to: {out_file}\n")
    return summary_rows

if __name__ == "__main__":
    run_comparison()
