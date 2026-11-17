"""
strategies/test_profit_boosters.py
Quantitative Research: Testing 6 Proven Alpha Levers to Maximize Profit on Real NIFTY Data.

Levers Evaluated:
1. Baseline: D45_L15 Tue-Thu-Fri (1 Lot fixed, 40T/60SL).
2. Lever A (Higher Timeframe 15m Confluence): 15m Trend + 5m Trigger + PCR.
3. Lever B (Minimum Credit Floor >= 90 pts): Skip low-reward days.
4. Lever C (Dynamic Extended Trailing): Lock Breakeven at +25%, Take 50% Target, Trail to 80%.
5. Lever D (Dual Engine - Directional + Sideways Days): Trade Iron Condor when PCR is neutral (0.85-1.15).
6. Lever E (Dynamic Capital Compounding): Reinvest profits (1 lot per Rs. 75,000 equity).
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

def run_booster_research():
    t_start = time.time()
    print("=" * 120)
    print("      QUANTITATIVE PROFIT MAXIMIZATION AUDIT: TESTING 6 HIGH-ALPHA LEVERS ON REAL DATA      ")
    print("=" * 120)
    print("Objective: Discover mathematical optimizations to boost monthly returns from Rs. 7k to Rs. 15k+")
    print("Underlying: NIFTY 50 Options (Real Parquet Vault)")
    print("=" * 120 + "\n")

    # 1. Load Spot Data and compute 5m & 15m EMAs
    print("1. Loading Spot Data and computing Multi-Timeframe EMAs (5m & 15m)...")
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
    
    # 15m Bars for HTF Confluence
    df_15m = df_spot.resample('15min', origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_15m['ema50_15m'] = df_15m['close'].ewm(span=50, adjust=False).mean()
    df_15m['ema20_15m'] = df_15m['close'].ewm(span=20, adjust=False).mean()
    df_15m['htf_bull'] = (df_15m['close'] > df_15m['ema50_15m']) & (df_15m['ema20_15m'] > df_15m['ema50_15m'])
    df_15m['htf_bear'] = (df_15m['close'] < df_15m['ema50_15m']) & (df_15m['ema20_15m'] < df_15m['ema50_15m'])
    df_15m['close_time'] = df_15m.index + pd.Timedelta(minutes=15)
    
    # Map HTF trend to 5m bars
    htf_bull_map = dict(zip(df_15m['close_time'], df_15m['htf_bull']))
    htf_bear_map = dict(zip(df_15m['close_time'], df_15m['htf_bear']))

    # Align HTF to 5m
    df_5m['htf_bull'] = df_5m['close_time'].map(htf_bull_map).ffill().fillna(False)
    df_5m['htf_bear'] = df_5m['close_time'].map(htf_bear_map).ffill().fillna(False)

    df_5m['is_bull'] = (df_5m['close'] > df_5m['ema50']) & (df_5m['ema9'] >= df_5m['ema20']) & (df_5m['close'] > df_5m['ema9']) & df_5m['is_green']
    df_5m['is_bear'] = (df_5m['close'] < df_5m['ema50']) & (df_5m['ema9'] <= df_5m['ema20']) & (df_5m['close'] < df_5m['ema9']) & df_5m['is_red']

    time_mask = (df_5m['close_time_t'] >= pd.Timestamp('09:20:00').time()) & (df_5m['close_time_t'] <= pd.Timestamp('14:30:00').time())
    df_5m_sub = df_5m[time_mask].copy()

    # 2. Load Options Data
    print("2. Loading 2024-2026 Options Vault & PCR...")
    clean_path = Path("data/nifty_option_chain_1min_clean.parquet")
    opt_table = pq.read_table(clean_path, columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'oi'])
    df_opt = opt_table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['expiry_date'] = df_opt['expiry'].dt.date
    all_dates = sorted(df_opt['date'].unique())

    oi_agg = df_opt.groupby(['timestamp', 'option_type'])['oi'].sum().unstack(fill_value=0)
    pcr_map = (oi_agg['PE'] / oi_agg['CE'].replace(0, 1)).to_dict()

    # Index trajectories
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

    print(f"   Indexed {len(daily_cache)} sessions into memory.\n")

    # 3. Simulate Levers
    # Baseline: D45_L15, 40T/60SL, Tue-Thu-Fri
    def simulate_engine(name, htf_filter=False, credit_floor=0.0, target_pct=0.40, sl_pct=0.60, deep_trail=False, compound=False):
        trades = []
        equity = 200000.0
        
        for dt in all_dates:
            day_name = pd.to_datetime(dt).day_name()
            if day_name not in ['Tuesday', 'Thursday', 'Friday']:
                continue # Tue-Thu-Fri Champion Days
                
            if dt not in daily_cache:
                continue
            day = daily_cache[dt]
            trajs = day['trajs']
            pe_strikes = day['pe_strikes']
            ce_strikes = day['ce_strikes']
            target_exp = day['exp']
            
            # Sizing: Fixed 1 Lot vs Compounding
            base_lot = get_nifty_lot_size(dt)
            if compound:
                num_lots = max(1, int(equity // 75000))
                lot_size = base_lot * num_lots
            else:
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
                
                # HTF Filter
                if htf_filter:
                    if is_bull and not bool(bar['htf_bull']):
                        continue
                    if is_bear and not bool(bar['htf_bear']):
                        continue
                        
                if not is_bull and not is_bear:
                    continue
                    
                curr_pcr = pcr_map.get(bar['close_time'], 1.0)
                if is_bull and curr_pcr < 1.0:
                    continue
                if is_bear and curr_pcr > 1.0:
                    continue
                    
                strat_type = "BULL_PUT_SPREAD" if is_bull else "BEAR_CALL_SPREAD"
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
                if net_credit < credit_floor or net_credit <= 0.5:
                    continue
                    
                times_sub_s = traj_s['times'][idx_match_s[0]:]
                times_sub_l = traj_l['times'][idx_match_l[0]:]
                ltp_sub_s = traj_s['ltp'][idx_match_s[0]:]
                ltp_sub_l = traj_l['ltp'][idx_match_l[0]:]
                s_dict = dict(zip(times_sub_s, ltp_sub_s))
                common_times = [t for t in times_sub_l if t in s_dict]
                if not common_times:
                    continue
                    
                tgt_val = net_credit * (1.0 - target_pct)
                current_sl_val = net_credit * (1.0 + sl_pct)
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
                    
                    # Breakeven @ +25%
                    if not be_active and curr_spread <= (net_credit * 0.75):
                        be_active = True
                        current_sl_val = net_credit
                        
                    # Deep Trailing: If reached 50% target, don't exit! Move SL to 35% profit and target 80% decay!
                    if deep_trail and not trail_active and curr_spread <= (net_credit * 0.50):
                        trail_active = True
                        current_sl_val = net_credit * 0.65 # Lock in 35% profit
                        tgt_val = net_credit * 0.20 # Aim for 80% decay!
                        
                    if curr_spread <= tgt_val:
                        exit_time = ct
                        exit_s_p = curr_s
                        exit_l_p = curr_l
                        exit_reason = "DEEP_TARGET" if deep_trail and trail_active else "TARGET"
                        break
                        
                    if curr_spread >= current_sl_val:
                        exit_time = ct
                        exit_s_p = curr_s
                        exit_l_p = curr_l
                        exit_reason = "TRAILED_PROFIT" if trail_active else ("BREAKEVEN_SL" if be_active else "STOP_LOSS")
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
                    'exit_reason': exit_reason
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
        
        tdf['cum_pnl'] = tdf['net_pnl'].cumsum()
        tdf['equity'] = 200000.0 + tdf['cum_pnl']
        peak = tdf['equity'].cummax()
        dd_pts = peak - tdf['equity']
        dd_pct = (dd_pts / peak).max() * 100.0
        
        total_net = tdf['net_pnl'].sum()
        wins = tdf[tdf['net_pnl'] > 0]
        losses = tdf[tdf['net_pnl'] <= 0]
        win_rate = (len(wins) / len(tdf)) * 100.0
        gross_win = wins['net_pnl'].sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses['net_pnl'].sum()) if len(losses) > 0 else 1.0
        pf = gross_win / gross_loss if gross_loss > 0 else 99.0

        return {
            'Lever_Name': name,
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

    print("3. Executing Alpha Booster Simulations across all 6 Levers...")
    experiments = [
        {"name": "Lever 0: Baseline Champion (D45_L15 Tue-Thu-Fri 1 Lot)", "htf": False, "floor": 0.0, "tgt": 0.40, "sl": 0.60, "trail": False, "comp": False},
        {"name": "Lever 1: Premium Floor (Net Credit >= 85 pts)",          "htf": False, "floor": 85.0, "tgt": 0.40, "sl": 0.60, "trail": False, "comp": False},
        {"name": "Lever 2: Deep Decay Runner (Lock 35% & Trail to 80%)",   "htf": False, "floor": 0.0, "tgt": 0.40, "sl": 0.60, "trail": True,  "comp": False},
        {"name": "Lever 3: HTF 15m Trend Confluence + 5m Trigger",        "htf": True,  "floor": 0.0, "tgt": 0.40, "sl": 0.60, "trail": False, "comp": False},
        {"name": "Lever 4: Master Synergy (Premium Floor + Deep Trail)",     "htf": False, "floor": 85.0, "tgt": 0.40, "sl": 0.60, "trail": True,  "comp": False},
        {"name": "Lever 5: Dynamic Compounding (1 Lot per Rs. 75k Equity)","htf": False, "floor": 85.0, "tgt": 0.40, "sl": 0.60, "trail": True,  "comp": True},
    ]

    summary_list = []
    for exp in experiments:
        res = simulate_engine(
            name=exp['name'],
            htf_filter=exp['htf'],
            credit_floor=exp['floor'],
            target_pct=exp['tgt'],
            sl_pct=exp['sl'],
            deep_trail=exp['trail'],
            compound=exp['comp']
        )
        summary_list.append(res)
        print(f"   -> Finished {exp['name']}: Net PnL = Rs. {res['Total_Net_PnL']:+,.2f} | Avg/mo = Rs. {res['Average_Monthly_PnL']:+,.2f} | Max DD = {res['Max_DD_%']:.2f}%")

    res_df = pd.DataFrame(summary_list)
    out_csv = Path("results/profit_booster_research_results.csv")
    res_df.to_csv(out_csv, index=False)
    print(f"\n4. Saved Profit Booster Research Results to {out_csv}")

    print("\n" + "=" * 135)
    print("                    PROFIT BOOSTER LEADERBOARD (RANKED BY TOTAL NET PNL & MONTHLY ALPHA)                    ")
    print("=" * 135)
    cols = ['Lever_Name', 'Total_Trades', 'Win_Rate_%', 'Total_Net_PnL', 'Profit_Factor', 'Max_DD_%', 'Months_>=5k', 'Months_>=10k', 'Average_Monthly_PnL']
    print(res_df[cols].to_string(index=False))
    print("=" * 135 + "\n")

if __name__ == '__main__':
    run_booster_research()
