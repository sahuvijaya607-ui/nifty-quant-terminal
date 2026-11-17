"""
strategies/competitor_strategies_backtest.py
Quantitative Tournament: Competing 5 High-Alpha Credit Spread Strategies against our Champion on Real NIFTY Data.

Strategies Evaluated:
1. CHAMPION BENCHMARK: PCR + 9/20/50 EMA Pullback (D45_L15 Tue-Thu-Fri)
2. STRATEGY 1: Multi-SuperTrend (3 Settings) + VWAP Momentum Spread
3. STRATEGY 2: 15-Minute Opening Range Breakout (ORB) Credit Spread
4. STRATEGY 3: Neutral PCR Market Regime Iron Condor (Dual-Sided Theta Harvest)
5. STRATEGY 4: 0-DTE Expiry Day Accelerated Decay Squeeze Spread
6. STRATEGY 5: Bollinger Band Squeeze + RSI Extreme Reversal Fade Spread
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

def run_tournament():
    t_start = time.time()
    print("=" * 125)
    print("     QUANTITATIVE TOURNAMENT: 5 COMPETITOR CREDIT SPREAD ALGORITHMS VS CHAMPION BENCHMARK     ")
    print("=" * 125)
    print("Underlying: NIFTY 50 Options & Spot Real Parquet Vault")
    print("Execution: 1 Lot Fixed, Round-trip Slippage (0.15%), Kotak Neo Brokerage (Rs. 0)")
    print("=" * 125 + "\n")

    # 1. Spot Data Pre-processing
    print("1. Loading Spot Data and Computing Multi-Indicator Signals...")
    df_spot = pd.read_parquet("data/nifty_spot_master_2020_2026_1min.parquet")
    spot_price_map = dict(zip(df_spot.index, df_spot['close']))

    df_daily = df_spot.resample('1D').agg({'close': 'last'}).dropna()
    df_daily['returns'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
    df_daily['rolling_vol'] = df_daily['returns'].rolling(30).std() * np.sqrt(252)
    df_daily['rolling_vol'] = df_daily['rolling_vol'].bfill().fillna(0.18)
    daily_vol_map = {d.date(): float(v) for d, v in df_daily['rolling_vol'].items()}

    # Resample to 5m bars
    df_5m = df_spot.resample('5min', origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_5m['close_time'] = df_5m.index + pd.Timedelta(minutes=5)
    df_5m['close_time_t'] = df_5m['close_time'].dt.time
    df_5m['date'] = df_5m.index.date

    # Indicators for Champion: 9, 20, 50 EMA
    df_5m['ema9'] = df_5m['close'].ewm(span=9, adjust=False).mean()
    df_5m['ema20'] = df_5m['close'].ewm(span=20, adjust=False).mean()
    df_5m['ema50'] = df_5m['close'].ewm(span=50, adjust=False).mean()
    df_5m['is_green'] = df_5m['close'] >= df_5m['open']
    df_5m['is_red'] = df_5m['close'] <= df_5m['open']

    # Strategy 1 Indicators: Typical Price Mean Anchor (VWAP Proxy) + 3 SuperTrends (7,2), (10,3), (14,4)
    df_5m['tp'] = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
    df_5m['vwap'] = df_5m.groupby('date')['tp'].transform(lambda s: s.expanding().mean())

    print("   Computing 3 SuperTrend settings (Fast 7,2; Medium 10,3; Slow 14,4)...")
    st1 = compute_supertrend(df_5m, period=7, multiplier=2.0)
    st2 = compute_supertrend(df_5m, period=10, multiplier=3.0)
    st3 = compute_supertrend(df_5m, period=14, multiplier=4.0)
    df_5m['st_bull'] = (st1 == 1) & (st2 == 1) & (st3 == 1)
    df_5m['st_bear'] = (st1 == -1) & (st2 == -1) & (st3 == -1)

    # Strategy 2 Indicators: 15-Minute Opening Range Breakout (ORB)
    # First 3 bars: 09:15-09:20, 09:20-09:25, 09:25-09:30
    orb_high_map = {}
    orb_low_map = {}
    for dt, grp in df_5m.groupby('date'):
        first_3 = grp.iloc[:3]
        if len(first_3) >= 3:
            orb_high_map[dt] = float(first_3['high'].max())
            orb_low_map[dt] = float(first_3['low'].min())
    df_5m['orb_high'] = df_5m['date'].map(orb_high_map)
    df_5m['orb_low'] = df_5m['date'].map(orb_low_map)

    # Strategy 5 Indicators: Bollinger Bands (20, 2.0) + RSI (14)
    df_5m['bb_mid'] = df_5m['close'].rolling(20).mean()
    df_5m['bb_std'] = df_5m['close'].rolling(20).std()
    df_5m['bb_upper'] = df_5m['bb_mid'] + (2.0 * df_5m['bb_std'])
    df_5m['bb_lower'] = df_5m['bb_mid'] - (2.0 * df_5m['bb_std'])

    delta_c = df_5m['close'].diff()
    gain = (delta_c.where(delta_c > 0, 0)).rolling(14).mean()
    loss = (-delta_c.where(delta_c < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, 0.001)
    df_5m['rsi'] = 100 - (100 / (1 + rs))

    # Trading Window Mask
    time_mask = (df_5m['close_time_t'] >= pd.Timestamp('09:20:00').time()) & (df_5m['close_time_t'] <= pd.Timestamp('14:30:00').time())
    df_5m_sub = df_5m[time_mask].copy()

    # 2. Load Options Data and build daily trajectories
    print("2. Loading 2024-2026 Options Vault & PCR...")
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
        expiries = sorted([exp for exp in day_df['expiry_date'].unique() if exp >= dt])
        if not expiries:
            continue
        # Weekly nearest expiry
        target_exp = expiries[0]
        # Check if today is expiry (0 DTE)
        is_expiry = (target_exp == dt)
        if is_expiry and len(expiries) > 1:
            next_exp = expiries[1]
        else:
            next_exp = target_exp
            
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
            'is_expiry': is_expiry,
            'next_exp': next_exp,
            'trajs': trajs,
            'pe_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'PE']),
            'ce_strikes': sorted([int(stk) for stk, otype in trajs.keys() if otype == 'CE'])
        }

    print(f"   Indexed {len(daily_cache)} sessions into memory.\n")

    # Helper execution simulator
    def simulate_strategy(strat_id: str, name: str):
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
            is_expiry = day['is_expiry']

            # Day filtering per strategy
            if strat_id == 'CHAMPION':
                if day_name not in ['Tuesday', 'Thursday', 'Friday']:
                    continue
            elif strat_id == 'STRAT_4_EXPIRY':
                # Only on Expiry days (0 DTE)
                if not is_expiry:
                    continue
            else:
                # Competitor strategies 1, 2, 3, 5 operate on all normal trading days (Mon-Fri)
                pass

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
                max_daily = 1 if strat_id == 'STRAT_2_ORB' else 2
                if daily_trades >= max_daily:
                    break
                    
                entry_time = bar['close_time'] + pd.Timedelta(minutes=1)
                if in_pos and pos_exit is not None and entry_time < pos_exit:
                    continue
                else:
                    in_pos = False

                curr_pcr = pcr_map.get(bar['close_time'], 1.0)
                is_bull = False
                is_bear = False
                is_condor = False

                # Strategy Logic Routing
                if strat_id == 'CHAMPION':
                    # 9/20/50 EMA Pullback + PCR
                    c_bull = (bar['close'] > bar['ema50']) & (bar['ema9'] >= bar['ema20']) & (bar['close'] > bar['ema9']) & bar['is_green']
                    c_bear = (bar['close'] < bar['ema50']) & (bar['ema9'] <= bar['ema20']) & (bar['close'] < bar['ema9']) & bar['is_red']
                    if c_bull and curr_pcr >= 1.0:
                        is_bull = True
                    elif c_bear and curr_pcr <= 1.0:
                        is_bear = True

                elif strat_id == 'STRAT_1_SUPERTREND_VWAP':
                    # Multi-SuperTrend (3 Settings) + VWAP Anchor
                    # Bull: Spot > VWAP and all 3 SuperTrends Bullish
                    st_bull = bar['st_bull'] and (bar['close'] > bar['vwap']) and bar['is_green']
                    st_bear = bar['st_bear'] and (bar['close'] < bar['vwap']) and bar['is_red']
                    if st_bull and curr_pcr >= 0.95:
                        is_bull = True
                    elif st_bear and curr_pcr <= 1.05:
                        is_bear = True

                elif strat_id == 'STRAT_2_ORB':
                    # 15m Opening Range Breakout: Entry strictly between 09:30 and 11:30
                    if bar['close_time_t'] >= pd.Timestamp('09:35:00').time() and bar['close_time_t'] <= pd.Timestamp('11:30:00').time():
                        orb_h = bar['orb_high']
                        orb_l = bar['orb_low']
                        if pd.notna(orb_h) and pd.notna(orb_l):
                            if bar['close'] > orb_h and bar['is_green'] and curr_pcr >= 0.90:
                                is_bull = True
                            elif bar['close'] < orb_l and bar['is_red'] and curr_pcr <= 1.10:
                                is_bear = True

                elif strat_id == 'STRAT_3_IRON_CONDOR':
                    # Neutral PCR Regime (0.85 <= PCR <= 1.15) & Range-bound (Between EMA 50 & VWAP)
                    # Deploy Iron Condor (Sell Bull Put Spread + Sell Bear Call Spread)
                    if 0.85 <= curr_pcr <= 1.15:
                        spread_vwap = abs(bar['close'] - bar['vwap']) / bar['close']
                        if spread_vwap < 0.005 and bar['close_time_t'] >= pd.Timestamp('09:30:00').time() and bar['close_time_t'] <= pd.Timestamp('11:00:00').time():
                            is_condor = True

                elif strat_id == 'STRAT_4_EXPIRY':
                    # 0-DTE Expiry Special: High-velocity theta between 11:30 AM and 13:45 PM
                    if bar['close_time_t'] >= pd.Timestamp('11:30:00').time() and bar['close_time_t'] <= pd.Timestamp('13:45:00').time():
                        # Directional bias with VWAP + 9 EMA
                        if bar['close'] > bar['vwap'] and bar['close'] > bar['ema9'] and bar['is_green']:
                            is_bull = True
                        elif bar['close'] < bar['vwap'] and bar['close'] < bar['ema9'] and bar['is_red']:
                            is_bear = True

                elif strat_id == 'STRAT_5_MEAN_REVERSION':
                    # Bollinger Band Extreme + RSI Exhaustion Fade
                    # Bullish reversal: Close below Lower BB and RSI < 30 (Oversold Panic Fade)
                    # Bearish reversal: Close above Upper BB and RSI > 70 (Overbought Exhaustion Fade)
                    if bar['close'] < bar['bb_lower'] and bar['rsi'] < 30 and bar['is_green']:
                        is_bull = True
                    elif bar['close'] > bar['bb_upper'] and bar['rsi'] > 70 and bar['is_red']:
                        is_bear = True

                if not is_bull and not is_bear and not is_condor:
                    continue

                if entry_time not in spot_price_map:
                    continue
                spot_entry = float(spot_price_map[entry_time])

                # Deltas Selection:
                # For directional: D45 short, D15 long (or D30/D10 for 0-DTE)
                target_short_delta = 0.30 if strat_id in ['STRAT_3_IRON_CONDOR', 'STRAT_4_EXPIRY'] else 0.45
                target_long_delta = 0.10 if strat_id in ['STRAT_3_IRON_CONDOR', 'STRAT_4_EXPIRY'] else 0.15

                # Parameter presets per strategy
                target_pct = 0.50 if strat_id == 'STRAT_4_EXPIRY' else 0.40
                sl_pct = 0.80 if strat_id == 'STRAT_4_EXPIRY' else 0.60

                if is_condor:
                    # Execute Iron Condor: 1 Bull Put Spread + 1 Bear Call Spread
                    # 1. Put Leg
                    if len(pe_strikes) < 2 or len(ce_strikes) < 2:
                        continue
                    p_deltas = calc_bs_deltas(spot_entry, np.array(pe_strikes), dte_years, 0.07, sigma, 'PE')
                    c_deltas = calc_bs_deltas(spot_entry, np.array(ce_strikes), dte_years, 0.07, sigma, 'CE')
                    
                    s_pe = pe_strikes[np.argmin(np.abs(p_deltas - (-0.30)))]
                    l_pe = pe_strikes[np.argmin(np.abs(p_deltas - (-0.10)))]
                    s_ce = ce_strikes[np.argmin(np.abs(c_deltas - 0.30))]
                    l_ce = ce_strikes[np.argmin(np.abs(c_deltas - 0.10))]

                    if s_pe <= l_pe or s_ce >= l_ce:
                        continue

                    keys = [(s_pe, 'PE'), (l_pe, 'PE'), (s_ce, 'CE'), (l_ce, 'CE')]
                    if any(k not in trajs for k in keys):
                        continue

                    # Check entry prices
                    try:
                        p_entry_s = float(trajs[(s_pe, 'PE')]['ltp'][np.where(trajs[(s_pe, 'PE')]['times'] == entry_time)[0][0]])
                        p_entry_l = float(trajs[(l_pe, 'PE')]['ltp'][np.where(trajs[(l_pe, 'PE')]['times'] == entry_time)[0][0]])
                        c_entry_s = float(trajs[(s_ce, 'CE')]['ltp'][np.where(trajs[(s_ce, 'CE')]['times'] == entry_time)[0][0]])
                        c_entry_l = float(trajs[(l_ce, 'CE')]['ltp'][np.where(trajs[(l_ce, 'CE')]['times'] == entry_time)[0][0]])
                    except:
                        continue

                    pe_credit = p_entry_s - p_entry_l
                    ce_credit = c_entry_s - c_entry_l
                    total_credit = pe_credit + ce_credit
                    if total_credit <= 1.0:
                        continue

                    # Simulate Iron Condor decay
                    # Common times
                    ct_pe = set(trajs[(s_pe, 'PE')]['times']).intersection(set(trajs[(l_pe, 'PE')]['times']))
                    ct_ce = set(trajs[(s_ce, 'CE')]['times']).intersection(set(trajs[(l_ce, 'CE')]['times']))
                    common_ts = sorted(list(ct_pe.intersection(ct_ce)))
                    common_ts = [t for t in common_ts if t >= entry_time]
                    if not common_ts:
                        continue

                    tgt_val = total_credit * 0.50
                    sl_val = total_credit * 1.50
                    exit_time = None
                    exit_condor_val = total_credit
                    exit_reason = None

                    # Fast lookup dicts
                    d_spe = dict(zip(trajs[(s_pe, 'PE')]['times'], trajs[(s_pe, 'PE')]['ltp']))
                    d_lpe = dict(zip(trajs[(l_pe, 'PE')]['times'], trajs[(l_pe, 'PE')]['ltp']))
                    d_sce = dict(zip(trajs[(s_ce, 'CE')]['times'], trajs[(s_ce, 'CE')]['ltp']))
                    d_lce = dict(zip(trajs[(l_ce, 'CE')]['times'], trajs[(l_ce, 'CE')]['ltp']))

                    for ct in common_ts:
                        ct_t = pd.Timestamp(ct).time()
                        curr_pe_sp = d_spe[ct] - d_lpe[ct]
                        curr_ce_sp = d_sce[ct] - d_lce[ct]
                        curr_condor = curr_pe_sp + curr_ce_sp

                        if curr_condor <= tgt_val:
                            exit_time = ct
                            exit_condor_val = curr_condor
                            exit_reason = "TARGET"
                            break
                        if curr_condor >= sl_val:
                            exit_time = ct
                            exit_condor_val = curr_condor
                            exit_reason = "STOP_LOSS"
                            break
                        if ct_t >= pd.Timestamp('15:15:00').time():
                            exit_time = ct
                            exit_condor_val = curr_condor
                            exit_reason = "TIME_EXIT"
                            break

                    if exit_time is None:
                        exit_time = common_ts[-1]
                        exit_condor_val = (d_spe[exit_time] - d_lpe[exit_time]) + (d_sce[exit_time] - d_lce[exit_time])
                        exit_reason = "EOD_CLOSE"

                    spread_pnl = total_credit - exit_condor_val
                    gross_pnl = spread_pnl * lot_size
                    turnover = (p_entry_s + p_entry_l + c_entry_s + c_entry_l + d_spe[exit_time] + d_lpe[exit_time] + d_sce[exit_time] + d_lce[exit_time]) * lot_size
                    slip = turnover * 0.0015
                    net_pnl = gross_pnl - slip
                    equity += net_pnl

                    trades.append({
                        'date': dt,
                        'year_month': f"{dt.year}-{dt.month:02d}",
                        'strategy': name,
                        'net_pnl': net_pnl,
                        'gross_pnl': gross_pnl,
                        'slippage': slip,
                        'exit_reason': exit_reason
                    })
                    daily_trades += 1
                    in_pos = True
                    pos_exit = exit_time
                    continue

                # Standard Directional Spread execution
                otype = "PE" if is_bull else "CE"
                available_strikes = pe_strikes if is_bull else ce_strikes
                if len(available_strikes) < 2:
                    continue

                deltas = calc_bs_deltas(spot_entry, np.array(available_strikes), dte_years, 0.07, sigma, otype)
                if is_bull:
                    idx_s = np.argmin(np.abs(deltas - (-target_short_delta)))
                    idx_l = np.argmin(np.abs(deltas - (-target_long_delta)))
                    short_k = available_strikes[idx_s]
                    long_k = available_strikes[idx_l]
                    if short_k <= long_k:
                        continue
                else:
                    idx_s = np.argmin(np.abs(deltas - target_short_delta))
                    idx_l = np.argmin(np.abs(deltas - target_long_delta))
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

                tgt_val = net_credit * (1.0 - target_pct)
                current_sl_val = net_credit * (1.0 + sl_pct)
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
                    'strategy': name,
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
            'Strategy_ID': strat_id,
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
            'Worst_Month': monthly.min(),
            'trades_df': tdf
        }

    competitors = [
        ('CHAMPION',               'Benchmark Champion: PCR + 9/20/50 EMA Pullback (Tue-Thu-Fri)'),
        ('STRAT_1_SUPERTREND_VWAP','Competitor 1: 3-Tier SuperTrend + VWAP Momentum Spread'),
        ('STRAT_2_ORB',            'Competitor 2: 15-Min Opening Range Breakout (ORB) Spread'),
        ('STRAT_3_IRON_CONDOR',    'Competitor 3: Neutral PCR Regime Iron Condor (Dual-Theta)'),
        ('STRAT_4_EXPIRY',         'Competitor 4: 0-DTE Expiry Day Theta Squeeze Spread'),
        ('STRAT_5_MEAN_REVERSION', 'Competitor 5: Bollinger Bands + RSI Panic/Exhaustion Fade')
    ]

    print("3. Executing Tournament Simulation across all 6 Strategies...")
    results = []
    trades_dict = {}

    for s_id, s_name in competitors:
        print(f"   -> Running {s_name}...")
        res = simulate_strategy(s_id, s_name)
        if res:
            trades_dict[s_id] = res.pop('trades_df')
            results.append(res)
            print(f"      [DONE] Net PnL = Rs. {res['Total_Net_PnL']:+,.2f} | WinRate = {res['Win_Rate_%']:.1f}% | Avg/Mo = Rs. {res['Average_Monthly_PnL']:+,.2f} | Max DD = {res['Max_DD_%']:.2f}% | >=5k: {res['Months_>=5k']}")

    res_df = pd.DataFrame(results)
    out_csv = Path("results/competitor_strategies_tournament_results.csv")
    res_df.to_csv(out_csv, index=False)
    print(f"\n4. Saved Competitor Tournament Results to {out_csv}")

    print("\n" + "=" * 140)
    print("                    QUANTITATIVE TOURNAMENT LEADERBOARD (RANKED BY TOTAL NET PROFIT)                    ")
    print("=" * 140)
    cols = ['Strategy_Name', 'Total_Trades', 'Win_Rate_%', 'Total_Net_PnL', 'Profit_Factor', 'Max_DD_%', 'Months_>=5k', 'Average_Monthly_PnL']
    print(res_df[cols].to_string(index=False))
    print("=" * 140 + "\n")

if __name__ == '__main__':
    run_tournament()
