"""
========================================================================================
QUANT STRATEGY: 6-YEAR SUPERTREND DIRECTIONAL CREDIT SPREAD (2020-2026 TOP 10 OPTIMIZATION)
========================================================================================
Architecture & Optimization Matrix:
- Starting Capital: Rs. 2,00,000.
- Sizing: 1 Lot (1 x get_nifty_lot_size(date)).
- Signal Time: 09:20:00 (using Daily Spot OHLC up to yesterday).
- Execution Time: 09:21:00 entry, forced square-off at 15:15:00.
- Smart Expiry: Nearest weekly expiry strictly after today (expiry.date() > trade_date).
- Costs: Kotak Neo Rs. 0 Brokerage + 0.15% Slippage on traded turnover.

Test Matrix (40 Combinations):
- 10 Supertrend (Period, Multiplier) settings:
  [(7, 3), (7, 2), (10, 3), (10, 2), (10, 1.5), (14, 3), (14, 2), (5, 2), (12, 3), (20, 2)]
- 4 Target & Double-SL pairs:
  [(0.30, 0.60), (0.40, 0.80), (0.50, 1.00), (0.60, 1.20)]

Output Files:
- results/st_cs_6yr_all40.csv (All 40 ranked by Return-to-Drawdown Ratio)
- results/st_cs_6yr_best_daily.csv (Complete day-by-day trade ledger for Rank #1)
========================================================================================
"""

import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import scipy.stats as si
import pyarrow.parquet as pq

# Add project root to sys.path
sys.path.insert(0, os.path.abspath("."))
from utils.nifty_lot_size import get_nifty_lot_size


def calc_bs_deltas(spot: float, strikes: np.ndarray, dte_years: float, r: float, sigma: float, option_type: str) -> np.ndarray:
    """
    Computes fast vectorized Black-Scholes deltas for option strikes.
    """
    strikes = np.asarray(strikes, dtype=float)
    T = max(float(dte_years), 0.001)
    sig = max(float(sigma), 0.05)
    d1 = (np.log(spot / strikes) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    if option_type == 'CE':
        return si.norm.cdf(d1)
    else:
        return si.norm.cdf(d1) - 1.0


def calculate_supertrend(df_daily: pd.DataFrame, period: int, multiplier: float) -> pd.Series:
    """
    Computes Supertrend trend series (1 = Bullish/Green, -1 = Bearish/Red).
    """
    df = df_daily[['open', 'high', 'low', 'close']].copy()
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = (df['high'] - df['prev_close']).abs()
    df['tr3'] = (df['low'] - df['prev_close']).abs()
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['tr'].ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    hl2 = (df['high'] + df['low']) / 2
    basic_upper = hl2 + multiplier * df['atr']
    basic_lower = hl2 - multiplier * df['atr']

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = pd.Series(index=df.index, dtype=int)

    for i in range(period, len(df)):
        prev_close = df['close'].iloc[i-1]
        
        if basic_upper.iloc[i] < final_upper.iloc[i-1] or prev_close > final_upper.iloc[i-1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]
            
        if basic_lower.iloc[i] > final_lower.iloc[i-1] or prev_close < final_lower.iloc[i-1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]
            
        prev_trend = trend.iloc[i-1] if not np.isnan(trend.iloc[i-1]) else 1
        curr_close = df['close'].iloc[i]
        
        if prev_trend == 1:
            if curr_close < final_lower.iloc[i]:
                trend.iloc[i] = -1
            else:
                trend.iloc[i] = 1
        else:
            if curr_close > final_upper.iloc[i]:
                trend.iloc[i] = 1
            else:
                trend.iloc[i] = -1

    return trend


def run_6yr_optimization():
    t_start = time.time()
    print("=" * 115)
    print("   6-YEAR SUPERTREND DIRECTIONAL CREDIT SPREAD (2020-2026 TOP 10 OPTIMIZATION)   ")
    print("=" * 115)

    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    opt_path = Path("data/nifty_options_master_2020_2026_1min.parquet")
    res_dir = Path("results")
    res_dir.mkdir(parents=True, exist_ok=True)
    out_all40 = res_dir / "st_cs_6yr_all40.csv"
    out_best_daily = res_dir / "st_cs_6yr_best_daily.csv"

    if not spot_path.exists() or not opt_path.exists():
        print("Error: Master datasets not found.")
        sys.exit(1)

    # 1. Prepare Daily Spot OHLC, Rolling Volatility, and 10 Supertrends
    print("-> Loading Daily Spot Data and computing 10 Supertrend signals...")
    df_spot = pd.read_parquet(spot_path)
    df_daily = df_spot.resample('1D').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_daily['candle_color'] = np.where(df_daily['close'] > df_daily['open'], 'GREEN', np.where(df_daily['close'] < df_daily['open'], 'RED', 'DOJI'))
    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = (df_daily['returns'].rolling(20).std() * np.sqrt(252)).fillna(0.18).clip(lower=0.10, upper=0.45)

    st_settings = [
        (7, 3.0), (7, 2.0), (10, 3.0), (10, 2.0), (10, 1.5),
        (14, 3.0), (14, 2.0), (5, 2.0), (12, 3.0), (20, 2.0)
    ]
    target_sl_pairs = [
        (0.30, 0.60), (0.40, 0.80), (0.50, 1.00), (0.60, 1.20)
    ]

    # Precalculate signals on daily spot for each setting
    signals_dict = {}
    for p, m in st_settings:
        st_series = calculate_supertrend(df_daily, p, m)
        yest_st = st_series.shift(1)
        yest_c = df_daily['candle_color'].shift(1)
        # +1 = Bull Put Spread, -1 = Bear Call Spread, 0 = Skip
        sig = np.where((yest_st == 1) & (yest_c == 'GREEN'), 1,
              np.where((yest_st == -1) & (yest_c == 'RED'), -1, 0))
        signals_dict[(p, m)] = pd.Series(sig, index=df_daily.index)

    # Pre-index dates that need trade evaluation
    # Date needs Bull trade if any setting has +1, Bear trade if any setting has -1
    all_trade_dates = [d.date() for d in df_daily.index if d.year >= 2020]
    date_needs_bull = {}
    date_needs_bear = {}
    for d in df_daily.index:
        dt = d.date()
        bull_need = any(signals_dict[s].loc[d] == 1 for s in st_settings)
        bear_need = any(signals_dict[s].loc[d] == -1 for s in st_settings)
        date_needs_bull[dt] = bull_need
        date_needs_bear[dt] = bear_need

    print(f"-> Pre-calculated 10 Supertrend signals across {len(all_trade_dates)} trading sessions (2020-2026).")

    # Day-level trade outcomes storage:
    # trade_outcomes[date]['BULL'][tgt_sl_idx] = dict of results
    # trade_outcomes[date]['BEAR'][tgt_sl_idx] = dict of results
    trade_outcomes = {}

    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    print("\n-> Processing 1-minute Options Master Year-by-Year (2020 to 2026)...")

    for yr in years:
        t_yr0 = time.time()
        table = pq.read_table(opt_path, filters=[
            ('timestamp', '>=', pd.Timestamp(f"{yr}-01-01 00:00:00")),
            ('timestamp', '<=', pd.Timestamp(f"{yr}-12-31 23:59:59"))
        ], columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'close'])
        df_opt_yr = table.to_pandas()
        if df_opt_yr.empty:
            continue

        df_opt_yr['date'] = df_opt_yr['timestamp'].dt.date
        df_opt_yr['time'] = df_opt_yr['timestamp'].dt.time
        df_opt_yr['expiry_date'] = df_opt_yr['expiry'].dt.date

        days_grp = {d: grp for d, grp in df_opt_yr.groupby('date')}
        yr_dates = sorted(days_grp.keys())

        for dt in yr_dates:
            dt_ts = pd.Timestamp(dt)
            if dt_ts not in df_daily.index:
                continue

            need_bull = date_needs_bull.get(dt, False)
            need_bear = date_needs_bear.get(dt, False)
            if not need_bull and not need_bear:
                continue

            day_opt = days_grp[dt]
            entry_opt = day_opt[day_opt['time'] == pd.Timestamp('09:21:00').time()]
            if entry_opt.empty:
                continue

            # Smart Expiry Selection: nearest weekly strictly after today
            valid_expiries = sorted([exp for exp in entry_opt['expiry_date'].unique() if exp > dt])
            if not valid_expiries:
                continue
            selected_expiry = valid_expiries[0]
            dte_days = (selected_expiry - dt).days
            dte_years = (pd.Timestamp(f"{selected_expiry} 15:30:00") - pd.Timestamp(f"{dt} 09:21:00")).total_seconds() / (365.25 * 86400)

            # Spot price at 09:21:00
            spot_0921_ts = pd.Timestamp(f"{dt} 09:21:00")
            spot_val = df_spot.loc[spot_0921_ts, 'close'] if spot_0921_ts in df_spot.index else float(entry_opt['strike_price'].median())
            
            idx = df_daily.index.get_loc(dt_ts)
            sigma = float(df_daily.iloc[idx - 1]['rolling_vol'])
            lot_size = get_nifty_lot_size(dt)
            qty = 1 * lot_size

            trade_outcomes[dt] = {'BULL': {}, 'BEAR': {}, 'spot_entry': spot_val, 'expiry': selected_expiry, 'dte': dte_days, 'lot_size': lot_size}

            # --- Evaluate BULL PUT SPREAD (SELL 35Δ PE, BUY 15Δ PE) ---
            if need_bull:
                pe_chain = entry_opt[(entry_opt['expiry_date'] == selected_expiry) & 
                                     (entry_opt['option_type'] == 'PE') & 
                                     (entry_opt['close'] > 0)].copy()
                if not pe_chain.empty:
                    pe_chain['delta'] = calc_bs_deltas(spot_val, pe_chain['strike_price'].values, dte_years, 0.07, sigma, 'PE')
                    pe_35_row = pe_chain.loc[(pe_chain['delta'] - (-0.35)).abs().idxmin()]
                    pe_15_row = pe_chain.loc[(pe_chain['delta'] - (-0.15)).abs().idxmin()]

                    short_k = pe_35_row['strike_price']
                    long_k = pe_15_row['strike_price']
                    p_sell = float(pe_35_row['close'].iloc[0]) if isinstance(pe_35_row['close'], pd.Series) else float(pe_35_row['close'])
                    p_buy = float(pe_15_row['close'].iloc[0]) if isinstance(pe_15_row['close'], pd.Series) else float(pe_15_row['close'])
                    net_credit = float(p_sell - p_buy)

                    # Track 1-minute trajectory
                    leg_s = day_opt[(day_opt['expiry_date'] == selected_expiry) & (day_opt['option_type'] == 'PE') & (day_opt['strike_price'] == short_k)].drop_duplicates(subset=['time']).set_index('time')
                    leg_l = day_opt[(day_opt['expiry_date'] == selected_expiry) & (day_opt['option_type'] == 'PE') & (day_opt['strike_price'] == long_k)].drop_duplicates(subset=['time']).set_index('time')
                    common_t = sorted([t for t in leg_s.index if t in leg_l.index and pd.Timestamp('09:22:00').time() <= t <= pd.Timestamp('15:15:00').time()])

                    spread_vals = []
                    for t in common_t:
                        s_val = float(leg_s.loc[t, 'close'].iloc[0]) if isinstance(leg_s.loc[t, 'close'], pd.Series) else float(leg_s.loc[t, 'close'])
                        l_val = float(leg_l.loc[t, 'close'].iloc[0]) if isinstance(leg_l.loc[t, 'close'], pd.Series) else float(leg_l.loc[t, 'close'])
                        spread_vals.append((t, s_val, l_val))

                    # Evaluate each target/SL pair
                    for tgt_sl in target_sl_pairs:
                        tgt_pct, sl_pct = tgt_sl
                        tgt_pts = float(tgt_pct * net_credit)
                        sl_pts = float(-sl_pct * net_credit)

                        exit_t = pd.Timestamp('15:15:00').time()
                        exit_reason = 'TIME_EXIT (15:15)'
                        pnl_pts = 0.0
                        s_exit_p = float(p_sell)
                        l_exit_p = float(p_buy)

                        for t, s_p, l_p in spread_vals:
                            curr_spread = float(s_p - l_p)
                            curr_pnl = float(net_credit - curr_spread)
                            if curr_pnl >= tgt_pts:
                                exit_t = t
                                exit_reason = f"TARGET_HIT (+{int(tgt_pct*100)}%)"
                                pnl_pts = curr_pnl
                                s_exit_p = float(s_p)
                                l_exit_p = float(l_p)
                                break
                            if curr_pnl <= sl_pts:
                                exit_t = t
                                exit_reason = f"STOP_LOSS (-{int(sl_pct*100)}%)"
                                pnl_pts = curr_pnl
                                s_exit_p = float(s_p)
                                l_exit_p = float(l_p)
                                break
                            if t == pd.Timestamp('15:15:00').time():
                                exit_t = t
                                exit_reason = 'TIME_EXIT (15:15)'
                                pnl_pts = curr_pnl
                                s_exit_p = float(s_p)
                                l_exit_p = float(l_p)
                                break

                        turnover = ((p_sell + p_buy) + (s_exit_p + l_exit_p)) * qty
                        costs = 0.0015 * turnover
                        gross_pnl = pnl_pts * qty
                        net_pnl = gross_pnl - costs

                        trade_outcomes[dt]['BULL'][tgt_sl] = {
                            'short_leg': f"{short_k:.0f} PE", 'long_leg': f"{long_k:.0f} PE",
                            'short_d': pe_35_row['delta'], 'long_d': pe_15_row['delta'],
                            'credit': net_credit, 'exit_time': str(exit_t), 'exit_reason': exit_reason,
                            'pnl_pts': pnl_pts, 'gross_pnl': gross_pnl, 'costs': costs, 'net_pnl': net_pnl
                        }

            # --- Evaluate BEAR CALL SPREAD (SELL 35Δ CE, BUY 15Δ CE) ---
            if need_bear:
                ce_chain = entry_opt[(entry_opt['expiry_date'] == selected_expiry) & 
                                     (entry_opt['option_type'] == 'CE') & 
                                     (entry_opt['close'] > 0)].copy()
                if not ce_chain.empty:
                    ce_chain['delta'] = calc_bs_deltas(spot_val, ce_chain['strike_price'].values, dte_years, 0.07, sigma, 'CE')
                    ce_35_row = ce_chain.loc[(ce_chain['delta'] - 0.35).abs().idxmin()]
                    ce_15_row = ce_chain.loc[(ce_chain['delta'] - 0.15).abs().idxmin()]

                    short_k = ce_35_row['strike_price']
                    long_k = ce_15_row['strike_price']
                    p_sell = float(ce_35_row['close'].iloc[0]) if isinstance(ce_35_row['close'], pd.Series) else float(ce_35_row['close'])
                    p_buy = float(ce_15_row['close'].iloc[0]) if isinstance(ce_15_row['close'], pd.Series) else float(ce_15_row['close'])
                    net_credit = p_sell - p_buy

                    leg_s = day_opt[(day_opt['expiry_date'] == selected_expiry) & (day_opt['option_type'] == 'CE') & (day_opt['strike_price'] == short_k)].drop_duplicates(subset=['time']).set_index('time')
                    leg_l = day_opt[(day_opt['expiry_date'] == selected_expiry) & (day_opt['option_type'] == 'CE') & (day_opt['strike_price'] == long_k)].drop_duplicates(subset=['time']).set_index('time')
                    common_t = sorted([t for t in leg_s.index if t in leg_l.index and pd.Timestamp('09:22:00').time() <= t <= pd.Timestamp('15:15:00').time()])

                    spread_vals = []
                    for t in common_t:
                        s_val = float(leg_s.loc[t, 'close'].iloc[0]) if isinstance(leg_s.loc[t, 'close'], pd.Series) else float(leg_s.loc[t, 'close'])
                        l_val = float(leg_l.loc[t, 'close'].iloc[0]) if isinstance(leg_l.loc[t, 'close'], pd.Series) else float(leg_l.loc[t, 'close'])
                        spread_vals.append((t, s_val, l_val))

                    for tgt_sl in target_sl_pairs:
                        tgt_pct, sl_pct = tgt_sl
                        tgt_pts = float(tgt_pct * net_credit)
                        sl_pts = float(-sl_pct * net_credit)

                        exit_t = pd.Timestamp('15:15:00').time()
                        exit_reason = 'TIME_EXIT (15:15)'
                        pnl_pts = 0.0
                        s_exit_p = float(p_sell)
                        l_exit_p = float(p_buy)

                        for t, s_p, l_p in spread_vals:
                            curr_spread = float(s_p - l_p)
                            curr_pnl = float(net_credit - curr_spread)
                            if curr_pnl >= tgt_pts:
                                exit_t = t
                                exit_reason = f"TARGET_HIT (+{int(tgt_pct*100)}%)"
                                pnl_pts = curr_pnl
                                s_exit_p = float(s_p)
                                l_exit_p = float(l_p)
                                break
                            if curr_pnl <= sl_pts:
                                exit_t = t
                                exit_reason = f"STOP_LOSS (-{int(sl_pct*100)}%)"
                                pnl_pts = curr_pnl
                                s_exit_p = float(s_p)
                                l_exit_p = float(l_p)
                                break
                            if t == pd.Timestamp('15:15:00').time():
                                exit_t = t
                                exit_reason = 'TIME_EXIT (15:15)'
                                pnl_pts = curr_pnl
                                s_exit_p = float(s_p)
                                l_exit_p = float(l_p)
                                break
                            if curr_pnl <= sl_pts:
                                exit_t = t
                                exit_reason = f"STOP_LOSS (-{int(sl_pct*100)}%)"
                                pnl_pts = curr_pnl
                                s_exit_p = s_p
                                l_exit_p = l_p
                                break
                            if t == pd.Timestamp('15:15:00').time():
                                exit_t = t
                                exit_reason = 'TIME_EXIT (15:15)'
                                pnl_pts = curr_pnl
                                s_exit_p = s_p
                                l_exit_p = l_p
                                break

                        turnover = ((p_sell + p_buy) + (s_exit_p + l_exit_p)) * qty
                        costs = 0.0015 * turnover
                        gross_pnl = pnl_pts * qty
                        net_pnl = gross_pnl - costs

                        trade_outcomes[dt]['BEAR'][tgt_sl] = {
                            'short_leg': f"{short_k:.0f} CE", 'long_leg': f"{long_k:.0f} CE",
                            'short_d': ce_35_row['delta'], 'long_d': ce_15_row['delta'],
                            'credit': net_credit, 'exit_time': str(exit_t), 'exit_reason': exit_reason,
                            'pnl_pts': pnl_pts, 'gross_pnl': gross_pnl, 'costs': costs, 'net_pnl': net_pnl
                        }

        t_yr1 = time.time()
        print(f"   Processed Year {yr}: {len(yr_dates)} trading sessions in {t_yr1-t_yr0:.2f}s")

    print(f"\n-> Completed multi-year trade cache in {time.time()-t_start:.2f}s. Assembling all 40 combinations...")

    # 3. Simulate all 40 combinations
    comb_results = []
    daily_records_dict = {}

    initial_capital = 200000.0

    for p, m in st_settings:
        st_label = f"({p}, {m})"
        sig_series = signals_dict[(p, m)]

        for tgt_pct, sl_pct in target_sl_pairs:
            tgt_sl = (tgt_pct, sl_pct)
            comb_key = f"ST_{p}_{m}_T{int(tgt_pct*100)}_SL{int(sl_pct*100)}"

            running_cap = initial_capital
            daily_rows = []

            for d in df_daily.index:
                dt = d.date()
                if dt.year < 2020:
                    continue

                sig = sig_series.loc[d]
                action = 'SKIPPED'
                res = None

                if sig == 1 and dt in trade_outcomes and tgt_sl in trade_outcomes[dt]['BULL']:
                    action = 'BULL_PUT_SPREAD'
                    res = trade_outcomes[dt]['BULL'][tgt_sl]
                elif sig == -1 and dt in trade_outcomes and tgt_sl in trade_outcomes[dt]['BEAR']:
                    action = 'BEAR_CALL_SPREAD'
                    res = trade_outcomes[dt]['BEAR'][tgt_sl]

                if res is not None:
                    net_pnl = res['net_pnl']
                    gross_pnl = res['gross_pnl']
                    costs = res['costs']
                    running_cap += net_pnl

                    daily_rows.append({
                        'Date': str(dt), 'Year': dt.year, 'Day': dt.strftime('%A'),
                        'Signal': action, 'Short_Leg': res['short_leg'], 'Long_Leg': res['long_leg'],
                        'Net_Credit_Pts': round(res['credit'], 2), 'Exit_Time': res['exit_time'],
                        'Exit_Reason': res['exit_reason'], 'Points_PnL': round(res['pnl_pts'], 2),
                        'Gross_PnL_Rs': round(gross_pnl, 2), 'Costs_Rs': round(costs, 2),
                        'Net_PnL_Rs': round(net_pnl, 2), 'Running_Capital': round(running_cap, 2)
                    })
                else:
                    daily_rows.append({
                        'Date': str(dt), 'Year': dt.year, 'Day': dt.strftime('%A'),
                        'Signal': 'SKIPPED', 'Short_Leg': '-', 'Long_Leg': '-',
                        'Net_Credit_Pts': 0.0, 'Exit_Time': '-',
                        'Exit_Reason': 'SKIPPED (Mismatch)', 'Points_PnL': 0.0,
                        'Gross_PnL_Rs': 0.0, 'Costs_Rs': 0.0,
                        'Net_PnL_Rs': 0.0, 'Running_Capital': round(running_cap, 2)
                    })

            df_comb_daily = pd.DataFrame(daily_rows)
            daily_records_dict[comb_key] = df_comb_daily

            trades_only = df_comb_daily[df_comb_daily['Signal'] != 'SKIPPED']
            total_trades = len(trades_only)
            wins = trades_only[trades_only['Net_PnL_Rs'] > 0]
            losses = trades_only[trades_only['Net_PnL_Rs'] < 0]
            win_count = len(wins)
            loss_count = len(losses)
            win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0

            target_hits = (trades_only['Exit_Reason'].str.contains('TARGET_HIT')).sum()
            sl_hits = (trades_only['Exit_Reason'].str.contains('STOP_LOSS')).sum()
            time_exits = (trades_only['Exit_Reason'].str.contains('TIME_EXIT')).sum()

            gross_pnl_total = trades_only['Gross_PnL_Rs'].sum()
            costs_total = trades_only['Costs_Rs'].sum()
            net_pnl_total = trades_only['Net_PnL_Rs'].sum()

            sum_w = wins['Net_PnL_Rs'].sum()
            sum_l = abs(losses['Net_PnL_Rs'].sum())
            pf = (sum_w / sum_l) if sum_l > 0 else np.nan

            equity = trades_only['Running_Capital']
            peak = equity.cummax()
            dd_series = equity - peak
            max_dd_rs = abs(dd_series.min()) if not dd_series.empty else 0.0
            max_dd_pct = (max_dd_rs / peak.loc[dd_series.idxmin()]) * 100 if not dd_series.empty and max_dd_rs > 0 else 0.0

            ret_dd_ratio = (net_pnl_total / max_dd_rs) if max_dd_rs > 0 else np.nan

            comb_results.append({
                'Combination': comb_key,
                'Supertrend': st_label,
                'ST_Period': p,
                'ST_Multiplier': m,
                'Target_Pct': f"{int(tgt_pct*100)}%",
                'SL_Pct': f"{int(sl_pct*100)}%",
                'Total_Trades': total_trades,
                'Wins': win_count,
                'Losses': loss_count,
                'Win_Rate_Pct': round(win_rate, 2),
                'Target_Hits': target_hits,
                'SL_Hits': sl_hits,
                'Time_Exits': time_exits,
                'Gross_PnL_Rs': round(gross_pnl_total, 2),
                'Costs_Rs': round(costs_total, 2),
                'Net_PnL_Rs': round(net_pnl_total, 2),
                'Final_Capital_Rs': round(running_cap, 2),
                'Max_DD_Rs': round(max_dd_rs, 2),
                'Max_DD_Pct': round(max_dd_pct, 2),
                'Profit_Factor': round(pf, 2) if not np.isnan(pf) else 0.0,
                'Return_to_DD_Ratio': round(ret_dd_ratio, 2) if not np.isnan(ret_dd_ratio) else 0.0
            })

    # Sort all 40 by Return_to_DD_Ratio descending
    df_all40 = pd.DataFrame(comb_results)
    df_all40 = df_all40.sort_values(by='Return_to_DD_Ratio', ascending=False).reset_index(drop=True)
    df_all40['Rank'] = df_all40.index + 1
    cols = ['Rank', 'Combination', 'Supertrend', 'Target_Pct', 'SL_Pct', 'Total_Trades', 'Win_Rate_Pct',
            'Target_Hits', 'SL_Hits', 'Time_Exits', 'Gross_PnL_Rs', 'Costs_Rs', 'Net_PnL_Rs',
            'Final_Capital_Rs', 'Max_DD_Rs', 'Max_DD_Pct', 'Profit_Factor', 'Return_to_DD_Ratio']
    df_all40 = df_all40[cols]
    df_all40.to_csv(out_all40, index=False)
    print(f"\n-> All 40 Combinations saved to: {out_all40}")

    # Best Combination
    best_row = df_all40.iloc[0]
    best_key = best_row['Combination']
    best_daily = daily_records_dict[best_key]
    best_daily.to_csv(out_best_daily, index=False)
    print(f"-> Rank #1 Day-by-Day Ledger saved to: {out_best_daily}\n")

    # 4. Print TOP 10 LEADERBOARD
    print("=" * 145)
    print("                 TOP 10 SUPERTREND DIRECTIONAL CREDIT SPREAD COMBINATIONS (2020-2026)                 ")
    print("=" * 145)
    print(
        f"{'Rank':<5} {'Supertrend':<12} {'Target':<8} {'SL':<8} {'Trades':<7} {'Win Rate':<10} "
        f"{'Tgt Hit':<8} {'SL Hit':<8} {'TimeExit':<9} {'Net PnL (Rs)':<15} {'Max DD (Rs)':<14} {'DD %':<8} {'PF':<6} {'Ret/DD':<7}"
    )
    print("-" * 145)

    for i in range(min(10, len(df_all40))):
        r = df_all40.iloc[i]
        pnl_str = f"+Rs.{r['Net_PnL_Rs']:,.2f}" if r['Net_PnL_Rs'] > 0 else f"-Rs.{abs(r['Net_PnL_Rs']):,.2f}"
        dd_str = f"Rs.{r['Max_DD_Rs']:,.2f}"
        print(
            f"{int(r['Rank']):<5} {r['Supertrend']:<12} {r['Target_Pct']:<8} {r['SL_Pct']:<8} {int(r['Total_Trades']):<7} "
            f"{r['Win_Rate_Pct']:<9.1f}% {int(r['Target_Hits']):<8} {int(r['SL_Hits']):<8} {int(r['Time_Exits']):<9} "
            f"{pnl_str:<15} {dd_str:<14} {r['Max_DD_Pct']:<7.2f}% {r['Profit_Factor']:<6.2f} {r['Return_to_DD_Ratio']:<7.2f}"
        )
    print("=" * 145)

    # 5. Print Year-by-Year Breakdown for Rank #1
    best_trades = best_daily[best_daily['Signal'] != 'SKIPPED']
    print(f"\n==========================================================================================")
    print(f"   RANK #1 STRATEGY: Supertrend {best_row['Supertrend']} | Target {best_row['Target_Pct']} | Stop-Loss {best_row['SL_Pct']}")
    print(f"==========================================================================================")
    print(
        f"{'Year':<6} {'Trades':<8} {'Wins':<6} {'Losses':<8} {'Win Rate':<10} "
        f"{'Gross PnL (Rs)':<16} {'Costs (Rs)':<12} {'Net PnL (Rs)':<16} {'Ending Capital (Rs)':<18}"
    )
    print("-" * 92)

    running_cap_tracker = initial_capital
    for yr, grp in best_trades.groupby('Year'):
        yr_trades = len(grp)
        yr_wins = (grp['Net_PnL_Rs'] > 0).sum()
        yr_losses = (grp['Net_PnL_Rs'] < 0).sum()
        yr_wr = (yr_wins / yr_trades * 100) if yr_trades > 0 else 0.0
        yr_gross = grp['Gross_PnL_Rs'].sum()
        yr_costs = grp['Costs_Rs'].sum()
        yr_net = grp['Net_PnL_Rs'].sum()
        running_cap_tracker += yr_net

        gross_str = f"+Rs.{yr_gross:,.2f}" if yr_gross > 0 else f"-Rs.{abs(yr_gross):,.2f}"
        net_str = f"+Rs.{yr_net:,.2f}" if yr_net > 0 else f"-Rs.{abs(yr_net):,.2f}"

        print(
            f"{yr:<6} {yr_trades:<8} {yr_wins:<6} {yr_losses:<8} {yr_wr:<9.1f}% "
            f"{gross_str:<16} Rs.{yr_costs:<10,.2f} {net_str:<16} Rs.{running_cap_tracker:,.2f}"
        )
    print("-" * 92)
    tot_trades = len(best_trades)
    tot_wins = (best_trades['Net_PnL_Rs'] > 0).sum()
    tot_losses = (best_trades['Net_PnL_Rs'] < 0).sum()
    tot_wr = (tot_wins / tot_trades * 100) if tot_trades > 0 else 0.0
    tot_gross = best_trades['Gross_PnL_Rs'].sum()
    tot_costs = best_trades['Costs_Rs'].sum()
    tot_net = best_trades['Net_PnL_Rs'].sum()

    print(
        f"{'TOTAL':<6} {tot_trades:<8} {tot_wins:<6} {tot_losses:<8} {tot_wr:<9.1f}% "
        f"{'+Rs.'+f'{tot_gross:,.2f}':<16} Rs.{tot_costs:<10,.2f} {'+Rs.'+f'{tot_net:,.2f}':<16} Rs.{running_cap_tracker:,.2f}"
    )
    print("=" * 92)
    print(f"\nOptimization completed in {time.time()-t_start:.1f} seconds.\n")


if __name__ == "__main__":
    run_6yr_optimization()
