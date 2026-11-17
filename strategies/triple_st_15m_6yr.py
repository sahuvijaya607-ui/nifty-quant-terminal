"""
========================================================================================
QUANT STRATEGY: 15-MINUTE TRIPLE SUPERTREND OPTION BUYING STRATEGY (2020-2026 FULL 6-YEAR)
========================================================================================
Architecture & Strategy Specification:
1. Account & Sizing:
   - Starting Capital: Rs. 2,00,000.
   - Sizing: 1 Lot (1 x get_nifty_lot_size(date)).
   - Max 2 trades per day (1 active position at a time).

2. 3 Supertrends (Computed on NSE-aligned 15-min Spot Bars):
   - Fast ST1:   Period=7,  Multiplier=1.5
   - Medium ST2: Period=10, Multiplier=2.0 (Used for entry signal & exit flip)
   - Slow ST3:   Period=14, Multiplier=3.0

3. Entry Rules (Evaluated at 15-min bar close, executed on the very next 1-minute bar):
   - Time Window: Bar close between 09:30:00 and 14:30:00 IST (09:15-09:29 strictly skipped; no entries after 14:30).
   - Max Candle Size Filter: Signal candle range (High - Low) must be strictly < 45 points on Spot.
   - Bullish Signal: All 3 STs GREEN and Signal Candle GREEN (Close > Open) -> BUY 1 Lot ATM CE (nearest weekly expiry > today).
   - Bearish Signal: All 3 STs RED and Signal Candle RED (Close < Open) -> BUY 1 Lot ATM PE (nearest weekly expiry > today).
   - ATM Strike: round(Spot_close / 50) * 50.

4. Smart Hybrid Exit Rules (Tracked minute-by-minute on 1-minute option prices):
   - Rule A: ST2 (10, 2.0) flips color against the position on a 15-min bar close.
   - Rule B: Option hits a 20% Hard Stop-Loss (with Breakeven Trailing: once option reaches +25% profit, SL moves to entry price).
   - Rule C: Intraday forced square-off at 15:15:00 IST.

5. Costs:
   - Brokerage: Rs. 0 (Kotak Neo Zero-Brokerage Plan).
   - Slippage: 0.20% on traded option turnover (entry + exit).

6. Outputs:
   - results/triple_st_15m_6yr_trades.csv
   - Terminal: Full Executive Quant Scorecard + Year-by-Year Breakdown (2020 to 2026).
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


def run_6yr_15m_option_buying():
    t_start = time.time()
    print("=" * 115)
    print("  15-MINUTE TRIPLE SUPERTREND OPTION BUYING STRATEGY: FULL 6-YEAR BACKTEST (2020-2026)  ")
    print("=" * 115)

    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    opt_path = Path("data/nifty_options_master_2020_2026_1min.parquet")
    res_dir = Path("results")
    res_dir.mkdir(parents=True, exist_ok=True)
    out_trades_csv = res_dir / "triple_st_15m_6yr_trades.csv"

    if not spot_path.exists() or not opt_path.exists():
        print("Error: Master datasets not found.")
        sys.exit(1)

    # 1. Resample Spot to 15-minute bars and compute 3 Supertrends
    print("-> Loading Spot Data across full 2020-2026 horizon...")
    df_spot = pd.read_parquet(spot_path)
    print(f"   Spot data loaded: {len(df_spot):,} rows ({df_spot.index.min()} to {df_spot.index.max()})")

    print("-> Resampling Spot to NSE-aligned 15-Minute bars and computing ST1, ST2, ST3...")
    df_15m = df_spot.resample('15min', origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_15m['st1'] = calculate_supertrend(df_15m, 7, 1.5)
    df_15m['st2'] = calculate_supertrend(df_15m, 10, 2.0)
    df_15m['st3'] = calculate_supertrend(df_15m, 14, 3.0)
    df_15m['range'] = df_15m['high'] - df_15m['low']
    df_15m['is_green'] = df_15m['close'] > df_15m['open']
    df_15m['is_red'] = df_15m['close'] < df_15m['open']
    df_15m['close_time'] = df_15m.index + pd.Timedelta(minutes=15)

    # Fast lookup by bar close timestamp
    bar_by_close = {row['close_time']: row for _, row in df_15m.iterrows()}
    print(f"   Generated {len(df_15m):,} 15-min bars with 3 Supertrends precomputed.\n")

    # 2. Iterate Year-by-Year through 1-minute Options Master
    initial_cap = 200000.0
    running_cap = initial_cap
    trades = []

    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    print("-> Processing 1-minute Options Master Year-by-Year (2020 to 2026)...")

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

        days_opt = {d: grp for d, grp in df_opt_yr.groupby('date')}
        yr_dates = sorted(days_opt.keys())

        yr_trade_count = 0

        for dt in yr_dates:
            day_opt = days_opt[dt]
            day_bars = df_15m[df_15m.index.date == dt]
            if day_bars.empty:
                continue

            daily_trades_count = 0
            last_exit_ts = None

            for _, bar in day_bars.iterrows():
                if daily_trades_count >= 2:
                    break

                bar_close_ts = bar['close_time']
                bar_close_time = bar_close_ts.time()

                # If there was a previous trade today, only enter after that trade exited!
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

                entry_p = float(leg_data.loc[exec_time, 'close'])
                if entry_p <= 0:
                    continue

                lot_size = get_nifty_lot_size(dt)
                qty = 1 * lot_size
                daily_trades_count += 1
                yr_trade_count += 1

                # Continuous minute-by-minute tracking
                all_minutes = sorted([t for t in leg_data.index if exec_time <= t <= pd.Timestamp('15:15:00').time()])

                sl_p = 0.80 * entry_p
                be_activated = False
                max_p = entry_p

                exit_time = pd.Timestamp('15:15:00').time()
                exit_p = entry_p
                exit_reason = 'TIME_EXIT (15:15)'

                for m_time in all_minutes:
                    curr_p = float(leg_data.loc[m_time, 'close'])
                    max_p = max(max_p, curr_p)

                    # Breakeven Trailing (+25% profit)
                    if curr_p >= 1.25 * entry_p and not be_activated:
                        be_activated = True
                        sl_p = max(sl_p, entry_p)

                    # Stop-Loss Check
                    if curr_p <= sl_p:
                        exit_time = m_time
                        exit_p = curr_p
                        exit_reason = 'BREAKEVEN_SL (0%)' if be_activated else 'HARD_SL (-20%)'
                        break

                    # ST2 Flip on 15-minute bar close
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

                    # 15:15:00 Forced Square-off
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
                    'Year': dt.year,
                    'Date': str(dt),
                    'Day': pd.Timestamp(dt).strftime('%A'),
                    'Signal_Bar_Close': str(bar_close_time),
                    'Spot_Signal': round(spot_close, 2),
                    'Entry_Time': str(exec_time),
                    'ATM_Strike': int(atm_strike),
                    'Option_Type': opt_type,
                    'Expiry': str(sel_exp),
                    'DTE': dte,
                    'Lot_Size': lot_size,
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

        t_yr1 = time.time()
        print(f"   Processed Year {yr}: {len(yr_dates)} trading days, {yr_trade_count} trades in {t_yr1-t_yr0:.2f}s")

    df_trades = pd.DataFrame(trades)
    df_trades.to_csv(out_trades_csv, index=False)
    print(f"\n-> All 6 Years of Trades saved to: {out_trades_csv}\n")

    # Compute Overall Quant Metrics
    total_trades = len(df_trades)
    wins = df_trades[df_trades['Net_PnL_Rs'] > 0]
    losses = df_trades[df_trades['Net_PnL_Rs'] < 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0

    gross_pnl = df_trades['Gross_PnL_Rs'].sum()
    total_costs = df_trades['Costs_Rs'].sum()
    net_pnl = df_trades['Net_PnL_Rs'].sum()
    pct_return = (net_pnl / initial_cap) * 100

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

    # 1. Print Executive Scorecard
    print("=" * 75)
    print("       15-MINUTE TRIPLE SUPERTREND EXECUTIVE SCORECARD (2020-2026)       ")
    print("=" * 75)
    print(f" Starting Capital              : Rs. {initial_cap:,.2f}")
    print(f" Final Capital                 : Rs. {running_cap:,.2f}")
    print(f" Net Realized Profit/Loss      : Rs. {net_pnl:,.2f} ({pct_return:+.2f}%)")
    print(f" Gross Option Alpha            : Rs. {gross_pnl:,.2f}")
    print(f" Total Slippage & Costs (0.20%): Rs. {total_costs:,.2f}")
    print("-" * 75)
    print(f" Total Trades Taken            : {total_trades} Trades")
    print(f" Winning Trades                : {win_count} Trades")
    print(f" Losing Trades                 : {loss_count} Trades")
    print(f" Overall Win Rate              : {win_rate:.1f}%")
    print(f" Profit Factor                 : {pf:.2f}")
    print("-" * 75)
    print(f" Hard Stop-Loss (-20%) Hits    : {hard_sl} ({hard_sl/total_trades*100:.1f}%)")
    print(f" Breakeven Trailing SL Hits    : {be_sl} ({be_sl/total_trades*100:.1f}%)")
    print(f" ST2 Color Flip Exits          : {st2_flip} ({st2_flip/total_trades*100:.1f}%)")
    print(f" Time Exits (15:15:00)         : {time_exit} ({time_exit/total_trades*100:.1f}%)")
    print("-" * 75)
    print(f" Average Winning Trade         : +Rs. {avg_win:,.2f}")
    print(f" Average Losing Trade          : -Rs. {abs(avg_loss):,.2f}")
    print(f" Win / Loss Expectancy Ratio   : {abs(avg_win / avg_loss):.2f}x" if avg_loss != 0 else "N/A")
    print(f" Peak-to-Trough Max Drawdown   : Rs. {max_dd_rs:,.2f} ({max_dd_pct:.2f}%)")
    print(f" Return-to-Drawdown Ratio      : {net_pnl / max_dd_rs:.2f}" if max_dd_rs > 0 else "N/A")
    print("=" * 75)

    # 2. Print Year-by-Year Breakdown Table
    print("\n" + "=" * 115)
    print("                   YEAR-BY-YEAR PnL BREAKDOWN (2020 TO 2026)                   ")
    print("=" * 115)
    print(
        f"{'Year':<6} {'Trades':<8} {'Wins':<6} {'Losses':<8} {'Win Rate':<10} "
        f"{'Gross PnL (Rs)':<16} {'Costs (Rs)':<12} {'Net PnL (Rs)':<16} {'Ending Capital (Rs)':<18} {'Return':<8}"
    )
    print("-" * 115)

    track_cap = initial_cap
    for yr, grp in df_trades.groupby('Year'):
        yr_t = len(grp)
        yr_w = (grp['Net_PnL_Rs'] > 0).sum()
        yr_l = (grp['Net_PnL_Rs'] < 0).sum()
        yr_wr = (yr_w / yr_t * 100) if yr_t > 0 else 0.0
        yr_gross = grp['Gross_PnL_Rs'].sum()
        yr_costs = grp['Costs_Rs'].sum()
        yr_net = grp['Net_PnL_Rs'].sum()
        yr_ret = (yr_net / track_cap) * 100
        track_cap += yr_net

        gross_str = f"+Rs.{yr_gross:,.2f}" if yr_gross > 0 else f"-Rs.{abs(yr_gross):,.2f}"
        net_str = f"+Rs.{yr_net:,.2f}" if yr_net > 0 else f"-Rs.{abs(yr_net):,.2f}"

        print(
            f"{yr:<6} {yr_t:<8} {yr_w:<6} {yr_l:<8} {yr_wr:<9.1f}% "
            f"{gross_str:<16} Rs.{yr_costs:<10,.2f} {net_str:<16} Rs.{track_cap:<16,.2f} {yr_ret:>+6.1f}%"
        )
    print("-" * 115)
    total_ret = (net_pnl / initial_cap) * 100
    print(
        f"{'TOTAL':<6} {total_trades:<8} {win_count:<6} {loss_count:<8} {win_rate:<9.1f}% "
        f"{'+Rs.'+f'{gross_pnl:,.2f}':<16} Rs.{total_costs:<10,.2f} {'+Rs.'+f'{net_pnl:,.2f}':<16} Rs.{running_cap:<16,.2f} {total_ret:>+6.1f}%"
    )
    print("=" * 115)
    print(f"\nExecution completed in {time.time()-t_start:.1f} seconds.\n")


if __name__ == "__main__":
    run_6yr_15m_option_buying()
