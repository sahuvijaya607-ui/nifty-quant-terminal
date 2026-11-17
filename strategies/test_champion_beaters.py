"""
strategies/test_champion_beaters.py
Testing 5 Advanced Candidate Strategies designed to compete with and beat the Benchmark Champion
on 1 LOT real parquet data.

Candidates:
1. BENCHMARK: PCR + 9/20/50 EMA Pullback (D45_L15, Tue-Thu-Fri) -> Baseline: Rs. 1,35,115
2. CANDIDATE 1: 3-Tier SuperTrend + Tue-Thu-Fri Day Filter (D45_L15)
3. CANDIDATE 2: Triple Confluence Master (SuperTrend + 50 EMA + PCR >= 1.0, Tue-Thu-Fri)
4. CANDIDATE 3: Institutional 09:45 AM Post-Open Momentum Spread (D45_L15)
5. CANDIDATE 4: VWAP Pullback Rejection + 20 EMA Bounce (D45_L15, Tue-Thu-Fri)
6. CANDIDATE 5: Ensemble Hybrid (Trade either EMA Pullback OR SuperTrend, whichever triggers first)
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

def run_beaters_audit():
    print("=" * 125)
    print("     QUANTITATIVE ALPHA RESEARCH: CAN ANY STRATEGY BEAT OUR CHAMPION ON 1 LOT REAL DATA?     ")
    print("=" * 125)

    # 1. Spot Data
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

    # Indicators
    df_5m['ema9'] = df_5m['close'].ewm(span=9, adjust=False).mean()
    df_5m['ema20'] = df_5m['close'].ewm(span=20, adjust=False).mean()
    df_5m['ema50'] = df_5m['close'].ewm(span=50, adjust=False).mean()
    df_5m['is_green'] = df_5m['close'] >= df_5m['open']
    df_5m['is_red'] = df_5m['close'] <= df_5m['open']

    # Typical Price Anchor
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

    def simulate(candidate_id: str, name: str):
        trades = []
        equity = 200000.0

        for dt in all_dates:
            day_name = pd.to_datetime(dt).day_name()
            if dt not in daily_cache:
                continue
            day = daily_cache[dt]
            trajs = day['trajs']
            pe_strikes = day['pe_strikes']
            ce_strikes = day['ce_strikes']
            target_exp = day['exp']

            # Day filtering: All top contenders use the proven Tue-Thu-Fri engine
            if candidate_id != 'ALL_DAYS_ST':
                if day_name not in ['Tuesday', 'Thursday', 'Friday']:
                    continue

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

                # Signal Selection
                if candidate_id == 'CHAMPION':
                    c_bull = (bar['close'] > bar['ema50']) & (bar['ema9'] >= bar['ema20']) & (bar['close'] > bar['ema9']) & bar['is_green']
                    c_bear = (bar['close'] < bar['ema50']) & (bar['ema9'] <= bar['ema20']) & (bar['close'] < bar['ema9']) & bar['is_red']
                    if c_bull and curr_pcr >= 1.0:
                        is_bull = True
                    elif c_bear and curr_pcr <= 1.0:
                        is_bear = True

                elif candidate_id == 'CAND_1_SUPERTREND_TTF':
                    # 3-Tier SuperTrend on Tue-Thu-Fri
                    st_bull = bar['st_bull'] and (bar['close'] > bar['vwap']) and bar['is_green']
                    st_bear = bar['st_bear'] and (bar['close'] < bar['vwap']) and bar['is_red']
                    if st_bull and curr_pcr >= 0.95:
                        is_bull = True
                    elif st_bear and curr_pcr <= 1.05:
                        is_bear = True

                elif candidate_id == 'CAND_2_TRIPLE_CONFLUENCE':
                    # SuperTrend + 50 EMA + PCR
                    c_bull = bar['st_bull'] and (bar['close'] > bar['ema50']) and (bar['ema9'] >= bar['ema20']) and bar['is_green']
                    c_bear = bar['st_bear'] and (bar['close'] < bar['ema50']) and (bar['ema9'] <= bar['ema20']) and bar['is_red']
                    if c_bull and curr_pcr >= 1.0:
                        is_bull = True
                    elif c_bear and curr_pcr <= 1.0:
                        is_bear = True

                elif candidate_id == 'CAND_3_POST_945_MOMENTUM':
                    # Wait for 09:45 AM opening volatility to clear, then take strong trend
                    if bar['close_time_t'] >= pd.Timestamp('09:45:00').time():
                        c_bull = (bar['close'] > bar['ema50']) & (bar['close'] > bar['vwap']) & (bar['ema9'] > bar['ema20']) & bar['is_green']
                        c_bear = (bar['close'] < bar['ema50']) & (bar['close'] < bar['vwap']) & (bar['ema9'] < bar['ema20']) & bar['is_red']
                        if c_bull and curr_pcr >= 1.0:
                            is_bull = True
                        elif c_bear and curr_pcr <= 1.0:
                            is_bear = True

                elif candidate_id == 'CAND_4_VWAP_EMA20_BOUNCE':
                    # Pullback to 20 EMA while above VWAP
                    dist_ema20 = abs(bar['close'] - bar['ema20']) / bar['close']
                    if dist_ema20 < 0.002: # Bounce off 20 EMA
                        if bar['close'] > bar['vwap'] and bar['is_green'] and curr_pcr >= 1.0:
                            is_bull = True
                        elif bar['close'] < bar['vwap'] and bar['is_red'] and curr_pcr <= 1.0:
                            is_bear = True

                elif candidate_id == 'CAND_5_HYBRID_ENSEMBLE':
                    # Either EMA Pullback OR SuperTrend triggers entry
                    ema_bull = (bar['close'] > bar['ema50']) & (bar['ema9'] >= bar['ema20']) & (bar['close'] > bar['ema9']) & bar['is_green'] & (curr_pcr >= 1.0)
                    ema_bear = (bar['close'] < bar['ema50']) & (bar['ema9'] <= bar['ema20']) & (bar['close'] < bar['ema9']) & bar['is_red'] & (curr_pcr <= 1.0)
                    st_bull = bar['st_bull'] and (bar['close'] > bar['vwap']) and bar['is_green'] and (curr_pcr >= 0.95)
                    st_bear = bar['st_bear'] and (bar['close'] < bar['vwap']) and bar['is_red'] and (curr_pcr <= 1.05)
                    if ema_bull or st_bull:
                        is_bull = True
                    elif ema_bear or st_bear:
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

                    # Breakeven trigger: +25% decay
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
                    'net_pnl': net_pnl,
                    'gross_pnl': gross_pnl,
                    'slippage': slip,
                    'exit_reason': exit_reason
                })
                daily_trades += 1
                in_pos = True
                pos_exit = exit_time

        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return None

        monthly = tdf.groupby('year_month')['net_pnl'].sum()
        total_months = len(monthly)
        pos_months = (monthly > 0).sum()
        target_5k = (monthly >= 5000.0).sum()
        target_10k = (monthly >= 10000.0).sum()

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
            'Candidate_ID': candidate_id,
            'Strategy_Name': name,
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
            'Average_Monthly_PnL': monthly.mean(),
            'Best_Month': monthly.max(),
            'Worst_Month': monthly.min()
        }

    contenders = [
        ('CHAMPION',                 '1. Champion Benchmark: PCR + 9/20/50 EMA Pullback'),
        ('CAND_1_SUPERTREND_TTF',    '2. Contender 1: 3-Tier SuperTrend + TTF (D45_L15)'),
        ('CAND_2_TRIPLE_CONFLUENCE', '3. Contender 2: Triple Confluence (SuperTrend + 50 EMA + PCR)'),
        ('CAND_3_POST_945_MOMENTUM', '4. Contender 3: Post-09:45 AM Institutional Momentum Spread'),
        ('CAND_4_VWAP_EMA20_BOUNCE', '5. Contender 4: VWAP Pullback Rejection + 20 EMA Bounce'),
        ('CAND_5_HYBRID_ENSEMBLE',   '6. Contender 5: Hybrid Ensemble (EMA Pullback + SuperTrend)')
    ]

    print("3. Executing Advanced Beaters Tournament...")
    results = []
    for c_id, c_name in contenders:
        res = simulate(c_id, c_name)
        if res:
            results.append(res)
            print(f"   -> {c_name}: Net PnL = Rs. {res['Total_Net_PnL']:+,.2f} | WR = {res['Win_Rate_%']:.1f}% | Avg/Mo = Rs. {res['Average_Monthly_PnL']:+,.2f} | Max DD = {res['Max_DD_%']:.2f}% | >=5k: {res['Months_>=5k']}")

    res_df = pd.DataFrame(results)
    out_csv = Path("results/champion_beaters_tournament_results.csv")
    res_df.to_csv(out_csv, index=False)
    print(f"\n4. Saved Results to {out_csv}")

    print("\n" + "=" * 140)
    print("                    ADVANCED STRATEGY TOURNAMENT LEADERBOARD (1 LOT FIXED)                    ")
    print("=" * 140)
    cols = ['Strategy_Name', 'Total_Trades', 'Win_Rate_%', 'Total_Net_PnL', 'Profit_Factor', 'Max_DD_%', 'Months_>=5k', 'Average_Monthly_PnL']
    print(res_df[cols].to_string(index=False))
    print("=" * 140 + "\n")

if __name__ == '__main__':
    run_beaters_audit()
