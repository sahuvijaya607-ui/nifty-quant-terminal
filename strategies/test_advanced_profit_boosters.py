"""
strategies/test_advanced_profit_boosters.py
Deep Alpha Research: Testing Exact Profit Maximization Levers on Real Parquet Vault.
1. Target Decay Grid (30%, 40%, 50%, 60%, 70%).
2. Correct Trailing Profit Engine (Lock at 35%, trail to 75%).
3. Compounding Scaling Comparison (1 Lot per 100k, 75k, 60k).
4. Full Month-by-Month Alpha Breakdown.
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

def run_advanced_research():
    print("=" * 120)
    print("     ADVANCED PROFIT MAXIMIZATION RESEARCH: EXACT MATHEMATICAL OPTIMIZATION ON REAL DATA     ")
    print("=" * 120)

    # 1. Spot Data
    df_spot = pd.read_parquet("data/nifty_spot_master_2020_2026_1min.parquet")
    spot_price_map = dict(zip(df_spot.index, df_spot['close']))

    df_daily = df_spot.resample('1D').agg({'close': 'last'}).dropna()
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

    def simulate(target_pct=0.40, sl_pct=0.60, trail_mode=False, compound_threshold=None):
        trades = []
        equity = 200000.0

        for dt in all_dates:
            day_name = pd.to_datetime(dt).day_name()
            if day_name not in ['Tuesday', 'Thursday', 'Friday']:
                continue
            if dt not in daily_cache:
                continue
            day = daily_cache[dt]
            trajs = day['trajs']
            pe_strikes = day['pe_strikes']
            ce_strikes = day['ce_strikes']
            target_exp = day['exp']

            base_lot = get_nifty_lot_size(dt)
            if compound_threshold is not None:
                num_lots = max(1, int(equity // compound_threshold))
                lot_size = base_lot * num_lots
            else:
                num_lots = 1
                lot_size = base_lot

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

                current_sl_val = net_credit * (1.0 + sl_pct)
                current_tgt_val = net_credit * (1.0 - target_pct)
                be_active = False
                trail_active = False

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

                    # Breakeven trigger: When decay reaches 25%
                    if not be_active and curr_spread <= (net_credit * 0.75):
                        be_active = True
                        current_sl_val = net_credit

                    # Dynamic Trailing Mode:
                    # When decay reaches 35%, lock in 20% profit, and extend target to 70% decay!
                    if trail_mode and not trail_active and curr_spread <= (net_credit * 0.65):
                        trail_active = True
                        current_sl_val = net_credit * 0.80 # Lock 20% profit
                        current_tgt_val = net_credit * 0.30 # Aim for 70% decay

                    if curr_spread <= current_tgt_val:
                        exit_time = ct
                        exit_s_p = curr_s
                        exit_l_p = curr_l
                        exit_reason = "EXTENDED_TARGET" if trail_active else "TARGET"
                        break

                    if curr_spread >= current_sl_val:
                        exit_time = ct
                        exit_s_p = curr_s
                        exit_l_p = curr_l
                        exit_reason = "TRAILED_SL" if trail_active else ("BREAKEVEN_SL" if be_active else "STOP_LOSS")
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
                    'lot_size': lot_size,
                    'num_lots': num_lots,
                    'exit_reason': exit_reason,
                    'equity': equity
                })

                daily_trades += 1
                in_pos = True
                pos_exit = exit_time

        tdf = pd.DataFrame(trades)
        monthly = tdf.groupby('year_month')['net_pnl'].sum()
        total_months = len(monthly)
        pos_months = (monthly > 0).sum()
        target_5k = (monthly >= 5000.0).sum()
        target_10k = (monthly >= 10000.0).sum()
        target_15k = (monthly >= 15000.0).sum()

        tdf['cum_pnl'] = tdf['net_pnl'].cumsum()
        tdf['eq_curve'] = 200000.0 + tdf['cum_pnl']
        peak = tdf['eq_curve'].cummax()
        dd_pts = peak - tdf['eq_curve']
        dd_pct = (dd_pts / peak).max() * 100.0

        total_net = tdf['net_pnl'].sum()
        wins = tdf[tdf['net_pnl'] > 0]
        losses = tdf[tdf['net_pnl'] <= 0]
        win_rate = (len(wins) / len(tdf)) * 100.0
        gross_win = wins['net_pnl'].sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses['net_pnl'].sum()) if len(losses) > 0 else 1.0
        pf = gross_win / gross_loss if gross_loss > 0 else 99.0

        return {
            'trades_df': tdf,
            'monthly_df': monthly,
            'metrics': {
                'Total_Trades': len(tdf),
                'Win_Rate_%': win_rate,
                'Total_Net_PnL': total_net,
                'Ending_Capital': 200000.0 + total_net,
                'ROI_%': (total_net / 200000.0) * 100.0,
                'Profit_Factor': pf,
                'Max_DD_%': dd_pct,
                'Positive_Months': f"{pos_months}/{total_months} ({pos_months/total_months*100:.1f}%)",
                'Months_>=5k': f"{target_5k}/{total_months} ({target_5k/total_months*100:.1f}%)",
                'Months_>=10k': f"{target_10k}/{total_months} ({target_10k/total_months*100:.1f}%)",
                'Months_>=15k': f"{target_15k}/{total_months} ({target_15k/total_months*100:.1f}%)",
                'Average_Monthly_PnL': monthly.mean(),
                'Best_Month': monthly.max(),
                'Worst_Month': monthly.min()
            }
        }

    print("\n--- TEST 1: TARGET DECAY PERCENTAGE OPTIMIZATION (1 Lot Fixed) ---")
    for tgt in [0.30, 0.40, 0.50, 0.60, 0.70]:
        res = simulate(target_pct=tgt, sl_pct=0.60, trail_mode=False, compound_threshold=None)
        m = res['metrics']
        print(f"Target {int(tgt*100)}% Decay: Net PnL = Rs. {m['Total_Net_PnL']:+,.2f} | WinRate = {m['Win_Rate_%']:.1f}% | Avg/Mo = Rs. {m['Average_Monthly_PnL']:+,.2f} | Max DD = {m['Max_DD_%']:.2f}% | >=5k: {m['Months_>=5k']}")

    print("\n--- TEST 2: STOP LOSS PERCENTAGE OPTIMIZATION (Target 40%, 1 Lot Fixed) ---")
    for sl in [0.40, 0.50, 0.60, 0.80, 1.00]:
        res = simulate(target_pct=0.40, sl_pct=sl, trail_mode=False, compound_threshold=None)
        m = res['metrics']
        print(f"SL {int(sl*100)}%: Net PnL = Rs. {m['Total_Net_PnL']:+,.2f} | WinRate = {m['Win_Rate_%']:.1f}% | Avg/Mo = Rs. {m['Average_Monthly_PnL']:+,.2f} | Max DD = {m['Max_DD_%']:.2f}% | >=5k: {m['Months_>=5k']}")

    print("\n--- TEST 3: DYNAMIC PROFIT TRAILING ENGINE (Lock 20% at 35% decay, aim for 70%) ---")
    res_trail = simulate(target_pct=0.40, sl_pct=0.60, trail_mode=True, compound_threshold=None)
    m = res_trail['metrics']
    print(f"Trailing Engine: Net PnL = Rs. {m['Total_Net_PnL']:+,.2f} | WinRate = {m['Win_Rate_%']:.1f}% | Avg/Mo = Rs. {m['Average_Monthly_PnL']:+,.2f} | Max DD = {m['Max_DD_%']:.2f}% | >=5k: {m['Months_>=5k']}")

    print("\n--- TEST 4: DYNAMIC CAPITAL COMPOUNDING ON BASELINE CHAMPION (1 Lot per X Equity) ---")
    comp_results = []
    for thresh in [100000, 75000, 60000, 50000]:
        res = simulate(target_pct=0.40, sl_pct=0.60, trail_mode=False, compound_threshold=thresh)
        m = res['metrics']
        comp_results.append({
            'Threshold': f"1 Lot per Rs. {thresh//1000}k",
            **m
        })
        print(f"Compounding (1 Lot / Rs. {thresh//1000}k): Net PnL = Rs. {m['Total_Net_PnL']:+,.2f} | Ending Cap = Rs. {m['Ending_Capital']:,.2f} | Avg/Mo = Rs. {m['Average_Monthly_PnL']:+,.2f} | Max DD = {m['Max_DD_%']:.2f}% | >=10k: {m['Months_>=10k']} | >=15k: {m['Months_>=15k']}")

    # Save detailed monthly breakdown for 1 Lot per 75k Compounding
    res_best = simulate(target_pct=0.40, sl_pct=0.60, trail_mode=False, compound_threshold=75000)
    monthly_best = res_best['monthly_df']
    monthly_best.to_csv("results/compounding_75k_monthly_breakdown.csv", header=['net_pnl'])
    res_best['trades_df'].to_csv("results/compounding_75k_trades.csv", index=False)
    print("\nSaved detailed compounding monthly ledger to results/compounding_75k_monthly_breakdown.csv")

if __name__ == '__main__':
    run_advanced_research()
