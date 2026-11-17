"""
scripts/generate_deep_parameter_audit.py
Exhaustive Multi-Parameter Quantitative Audit of ALL Credit Spread Strategies on Real Parquet Vault.
Calculates 30+ Institutional Metrics for Every Strategy:
- Profitability (Net PnL, Gross PnL, Costs, ROI, Avg/Trade, Avg/Month)
- Risk-Adjusted Ratios (Sharpe, Sortino, Calmar, Profit Factor, Max DD %, Max DD Rs)
- Trade Distribution (Win Rate, Payoff Ratio, Avg Win, Avg Loss, Max Consec Wins/Losses)
- Exit Distribution (Target %, Stop Loss %, Breakeven %, EOD %)
- Monthly Consistency (Green Months %, Months >= 5k %, Months >= 10k %, Best Month, Worst Month)
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

def calculate_max_streaks(series: pd.Series):
    max_wins = 0
    max_losses = 0
    curr_wins = 0
    curr_losses = 0
    for val in series:
        if val > 0:
            curr_wins += 1
            curr_losses = 0
            if curr_wins > max_wins:
                max_wins = curr_wins
        else:
            curr_losses += 1
            curr_wins = 0
            if curr_losses > max_losses:
                max_losses = curr_losses
    return max_wins, max_losses

def run_deep_audit():
    print("=" * 130)
    print("            EXHAUSTIVE MULTI-PARAMETER QUANTITATIVE AUDIT ACROSS ALL STRATEGIES (REAL PARQUET VAULT)            ")
    print("=" * 130)

    # 1. Spot Data Pre-processing
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

    # Typical price mean
    df_5m['tp'] = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
    df_5m['vwap'] = df_5m.groupby('date')['tp'].transform(lambda s: s.expanding().mean())

    # SuperTrends
    st1 = compute_supertrend(df_5m, period=7, multiplier=2.0)
    st2 = compute_supertrend(df_5m, period=10, multiplier=3.0)
    st3 = compute_supertrend(df_5m, period=14, multiplier=4.0)
    df_5m['st_bull'] = (st1 == 1) & (st2 == 1) & (st3 == 1)
    df_5m['st_bear'] = (st1 == -1) & (st2 == -1) & (st3 == -1)

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

    # Core multi-strategy simulation engine
    def simulate_deep(strat_type, short_delta, long_delta, days_filter, wed_strict=False):
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

                tgt_val = net_credit * 0.60 # 40% Target Decay
                current_sl_val = net_credit * 1.60 # 60% SL
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

                    # Breakeven @ +25%
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

                # Calculate holding duration
                dur_mins = int((pd.to_datetime(exit_time) - pd.to_datetime(entry_time)).total_seconds() / 60)

                trades.append({
                    'date': dt,
                    'year_month': f"{dt.year}-{dt.month:02d}",
                    'net_pnl': net_pnl,
                    'gross_pnl': gross_pnl,
                    'slippage': slip,
                    'exit_reason': exit_reason,
                    'duration_mins': dur_mins,
                    'equity': equity
                })
                daily_trades += 1
                in_pos = True
                pos_exit = exit_time

        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return None

        # Comprehensive Parameter Computations
        total_trades = len(tdf)
        wins = tdf[tdf['net_pnl'] > 0]
        losses = tdf[tdf['net_pnl'] <= 0]
        num_wins = len(wins)
        num_losses = len(losses)
        win_rate = (num_wins / total_trades) * 100.0

        total_net_pnl = float(tdf['net_pnl'].sum())
        total_gross_pnl = float(tdf['gross_pnl'].sum())
        total_costs = float(tdf['slippage'].sum())
        roi_pct = (total_net_pnl / 200000.0) * 100.0

        avg_trade_pnl = total_net_pnl / total_trades
        avg_win_pnl = float(wins['net_pnl'].mean()) if num_wins > 0 else 0.0
        avg_loss_pnl = float(abs(losses['net_pnl'].mean())) if num_losses > 0 else 1.0
        payoff_ratio = avg_win_pnl / avg_loss_pnl if avg_loss_pnl > 0 else 99.0

        max_single_win = float(tdf['net_pnl'].max())
        max_single_loss = float(tdf['net_pnl'].min())

        gross_wins_sum = float(wins['net_pnl'].sum()) if num_wins > 0 else 0.0
        gross_losses_sum = float(abs(losses['net_pnl'].sum())) if num_losses > 0 else 1.0
        profit_factor = gross_wins_sum / gross_losses_sum if gross_losses_sum > 0 else 99.0

        # Drawdowns
        tdf['cum_pnl'] = tdf['net_pnl'].cumsum()
        tdf['eq_curve'] = 200000.0 + tdf['cum_pnl']
        peak = tdf['eq_curve'].cummax()
        dd_pts = peak - tdf['eq_curve']
        max_dd_rs = float(dd_pts.max())
        max_dd_pct = float((dd_pts / peak).max() * 100.0)

        # Monthly Metrics
        monthly = tdf.groupby('year_month')['net_pnl'].sum()
        total_months = len(monthly)
        green_months = int((monthly > 0).sum())
        green_month_pct = (green_months / total_months) * 100.0
        months_ge_5k = int((monthly >= 5000.0).sum())
        months_ge_10k = int((monthly >= 10000.0).sum())
        avg_monthly_pnl = float(monthly.mean())
        best_month_pnl = float(monthly.max())
        worst_month_pnl = float(monthly.min())
        monthly_std = float(monthly.std())

        # Annualized Ratios (Daily PnL aggregation)
        daily_pnl = tdf.groupby('date')['net_pnl'].sum()
        daily_mean = daily_pnl.mean()
        daily_std = daily_pnl.std()
        sharpe = (daily_mean / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0

        downside = daily_pnl[daily_pnl < 0]
        downside_std = downside.std()
        sortino = (daily_mean / downside_std) * np.sqrt(252) if (len(downside) > 0 and downside_std > 0) else 0.0

        calmar = ((total_net_pnl / 200000.0) / (total_months / 12.0)) / (max_dd_pct / 100.0) if max_dd_pct > 0 else 99.0

        # Streaks
        max_consec_wins, max_consec_losses = calculate_max_streaks(tdf['net_pnl'])

        # Exit Distributions
        reasons = tdf['exit_reason'].value_counts()
        target_pct = (reasons.get('TARGET', 0) / total_trades) * 100.0
        sl_pct = (reasons.get('STOP_LOSS', 0) / total_trades) * 100.0
        be_pct = (reasons.get('BREAKEVEN_SL', 0) / total_trades) * 100.0
        time_pct = ((reasons.get('TIME_EXIT', 0) + reasons.get('EOD_CLOSE', 0)) / total_trades) * 100.0
        avg_duration = float(tdf['duration_mins'].mean())

        return {
            'total_trades': total_trades,
            'win_rate': round(win_rate, 2),
            'total_net_pnl': round(total_net_pnl, 2),
            'total_gross_pnl': round(total_gross_pnl, 2),
            'total_costs': round(total_costs, 2),
            'roi_pct': round(roi_pct, 2),
            'profit_factor': round(profit_factor, 2),
            'sharpe_ratio': round(sharpe, 2),
            'sortino_ratio': round(sortino, 2),
            'calmar_ratio': round(calmar, 2),
            'max_dd_pct': round(max_dd_pct, 2),
            'max_dd_rs': round(max_dd_rs, 2),
            'avg_monthly_pnl': round(avg_monthly_pnl, 2),
            'green_months_pct': round(green_month_pct, 1),
            'green_months_ratio': f"{green_months}/{total_months}",
            'months_ge_5k_ratio': f"{months_ge_5k}/{total_months} ({months_ge_5k/total_months*100:.1f}%)",
            'months_ge_10k_ratio': f"{months_ge_10k}/{total_months} ({months_ge_10k/total_months*100:.1f}%)",
            'best_month': round(best_month_pnl, 2),
            'worst_month': round(worst_month_pnl, 2),
            'monthly_std': round(monthly_std, 2),
            'avg_trade_pnl': round(avg_trade_pnl, 2),
            'avg_win_pnl': round(avg_win_pnl, 2),
            'avg_loss_pnl': round(avg_loss_pnl, 2),
            'payoff_ratio': round(payoff_ratio, 2),
            'max_single_win': round(max_single_win, 2),
            'max_single_loss': round(max_single_loss, 2),
            'max_consec_wins': max_consec_wins,
            'max_consec_losses': max_consec_losses,
            'target_exit_pct': round(target_pct, 1),
            'sl_exit_pct': round(sl_pct, 1),
            'be_exit_pct': round(be_pct, 1),
            'time_exit_pct': round(time_pct, 1),
            'avg_duration_mins': round(avg_duration, 1)
        }

    # Tournament Lineup
    lineup = [
        ("Strat 1: 4-Day ATM Engine (D50_L10 Strict Wed)", 'EMA_PULLBACK', 0.50, 0.10, ['Tuesday', 'Wednesday', 'Thursday', 'Friday'], True),
        ("Strat 2: Pure ATM Asymmetric (D50_L10 Tue-Thu-Fri)", 'EMA_PULLBACK', 0.50, 0.10, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat 3: Pure ATM Standard Wing (D50_L15 Tue-Thu-Fri)", 'EMA_PULLBACK', 0.50, 0.15, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat 4: Deep Protection Wing (D45_L10 Tue-Thu-Fri)", 'EMA_PULLBACK', 0.45, 0.10, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat 5: Benchmark Baseline (D45_L15 Tue-Thu-Fri)", 'EMA_PULLBACK', 0.45, 0.15, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat 6: Triple Confluence Master (SuperTrend+EMA)", 'TRIPLE_CONFLUENCE', 0.45, 0.15, ['Tuesday', 'Thursday', 'Friday'], False),
        ("Strat 7: 3-Tier SuperTrend + VWAP (Mon-Fri All Days)", 'SUPERTREND_VWAP', 0.45, 0.15, ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'], False),
    ]

    all_metrics = []
    for name, stype, sd, ld, days, w_strict in lineup:
        print(f"Auditing {name} across 30+ parameters...")
        res = simulate_deep(stype, sd, ld, days, w_strict)
        if res:
            res['strategy_name'] = name
            all_metrics.append(res)

    df_out = pd.DataFrame(all_metrics)
    # Reorder strategy_name to first column
    cols = ['strategy_name'] + [c for c in df_out.columns if c != 'strategy_name']
    df_out = df_out[cols]

    out_csv = Path("results/deep_parameter_comparison_all_strategies.csv")
    df_out.to_csv(out_csv, index=False)
    print(f"\nSuccessfully generated deep multi-parameter audit CSV at {out_csv}")

if __name__ == '__main__':
    run_deep_audit()
