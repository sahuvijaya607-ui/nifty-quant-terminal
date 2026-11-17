"""
========================================================================================
QUANT STRATEGY: INTRADAY SUPERTREND DIRECTIONAL CREDIT SPREAD (JANUARY 2025 TEST)
========================================================================================
Architecture & Strategy Specification:
1. Account & Sizing:
   - Initial Capital: Rs. 2,00,000.
   - Sizing: 1 Lot (1 x get_nifty_lot_size(date)). In Jan 2025, lot size = 75 qty.
   - Max 1 trade per day.

2. Daily Signal Generation (09:20:00 Signal based on Daily Spot OHLC up to yesterday):
   - Supertrend (Period=7, Multiplier=3) calculated on 2024-2025 daily bars for 100% warmup.
   - Bullish Entry: Yesterday's Supertrend is GREEN and Yesterday's Candle is GREEN (Close > Open)
     -> Enter BULL PUT CREDIT SPREAD (SELL 35-Delta PE, BUY 15-Delta PE).
   - Bearish Entry: Yesterday's Supertrend is RED and Yesterday's Candle is RED (Close < Open)
     -> Enter BEAR CALL CREDIT SPREAD (SELL 35-Delta CE, BUY 15-Delta CE).
   - Filter (No-Trade): If Supertrend and Candle colors mismatch, SKIP trading that day.

3. Execution & Smart Expiry:
   - Executed at 09:21:00 candle prices.
   - Smart Expiry: Nearest weekly expiry strictly after today (expiry.date() > trade_date).

4. Target & Double-SL Rule:
   - Target = +40% of Net Credit received.
   - Stop-Loss = -80% of Net Credit received (2x Target).
   - Time Exit: Forced square-off at 15:15:00 IST.

5. Costs:
   - Kotak Neo Zero-Brokerage: Rs. 0 Brokerage.
   - Realistic Slippage & Turnover Charges: 0.15% on traded turnover.

6. Outputs:
   - Full Day-by-Day ledger saved to results/mini_st_cs_jan2025.csv.
   - Comprehensive Day-by-Day terminal table + Quant Scorecard.
========================================================================================
"""

import os
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow.parquet as pq

# Add project root to sys.path
sys.path.insert(0, os.path.abspath("."))
from utils.nifty_lot_size import get_nifty_lot_size


def calculate_supertrend(df_daily: pd.DataFrame, period: int = 7, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Computes standard Supertrend (Period=7, Multiplier=3) with ATR using Wilder's RMA.
    """
    df = df_daily.copy()
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
    st = pd.Series(index=df.index, dtype=float)
    trend = pd.Series(index=df.index, dtype=int) # 1 = Green/Bullish, -1 = Red/Bearish

    for i in range(period, len(df)):
        prev_close = df['close'].iloc[i-1]
        
        # Upper band adjustment
        if basic_upper.iloc[i] < final_upper.iloc[i-1] or prev_close > final_upper.iloc[i-1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]
            
        # Lower band adjustment
        if basic_lower.iloc[i] > final_lower.iloc[i-1] or prev_close < final_lower.iloc[i-1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]
            
        prev_trend = trend.iloc[i-1] if not np.isnan(trend.iloc[i-1]) else 1
        curr_close = df['close'].iloc[i]
        
        if prev_trend == 1:
            if curr_close < final_lower.iloc[i]:
                trend.iloc[i] = -1
                st.iloc[i] = final_upper.iloc[i]
            else:
                trend.iloc[i] = 1
                st.iloc[i] = final_lower.iloc[i]
        else:
            if curr_close > final_upper.iloc[i]:
                trend.iloc[i] = 1
                st.iloc[i] = final_lower.iloc[i]
            else:
                trend.iloc[i] = -1
                st.iloc[i] = final_upper.iloc[i]

    df['supertrend'] = st
    df['trend'] = trend
    df['candle_color'] = np.where(df['close'] > df['open'], 'GREEN', np.where(df['close'] < df['open'], 'RED', 'DOJI'))
    return df


def run_january_2025_sanity_check():
    print("=" * 115)
    print("   INTRADAY SUPERTREND DIRECTIONAL CREDIT SPREAD (JANUARY 2025 SANITY CHECK)   ")
    print("=" * 115)

    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    opt_path = Path("data/nifty_option_chain_1min_clean.parquet")
    res_dir = Path("results")
    res_dir.mkdir(parents=True, exist_ok=True)
    out_csv = res_dir / "mini_st_cs_jan2025.csv"

    if not spot_path.exists():
        print(f"Error: {spot_path} not found.")
        sys.exit(1)
    if not opt_path.exists():
        print(f"Error: {opt_path} not found.")
        sys.exit(1)

    print(f"-> Loading Spot Master for Supertrend warmup: {spot_path}...")
    df_spot = pd.read_parquet(spot_path)
    df_daily = df_spot.resample('1D').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_daily = df_daily[df_daily.index >= '2024-01-01'].copy()
    df_daily = calculate_supertrend(df_daily, period=7, multiplier=3)

    print(f"-> Loading January 2025 1-minute options chain: {opt_path}...")
    table = pq.read_table(opt_path, filters=[
        ('timestamp', '>=', pd.Timestamp('2025-01-01 09:00:00')),
        ('timestamp', '<=', pd.Timestamp('2025-01-31 16:00:00'))
    ], columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'delta'])
    df_opt = table.to_pandas()
    df_opt['date'] = df_opt['timestamp'].dt.date
    df_opt['time'] = df_opt['timestamp'].dt.time
    df_opt['expiry_date'] = df_opt['expiry'].dt.date

    # Group by date for ultra-fast slicing
    days_data = {d: grp for d, grp in df_opt.groupby('date')}
    jan_dates = sorted(df_opt['date'].unique())

    print(f"-> Successfully loaded {len(df_opt):,} option bars across {len(jan_dates)} trading sessions in Jan 2025.\n")

    initial_capital = 200000.0
    running_capital = initial_capital
    records = []

    for trade_date in jan_dates:
        date_ts = pd.Timestamp(trade_date)
        day_name = date_ts.strftime('%A')

        # Spot at 09:21:00
        spot_0921_ts = pd.Timestamp(f"{trade_date} 09:21:00")
        entry_spot = df_spot.loc[spot_0921_ts, 'close'] if spot_0921_ts in df_spot.index else np.nan

        # Signal from yesterday's daily candle and Supertrend
        idx = df_daily.index.get_loc(date_ts)
        yest = df_daily.iloc[idx - 1]
        yest_st = 'GREEN' if yest['trend'] == 1 else 'RED'
        yest_candle = yest['candle_color']

        if yest_st == 'GREEN' and yest_candle == 'GREEN':
            action = 'BULL_PUT_SPREAD'
            target_opt_type = 'PE'
            target_sell_delta = -0.35
            target_buy_delta = -0.15
        elif yest_st == 'RED' and yest_candle == 'RED':
            action = 'BEAR_CALL_SPREAD'
            target_opt_type = 'CE'
            target_sell_delta = 0.35
            target_buy_delta = 0.15
        else:
            action = 'SKIPPED'
            skip_reason = f"Mismatch (ST={yest_st}, Candle={yest_candle})"
            records.append({
                'Date': str(trade_date),
                'Day_Name': day_name,
                'Supertrend': yest_st,
                'Candle': yest_candle,
                'Signal': action,
                'NIFTY_Entry_Spot': round(entry_spot, 2) if not np.isnan(entry_spot) else "-",
                'Expiry': '-',
                'DTE': '-',
                'Short_Leg': '-',
                'Long_Leg': '-',
                'Net_Credit_Pts': 0.0,
                'Exit_Time': '-',
                'NIFTY_Exit_Spot': "-",
                'Exit_Reason': f"SKIPPED ({skip_reason})",
                'Points_PnL': 0.0,
                'Gross_PnL_Rs': 0.0,
                'Costs_Rs': 0.0,
                'Net_PnL_Rs': 0.0,
                'Running_Capital': round(running_capital, 2)
            })
            continue

        # Traded day execution
        day_opt = days_data[trade_date]
        entry_opt = day_opt[day_opt['time'] == pd.Timestamp('09:21:00').time()]

        # Smart Expiry Selection: nearest weekly strictly after today
        valid_expiries = sorted([exp for exp in entry_opt['expiry_date'].unique() if exp > trade_date])
        if not valid_expiries:
            print(f"Warning: No valid expiry > {trade_date}")
            continue
        selected_expiry = valid_expiries[0]
        dte = (selected_expiry - trade_date).days

        # Filter option chain for selected expiry and target option type
        chain = entry_opt[(entry_opt['expiry_date'] == selected_expiry) & 
                          (entry_opt['option_type'] == target_opt_type) & 
                          (entry_opt['delta'].notna()) & 
                          (entry_opt['ltp'] > 0)]

        if chain.empty:
            print(f"Warning: Empty chain on {trade_date}")
            continue

        # Short leg: Delta closest to target_sell_delta
        short_row = chain.loc[(chain['delta'] - target_sell_delta).abs().idxmin()]
        short_strike = short_row['strike_price']
        short_delta = short_row['delta']
        short_entry_price = short_row['ltp']

        # Long leg: Delta closest to target_buy_delta
        long_row = chain.loc[(chain['delta'] - target_buy_delta).abs().idxmin()]
        long_strike = long_row['strike_price']
        long_delta = long_row['delta']
        long_entry_price = long_row['ltp']

        net_credit = short_entry_price - long_entry_price
        lot_size = get_nifty_lot_size(trade_date)
        qty = 1 * lot_size

        target_pts = 0.40 * net_credit
        sl_pts = -0.80 * net_credit

        # Minute-by-minute tracking of both legs
        leg_short = day_opt[(day_opt['expiry_date'] == selected_expiry) & 
                            (day_opt['option_type'] == target_opt_type) & 
                            (day_opt['strike_price'] == short_strike)].set_index('time')
        leg_long = day_opt[(day_opt['expiry_date'] == selected_expiry) & 
                           (day_opt['option_type'] == target_opt_type) & 
                           (day_opt['strike_price'] == long_strike)].set_index('time')

        common_times = sorted([
            t for t in leg_short.index 
            if t in leg_long.index and pd.Timestamp('09:22:00').time() <= t <= pd.Timestamp('15:15:00').time()
        ])

        exit_time = pd.Timestamp('15:15:00').time()
        exit_reason = 'TIME_EXIT (15:15)'
        short_exit_price = short_entry_price
        long_exit_price = long_entry_price
        pnl_pts = 0.0

        for t in common_times:
            curr_short_p = leg_short.loc[t, 'ltp']
            curr_long_p = leg_long.loc[t, 'ltp']
            curr_spread_val = curr_short_p - curr_long_p
            curr_pnl_pts = net_credit - curr_spread_val

            # Target check (+40%)
            if curr_pnl_pts >= target_pts:
                exit_time = t
                exit_reason = 'TARGET_HIT (+40%)'
                short_exit_price = curr_short_p
                long_exit_price = curr_long_p
                pnl_pts = curr_pnl_pts
                break

            # Stop-loss check (-80%)
            if curr_pnl_pts <= sl_pts:
                exit_time = t
                exit_reason = 'STOP_LOSS (-80%)'
                short_exit_price = curr_short_p
                long_exit_price = curr_long_p
                pnl_pts = curr_pnl_pts
                break

            if t == pd.Timestamp('15:15:00').time():
                exit_time = t
                exit_reason = 'TIME_EXIT (15:15)'
                short_exit_price = curr_short_p
                long_exit_price = curr_long_p
                pnl_pts = curr_pnl_pts
                break

        # Spot at Exit Time
        exit_spot_ts = pd.Timestamp(f"{trade_date} {exit_time}")
        exit_spot = df_spot.loc[exit_spot_ts, 'close'] if exit_spot_ts in df_spot.index else np.nan

        # Costs: ₹0 Brokerage + 0.15% Slippage on traded turnover
        entry_turnover = (short_entry_price + long_entry_price) * qty
        exit_turnover = (short_exit_price + long_exit_price) * qty
        slippage_cost = 0.0015 * (entry_turnover + exit_turnover)

        gross_pnl_rs = pnl_pts * qty
        net_pnl_rs = gross_pnl_rs - slippage_cost
        running_capital += net_pnl_rs

        records.append({
            'Date': str(trade_date),
            'Day_Name': day_name,
            'Supertrend': yest_st,
            'Candle': yest_candle,
            'Signal': action,
            'NIFTY_Entry_Spot': round(entry_spot, 2) if not np.isnan(entry_spot) else "-",
            'Expiry': str(selected_expiry),
            'DTE': dte,
            'Short_Leg': f"{short_strike:.0f} {target_opt_type} (d={short_delta:.2f})",
            'Long_Leg': f"{long_strike:.0f} {target_opt_type} (d={long_delta:.2f})",
            'Net_Credit_Pts': round(net_credit, 2),
            'Exit_Time': str(exit_time),
            'NIFTY_Exit_Spot': round(exit_spot, 2) if not np.isnan(exit_spot) else "-",
            'Exit_Reason': exit_reason,
            'Points_PnL': round(pnl_pts, 2),
            'Gross_PnL_Rs': round(gross_pnl_rs, 2),
            'Costs_Rs': round(slippage_cost, 2),
            'Net_PnL_Rs': round(net_pnl_rs, 2),
            'Running_Capital': round(running_capital, 2)
        })

    df_ledger = pd.DataFrame(records)
    df_ledger.to_csv(out_csv, index=False)
    print(f"-> Day-by-Day Trade Ledger saved to: {out_csv}\n")

    # Print Table
    print("=" * 145)
    print("                     DAY-BY-DAY TRADE LEDGER (JANUARY 2025)                     ")
    print("=" * 145)
    header = (
        f"{'Date':<10} {'Day':<9} {'Signal':<17} {'Spot(09:21)':<11} {'Short Leg':<18} {'Long Leg':<18} "
        f"{'Credit':<7} {'ExitTime':<8} {'ExitSpot':<10} {'Exit Reason':<20} {'Net PnL(Rs)':<12} {'Capital(Rs)':<11}"
    )
    print(header)
    print("-" * 145)

    for _, r in df_ledger.iterrows():
        pnl_str = f"+Rs.{r['Net_PnL_Rs']:,.2f}" if r['Net_PnL_Rs'] > 0 else (f"-Rs.{abs(r['Net_PnL_Rs']):,.2f}" if r['Net_PnL_Rs'] < 0 else "Rs.0.00")
        cap_str = f"Rs.{r['Running_Capital']:,.2f}"
        spot_in = f"{r['NIFTY_Entry_Spot']}" if r['NIFTY_Entry_Spot'] != '-' else "-"
        spot_out = f"{r['NIFTY_Exit_Spot']}" if r['NIFTY_Exit_Spot'] != '-' else "-"
        credit_str = f"{r['Net_Credit_Pts']:.2f}" if r['Net_Credit_Pts'] > 0 else "-"

        print(
            f"{r['Date']:<10} {r['Day_Name'][:3]:<9} {r['Signal']:<17} {spot_in:<11} "
            f"{r['Short_Leg']:<18} {r['Long_Leg']:<18} {credit_str:<7} {r['Exit_Time']:<8} {spot_out:<10} "
            f"{r['Exit_Reason']:<20} {pnl_str:<12} {cap_str:<11}"
        )
    print("=" * 145)

    # Compute Quant Scorecard Metrics
    trades_only = df_ledger[df_ledger['Signal'] != 'SKIPPED']
    total_days = len(df_ledger)
    skipped_days = (df_ledger['Signal'] == 'SKIPPED').sum()
    total_trades = len(trades_only)
    wins = trades_only[trades_only['Net_PnL_Rs'] > 0]
    losses = trades_only[trades_only['Net_PnL_Rs'] < 0]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0

    target_hits = (trades_only['Exit_Reason'].str.contains('TARGET_HIT')).sum()
    sl_hits = (trades_only['Exit_Reason'].str.contains('STOP_LOSS')).sum()
    time_exits = (trades_only['Exit_Reason'].str.contains('TIME_EXIT')).sum()

    gross_profit = trades_only['Gross_PnL_Rs'].sum()
    total_costs = trades_only['Costs_Rs'].sum()
    net_profit = trades_only['Net_PnL_Rs'].sum()
    pct_return = (net_profit / initial_capital) * 100

    sum_wins = wins['Net_PnL_Rs'].sum()
    sum_losses = abs(losses['Net_PnL_Rs'].sum())
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else np.nan
    avg_win = wins['Net_PnL_Rs'].mean() if win_count > 0 else 0.0
    avg_loss = losses['Net_PnL_Rs'].mean() if loss_count > 0 else 0.0

    # Drawdown
    equity_curve = trades_only['Running_Capital']
    peak = equity_curve.cummax()
    dd_series = equity_curve - peak
    max_dd_rs = dd_series.min() if not dd_series.empty else 0.0
    max_dd_pct = (max_dd_rs / peak.loc[dd_series.idxmin()]) * 100 if not dd_series.empty and max_dd_rs < 0 else 0.0

    print("\n" + "=" * 65)
    print("          EXECUTIVE QUANT SCORECARD (JANUARY 2025)          ")
    print("=" * 65)
    print(f" Starting Capital              : Rs. {initial_capital:,.2f}")
    print(f" Final Capital                 : Rs. {running_capital:,.2f}")
    print(f" Net Realized Profit/Loss      : Rs. {net_profit:,.2f} ({pct_return:+.2f}%)")
    print(f" Total Calendar Trading Days   : {total_days} Days")
    print(f" Days Skipped by Trend Filter  : {skipped_days} Days ({skipped_days/total_days*100:.1f}%)")
    print(f" Days Traded                   : {total_trades} Days ({total_trades/total_days*100:.1f}%)")
    print("-" * 65)
    print(f" Winning Trades                : {win_count} Trades")
    print(f" Losing Trades                 : {loss_count} Trades")
    print(f" Win Rate                      : {win_rate:.1f}%")
    print(f" Profit Factor                 : {profit_factor:.2f}")
    print("-" * 65)
    print(f" Target Hits (+40% Credit)     : {target_hits} ({target_hits/total_trades*100:.1f}%)")
    print(f" Stop-Loss Hits (-80% Credit)  : {sl_hits} ({sl_hits/total_trades*100:.1f}%)")
    print(f" Time Exits (15:15 Square-off) : {time_exits} ({time_exits/total_trades*100:.1f}%)")
    print("-" * 65)
    print(f" Average Winning Trade         : +Rs. {avg_win:,.2f}")
    print(f" Average Losing Trade          : -Rs. {abs(avg_loss):,.2f}")
    print(f" Win / Loss Expectancy Ratio   : {abs(avg_win / avg_loss):.2f}x" if avg_loss != 0 else "N/A")
    print(f" Max Drawdown                  : Rs. {abs(max_dd_rs):,.2f} ({abs(max_dd_pct):.2f}%)")
    print(f" Total Slippage & Turnover Cost: Rs. {total_costs:,.2f}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run_january_2025_sanity_check()
