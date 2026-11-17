"""
========================================================================================
QUANT STRATEGY: TRIPLE SUPERTREND OPTION BUYING STRATEGY (5-MIN vs 15-MIN SANITY CHECK)
========================================================================================
Architecture & Strategy Specification:
1. Account & Sizing:
   - Initial Capital: Rs. 2,00,000.
   - Sizing: 1 Lot (1 x get_nifty_lot_size(date)). In Jan 2025, lot size = 75 qty.
   - Max 2 trades per day (1 active position at a time).

2. 3 Supertrends (Computed on NSE-aligned 5-min and 15-min Spot Bars):
   - Fast ST1:   Period=7,  Multiplier=1.5
   - Medium ST2: Period=10, Multiplier=2.0 (Used for entry signal & exit flip)
   - Slow ST3:   Period=14, Multiplier=3.0

3. Entry Rules (Evaluated at timeframe bar close, executed on the very next 1-minute bar):
   - Time Window: Bar close between 09:30:00 and 14:30:00 IST (09:15-09:29 strictly skipped; no entries after 14:30).
   - Max Candle Size Filter: Signal candle range (High - Low) must be strictly < 45 points on Spot.
   - Bullish Signal: All 3 STs GREEN and Signal Candle GREEN (Close > Open) -> BUY 1 Lot ATM CE (nearest weekly expiry > today).
   - Bearish Signal: All 3 STs RED and Signal Candle RED (Close < Open) -> BUY 1 Lot ATM PE (nearest weekly expiry > today).
   - ATM Strike: round(Spot_close / 50) * 50.

4. Smart Hybrid Exit Rules (Tracked minute-by-minute on 1-minute option prices):
   - Rule A: ST2 (10, 2.0) flips color against the position on a timeframe bar close.
   - Rule B: Option hits a 20% Hard Stop-Loss (with Breakeven Trailing: once option reaches +25% profit, SL moves to entry price).
   - Rule C: Intraday forced square-off at 15:15:00 IST.

5. Costs:
   - Brokerage: Rs. 0 (Kotak Neo Zero-Brokerage Plan).
   - Slippage: 0.20% on traded option turnover (entry + exit).

6. Outputs:
   - results/triple_st_5m_jan2025.csv
   - results/triple_st_15m_jan2025.csv
   - Terminal: Side-by-Side Comparison Scorecard + Full Trade-by-Trade Tables.
========================================================================================
"""

import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow.parquet as pq

# Add project root to sys.path
sys.path.insert(0, os.path.abspath("."))
from utils.nifty_lot_size import get_nifty_lot_size


def calculate_supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.Series:
    """
    Computes Supertrend trend series (1 = Bullish/Green, -1 = Bearish/Red).
    """
    df_calc = df[['open', 'high', 'low', 'close']].copy()
    df_calc['prev_close'] = df_calc['close'].shift(1)
    df_calc['tr1'] = df_calc['high'] - df_calc['low']
    df_calc['tr2'] = (df_calc['high'] - df_calc['prev_close']).abs()
    df_calc['tr3'] = (df_calc['low'] - df_calc['prev_close']).abs()
    df_calc['tr'] = df_calc[['tr1', 'tr2', 'tr3']].max(axis=1)
    df_calc['atr'] = df_calc['tr'].ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    hl2 = (df_calc['high'] + df_calc['low']) / 2
    basic_upper = hl2 + multiplier * df_calc['atr']
    basic_lower = hl2 - multiplier * df_calc['atr']

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = pd.Series(index=df_calc.index, dtype=int)

    for i in range(period, len(df_calc)):
        prev_close = df_calc['close'].iloc[i-1]
        
        if basic_upper.iloc[i] < final_upper.iloc[i-1] or prev_close > final_upper.iloc[i-1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]
            
        if basic_lower.iloc[i] > final_lower.iloc[i-1] or prev_close < final_lower.iloc[i-1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]
            
        prev_trend = trend.iloc[i-1] if not np.isnan(trend.iloc[i-1]) else 1
        curr_close = df_calc['close'].iloc[i]
        
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


def run_strategy_simulation(df_spot_sub: pd.DataFrame, days_opt: dict, tf_str: str, bar_minutes: int) -> pd.DataFrame:
    """
    Executes the Triple Supertrend option buying strategy on the specified timeframe.
    """
    df_tf = df_spot_sub.resample(tf_str, origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_tf['st1'] = calculate_supertrend(df_tf, 7, 1.5)
    df_tf['st2'] = calculate_supertrend(df_tf, 10, 2.0)
    df_tf['st3'] = calculate_supertrend(df_tf, 14, 3.0)
    df_tf['range'] = df_tf['high'] - df_tf['low']
    df_tf['is_green'] = df_tf['close'] > df_tf['open']
    df_tf['is_red'] = df_tf['close'] < df_tf['open']
    df_tf['close_time'] = df_tf.index + pd.Timedelta(minutes=bar_minutes)

    # Index by close_time for instant lookup during minute tracking
    bar_by_close = {row['close_time']: row for _, row in df_tf.iterrows()}

    jan_dates = sorted(list(set(df_tf.loc['2025-01-01':'2025-01-31'].index.date)))
    initial_cap = 200000.0
    running_cap = initial_cap
    trades = []

    for dt in jan_dates:
        if dt not in days_opt:
            continue
        day_opt = days_opt[dt]
        day_bars = df_tf[df_tf.index.date == dt]

        daily_trades_count = 0
        last_exit_ts = None

        for _, bar in day_bars.iterrows():
            if daily_trades_count >= 2:
                break
                
            bar_close_ts = bar['close_time']
            bar_close_time = bar_close_ts.time()

            # If there was a previous trade today, only enter after that trade has exited!
            if last_exit_ts is not None and bar_close_ts <= last_exit_ts:
                continue

            # Time window: 09:30:00 <= close_time <= 14:30:00
            if not (pd.Timestamp('09:30:00').time() <= bar_close_time <= pd.Timestamp('14:30:00').time()):
                continue

            # Range filter: signal candle range < 45 pts
            if bar['range'] >= 45.0:
                continue

            # Check 3 Supertrends and candle color
            bull_sig = (bar['st1'] == 1 and bar['st2'] == 1 and bar['st3'] == 1 and bar['is_green'])
            bear_sig = (bar['st1'] == -1 and bar['st2'] == -1 and bar['st3'] == -1 and bar['is_red'])

            if not (bull_sig or bear_sig):
                continue

            opt_type = 'CE' if bull_sig else 'PE'
            spot_close = bar['close']
            atm_strike = round(spot_close / 50) * 50

            # Execute at next 1-minute bar for zero look-ahead bias
            exec_ts = bar_close_ts + pd.Timedelta(minutes=1)
            exec_time = exec_ts.time()

            # Nearest weekly expiry strictly after today
            valid_exp = sorted([e for e in day_opt['expiry_date'].unique() if e > dt])
            if not valid_exp:
                continue
            sel_exp = valid_exp[0]
            dte = (sel_exp - dt).days

            leg_data = day_opt[(day_opt['expiry_date'] == sel_exp) & 
                               (day_opt['option_type'] == opt_type) & 
                               (day_opt['strike_price'] == atm_strike)].drop_duplicates(subset=['time']).set_index('time')

            if exec_time not in leg_data.index:
                continue

            entry_p = float(leg_data.loc[exec_time, 'ltp'])
            if entry_p <= 0:
                continue

            lot_size = get_nifty_lot_size(dt)
            qty = 1 * lot_size
            daily_trades_count += 1

            # Continuous minute-by-minute tracking
            all_minutes = sorted([t for t in leg_data.index if exec_time <= t <= pd.Timestamp('15:15:00').time()])
            
            sl_p = 0.80 * entry_p
            be_activated = False
            max_p = entry_p

            exit_time = pd.Timestamp('15:15:00').time()
            exit_p = entry_p
            exit_reason = 'TIME_EXIT (15:15)'

            for m_time in all_minutes:
                curr_p = float(leg_data.loc[m_time, 'ltp'])
                max_p = max(max_p, curr_p)

                # Check Breakeven Trailing (+25% profit)
                if curr_p >= 1.25 * entry_p and not be_activated:
                    be_activated = True
                    sl_p = max(sl_p, entry_p)

                # Check Stop-Loss
                if curr_p <= sl_p:
                    exit_time = m_time
                    exit_p = curr_p
                    exit_reason = 'BREAKEVEN_SL (0%)' if be_activated else 'HARD_SL (-20%)'
                    break

                # Check ST2 flip on timeframe bar close
                curr_ts = pd.Timestamp(f"{dt} {m_time}")
                if curr_ts in bar_by_close:
                    completed_bar = bar_by_close[curr_ts]
                    st2_state = completed_bar['st2']
                    if opt_type == 'CE' and st2_state == -1:
                        exit_time = m_time
                        exit_p = curr_p
                        exit_reason = 'ST2_FLIP (BEARISH)'
                        break
                    elif opt_type == 'PE' and st2_state == 1:
                        exit_time = m_time
                        exit_p = curr_p
                        exit_reason = 'ST2_FLIP (BULLISH)'
                        break

                # Check 15:15:00 forced exit
                if m_time == pd.Timestamp('15:15:00').time():
                    exit_time = m_time
                    exit_p = curr_p
                    exit_reason = 'TIME_EXIT (15:15)'
                    break

            last_exit_ts = pd.Timestamp(f"{dt} {exit_time}")
            gross_pnl = (exit_p - entry_p) * qty
            costs = 0.0020 * (entry_p + exit_p) * qty
            net_pnl = gross_pnl - costs
            running_cap += net_pnl

            trades.append({
                'Timeframe': tf_str,
                'Date': str(dt),
                'Day': pd.Timestamp(dt).strftime('%A'),
                'Signal_Bar_Close': str(bar_close_time),
                'Spot_Signal': spot_close,
                'Entry_Time': str(exec_time),
                'ATM_Strike': atm_strike,
                'Option_Type': opt_type,
                'Expiry': str(sel_exp),
                'DTE': dte,
                'Entry_Price': round(entry_p, 2),
                'Exit_Time': str(exit_time),
                'Exit_Price': round(exit_p, 2),
                'Exit_Reason': exit_reason,
                'Points_PnL': round(exit_p - entry_p, 2),
                'Gross_PnL_Rs': round(gross_pnl, 2),
                'Costs_Rs': round(costs, 2),
                'Net_PnL_Rs': round(net_pnl, 2),
                'Running_Capital': round(running_cap, 2)
            })

    return pd.DataFrame(trades)


def compute_metrics(df_trades: pd.DataFrame, initial_capital: float = 200000.0) -> dict:
    """
    Computes key quant metrics for a trade ledger.
    """
    if df_trades.empty:
        return {}
        
    total_trades = len(df_trades)
    wins = df_trades[df_trades['Net_PnL_Rs'] > 0]
    losses = df_trades[df_trades['Net_PnL_Rs'] < 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0

    gross_pnl = df_trades['Gross_PnL_Rs'].sum()
    total_costs = df_trades['Costs_Rs'].sum()
    net_pnl = df_trades['Net_PnL_Rs'].sum()
    pct_return = (net_pnl / initial_capital) * 100
    final_cap = initial_capital + net_pnl

    hard_sl = (df_trades['Exit_Reason'].str.contains('HARD_SL')).sum()
    be_sl = (df_trades['Exit_Reason'].str.contains('BREAKEVEN_SL')).sum()
    st2_flip = (df_trades['Exit_Reason'].str.contains('ST2_FLIP')).sum()
    time_exit = (df_trades['Exit_Reason'].str.contains('TIME_EXIT')).sum()

    sum_wins = wins['Net_PnL_Rs'].sum()
    sum_losses = abs(losses['Net_PnL_Rs'].sum())
    pf = (sum_wins / sum_losses) if sum_losses > 0 else np.nan
    avg_win = wins['Net_PnL_Rs'].mean() if win_count > 0 else 0.0
    avg_loss = losses['Net_PnL_Rs'].mean() if loss_count > 0 else 0.0

    equity = df_trades['Running_Capital']
    peak = equity.cummax()
    dd_series = equity - peak
    max_dd_rs = abs(dd_series.min()) if not dd_series.empty else 0.0
    max_dd_pct = (max_dd_rs / peak.loc[dd_series.idxmin()]) * 100 if not dd_series.empty and max_dd_rs > 0 else 0.0

    return {
        'Total_Trades': total_trades,
        'Wins': win_count,
        'Losses': loss_count,
        'Win_Rate_Pct': win_rate,
        'Gross_PnL_Rs': gross_pnl,
        'Costs_Rs': total_costs,
        'Net_PnL_Rs': net_pnl,
        'Pct_Return': pct_return,
        'Final_Capital_Rs': final_cap,
        'Hard_SL_Hits': hard_sl,
        'Breakeven_SL_Hits': be_sl,
        'ST2_Flip_Exits': st2_flip,
        'Time_Exits': time_exit,
        'Profit_Factor': pf,
        'Avg_Win_Rs': avg_win,
        'Avg_Loss_Rs': avg_loss,
        'Max_DD_Rs': max_dd_rs,
        'Max_DD_Pct': max_dd_pct
    }


def main():
    print("=" * 115)
    print("  TRIPLE SUPERTREND OPTION BUYING STRATEGY: 5-MIN vs 15-MIN (JANUARY 2025)   ")
    print("=" * 115)

    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    opt_path = Path("data/nifty_option_chain_1min_clean.parquet")
    res_dir = Path("results")
    res_dir.mkdir(parents=True, exist_ok=True)
    out_5m = res_dir / "triple_st_5m_jan2025.csv"
    out_15m = res_dir / "triple_st_15m_jan2025.csv"

    if not spot_path.exists() or not opt_path.exists():
        print("Error: Master datasets not found.")
        sys.exit(1)

    print("-> Loading Spot Data (warmup from 2024 to Jan 2025)...")
    df_spot = pd.read_parquet(spot_path)
    df_spot_sub = df_spot.loc['2024-01-01 09:15:00':'2025-01-31 15:30:00']

    print("-> Loading January 2025 1-minute options data...")
    table = pq.read_table(opt_path, filters=[
        ('timestamp', '>=', pd.Timestamp('2025-01-01 09:00:00')),
        ('timestamp', '<=', pd.Timestamp('2025-01-31 16:00:00'))
    ], columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'ltp'])
    df_opt = table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['time'] = df_opt['timestamp'].dt.time
    df_opt['expiry_date'] = df_opt['expiry'].dt.date
    days_opt = {d: grp for d, grp in df_opt.groupby('date')}

    print("\n-> Simulating 5-Minute Timeframe...")
    t0 = time.time()
    df_trades_5m = run_strategy_simulation(df_spot_sub, days_opt, '5min', 5)
    df_trades_5m.to_csv(out_5m, index=False)
    print(f"   5-Min completed: {len(df_trades_5m)} trades in {time.time()-t0:.2f}s | Saved to {out_5m}")

    print("\n-> Simulating 15-Minute Timeframe...")
    t1 = time.time()
    df_trades_15m = run_strategy_simulation(df_spot_sub, days_opt, '15min', 15)
    df_trades_15m.to_csv(out_15m, index=False)
    print(f"   15-Min completed: {len(df_trades_15m)} trades in {time.time()-t1:.2f}s | Saved to {out_15m}")

    m_5m = compute_metrics(df_trades_5m)
    m_15m = compute_metrics(df_trades_15m)

    # 1. Print Side-by-Side Comparison Scorecard
    print("\n" + "=" * 85)
    print("         EXECUTIVE SIDE-BY-SIDE SCORECARD: 5-MIN vs 15-MIN (JANUARY 2025)         ")
    print("=" * 85)
    print(f"{'Performance Metric':<32} {'5-Minute Timeframe':<25} {'15-Minute Timeframe':<25}")
    print("-" * 85)
    print(f"{'Starting Capital':<32} Rs. 200,000.00           Rs. 200,000.00")
    print(f"{'Final Capital':<32} Rs. {m_5m['Final_Capital_Rs']:<21,.2f} Rs. {m_15m['Final_Capital_Rs']:<21,.2f}")
    print(f"{'Net Realized Profit/Loss':<32} Rs. {m_5m['Net_PnL_Rs']:<21,.2f} Rs. {m_15m['Net_PnL_Rs']:<21,.2f}")
    print(f"{'Percentage Return on Capital':<32} {m_5m['Pct_Return']:>+6.2f}%                   {m_15m['Pct_Return']:>+6.2f}%")
    print("-" * 85)
    print(f"{'Total Trades Taken':<32} {m_5m['Total_Trades']:<25} {m_15m['Total_Trades']:<25}")
    print(f"{'Winning Trades':<32} {m_5m['Wins']:<25} {m_15m['Wins']:<25}")
    print(f"{'Losing Trades':<32} {m_5m['Losses']:<25} {m_15m['Losses']:<25}")
    print(f"{'Win Rate (%)':<32} {m_5m['Win_Rate_Pct']:<24.1f}% {m_15m['Win_Rate_Pct']:<24.1f}%")
    print(f"{'Profit Factor':<32} {m_5m['Profit_Factor']:<25.2f} {m_15m['Profit_Factor']:<25.2f}")
    print("-" * 85)
    print(f"{'Hard Stop-Loss Hits (-20%)':<32} {m_5m['Hard_SL_Hits']:<25} {m_15m['Hard_SL_Hits']:<25}")
    print(f"{'Breakeven SL Hits (0%)':<32} {m_5m['Breakeven_SL_Hits']:<25} {m_15m['Breakeven_SL_Hits']:<25}")
    print(f"{'ST2 Color Flip Exits':<32} {m_5m['ST2_Flip_Exits']:<25} {m_15m['ST2_Flip_Exits']:<25}")
    print(f"{'Time Exits (15:15:00)':<32} {m_5m['Time_Exits']:<25} {m_15m['Time_Exits']:<25}")
    print("-" * 85)
    print(f"{'Average Winning Trade':<32} +Rs. {m_5m['Avg_Win_Rs']:<20,.2f} +Rs. {m_15m['Avg_Win_Rs']:<20,.2f}")
    print(f"{'Average Losing Trade':<32} -Rs. {abs(m_5m['Avg_Loss_Rs']):<20,.2f} -Rs. {abs(m_15m['Avg_Loss_Rs']):<20,.2f}")
    print(f"{'Max Peak-to-Trough Drawdown':<32} Rs. {m_5m['Max_DD_Rs']:,.2f} ({m_5m['Max_DD_Pct']:.1f}%)    Rs. {m_15m['Max_DD_Rs']:,.2f} ({m_15m['Max_DD_Pct']:.1f}%)")
    print(f"{'Total Slippage & Costs':<32} Rs. {m_5m['Costs_Rs']:<21,.2f} Rs. {m_15m['Costs_Rs']:<21,.2f}")
    print("=" * 85)

    # 2. Print Complete 15-Minute Trade Table (The Winner)
    print("\n" + "=" * 145)
    print("                  15-MINUTE TIMEFRAME: COMPLETE DAY-BY-DAY TRADE LEDGER (WINNER)                  ")
    print("=" * 145)
    hdr = f"{'Date':<10} {'Day':<4} {'EntryTime':<10} {'Strike & Type':<15} {'EntryPrice':<11} {'ExitTime':<9} {'ExitPrice':<10} {'Exit Reason':<20} {'Pts PnL':<9} {'Net PnL (Rs)':<14} {'Capital (Rs)':<12}"
    print(hdr)
    print("-" * 145)
    for _, r in df_trades_15m.iterrows():
        pnl_str = f"+Rs.{r['Net_PnL_Rs']:,.2f}" if r['Net_PnL_Rs'] > 0 else f"-Rs.{abs(r['Net_PnL_Rs']):,.2f}"
        print(
            f"{r['Date']:<10} {r['Day'][:3]:<4} {r['Entry_Time']:<10} {int(r['ATM_Strike'])} {r['Option_Type']:<10} "
            f"{r['Entry_Price']:<11.2f} {r['Exit_Time']:<9} {r['Exit_Price']:<10.2f} {r['Exit_Reason']:<20} "
            f"{r['Points_PnL']:>+7.2f}  {pnl_str:<14} Rs.{r['Running_Capital']:,.2f}"
        )
    print("=" * 145)

    # 3. Print 5-Minute Trade Table
    print("\n" + "=" * 145)
    print("                      5-MINUTE TIMEFRAME: COMPLETE DAY-BY-DAY TRADE LEDGER                      ")
    print("=" * 145)
    print(hdr)
    print("-" * 145)
    for _, r in df_trades_5m.iterrows():
        pnl_str = f"+Rs.{r['Net_PnL_Rs']:,.2f}" if r['Net_PnL_Rs'] > 0 else f"-Rs.{abs(r['Net_PnL_Rs']):,.2f}"
        print(
            f"{r['Date']:<10} {r['Day'][:3]:<4} {r['Entry_Time']:<10} {int(r['ATM_Strike'])} {r['Option_Type']:<10} "
            f"{r['Entry_Price']:<11.2f} {r['Exit_Time']:<9} {r['Exit_Price']:<10.2f} {r['Exit_Reason']:<20} "
            f"{r['Points_PnL']:>+7.2f}  {pnl_str:<14} Rs.{r['Running_Capital']:,.2f}"
        )
    print("=" * 145 + "\n")


if __name__ == "__main__":
    main()
