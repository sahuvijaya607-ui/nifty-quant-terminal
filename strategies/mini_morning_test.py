import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow.dataset as ds

def run_mini_morning_backtest():
    spot_path = Path("data/nifty_1min_5yr.parquet")
    options_path = Path("data/nifty_options_master_2020_2026_1min.parquet")
    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)
    out_csv = results_dir / "mini_jan2025_trades.csv"

    if not spot_path.exists():
        print(f"Error: {spot_path} not found.")
        sys.exit(1)
    if not options_path.exists():
        print(f"Error: {options_path} not found.")
        sys.exit(1)

    print("================================================================================")
    print("      MINI MORNING BREAKOUT SANITY TEST: JANUARY 2025 (FAST VERIFICATION)       ")
    print("================================================================================")
    print("Strategy Parameters:")
    print("  • Lookback Decision Window: 09:15:00 (Open) to 09:29:00 (Close)")
    print("  • Trade Entry:              09:30:00 (ATM Nearest Weekly Expiry)")
    print("  • Position Sizing:          1 Lot = 75 Qty")
    print("  • Target / Stop Loss:       +30% / -20%")
    print("  • Max Holding Time:         11:30:00 IST (Forced Exit)")
    print("  * Transaction Cost:         Rs. 40 flat brokerage per round-trip trade")
    print("================================================================================\n")

    t0 = time.time()

    # 1. Load Spot Data for January 2025
    print("Loading January 2025 NIFTY Spot data...")
    df_spot = pd.read_parquet(spot_path)
    df_spot_jan = df_spot.loc['2025-01-01':'2025-01-31'].copy()
    trading_days = sorted(df_spot_jan.index.normalize().unique())
    print(f"Loaded {len(df_spot_jan):,} spot candles across {len(trading_days)} trading days.")

    # 2. Load Options Data for January 2025 with PyArrow filter (<0.5 sec)
    print("Loading January 2025 NIFTY Options data via PyArrow predicate pushdown...")
    dataset = ds.dataset(options_path, format='parquet')
    filter_expr = (
        (ds.field('timestamp') >= pd.to_datetime('2025-01-01 00:00:00')) & 
        (ds.field('timestamp') <= pd.to_datetime('2025-01-31 23:59:59'))
    )
    table_jan = dataset.to_table(filter=filter_expr)
    df_opt_jan = table_jan.to_pandas()
    print(f"Loaded {len(df_opt_jan):,} options candles in {time.time()-t0:.2f} seconds.\n")

    # Pre-index options by date for instant lookup
    df_opt_jan['date'] = df_opt_jan['timestamp'].dt.date

    trades = []
    lot_size = 75
    brokerage_per_trade = 40.0

    print("Simulating trades day-by-day (09:15 to 11:30)...")

    for current_day in trading_days:
        day_str = current_day.strftime('%Y-%m-%d')
        date_obj = current_day.date()

        # Get spot data for this day
        spot_day = df_spot_jan.loc[day_str]
        if spot_day.empty:
            continue

        ts_0915 = pd.to_datetime(f"{day_str} 09:15:00")
        ts_0929 = pd.to_datetime(f"{day_str} 09:29:00")
        ts_0930 = pd.to_datetime(f"{day_str} 09:30:00")
        ts_1130 = pd.to_datetime(f"{day_str} 11:30:00")

        if ts_0915 not in spot_day.index or ts_0929 not in spot_day.index or ts_0930 not in spot_day.index:
            continue

        spot_open_0915 = spot_day.loc[ts_0915, 'open']
        spot_close_0929 = spot_day.loc[ts_0929, 'close']
        spot_close_0930 = spot_day.loc[ts_0930, 'close']

        spot_delta = spot_close_0929 - spot_open_0915

        # Decision Rule
        if spot_delta > 0:
            signal = "BUY_CALL"
            opt_type = "CE"
        elif spot_delta < 0:
            signal = "BUY_PUT"
            opt_type = "PE"
        else:
            # Flat
            continue

        # ATM Strike Selection
        atm_strike = round(spot_close_0930 / 50.0) * 50.0

        # Nearest Expiry Selection
        opt_day = df_opt_jan[df_opt_jan['date'] == date_obj]
        available_expiries = sorted(opt_day[opt_day['expiry'] >= ts_0930]['expiry'].unique())
        if not available_expiries:
            continue
        nearest_expiry = available_expiries[0]

        # Extract contract candle series
        contract = opt_day[
            (opt_day['expiry'] == nearest_expiry) & 
            (opt_day['strike_price'] == atm_strike) & 
            (opt_day['option_type'] == opt_type)
        ].sort_values('timestamp')

        if contract.empty:
            continue

        # Entry at 09:30:00 close
        entry_row = contract[contract['timestamp'] == ts_0930]
        if entry_row.empty:
            # Fallback to closest bar within 2 minutes
            entry_row = contract[(contract['timestamp'] >= ts_0930) & (contract['timestamp'] <= ts_0930 + pd.Timedelta(minutes=2))].head(1)
            if entry_row.empty:
                continue

        entry_time = entry_row['timestamp'].iloc[0]
        entry_price = float(entry_row['close'].iloc[0])

        if entry_price <= 0:
            continue

        # Exit boundaries
        target_price = round(entry_price * 1.30, 2)
        sl_price = round(entry_price * 0.80, 2)

        # Track minute-by-minute between entry_time and 11:30:00
        tracking_series = contract[(contract['timestamp'] > entry_time) & (contract['timestamp'] <= ts_1130)]

        exit_time = None
        exit_price = None
        exit_reason = None

        for _, bar in tracking_series.iterrows():
            t = bar['timestamp']
            h = float(bar['high'])
            l = float(bar['low'])
            c = float(bar['close'])

            # 1. Stop Loss (-20%)
            if l <= sl_price or c <= sl_price:
                exit_time = t
                exit_price = min(sl_price, c)
                exit_reason = "STOP_LOSS (-20%)"
                break

            # 2. Target (+30%)
            elif h >= target_price or c >= target_price:
                exit_time = t
                exit_price = max(target_price, c)
                exit_reason = "TARGET (+30%)"
                break

            # 3. Time Exit at 11:30:00
            if t >= ts_1130:
                exit_time = t
                exit_price = c
                exit_reason = "TIME_EXIT (11:30)"
                break

        # If tracking series ended before 11:30 without hitting SL/Target
        if exit_time is None:
            if not tracking_series.empty:
                last_bar = tracking_series.iloc[-1]
                exit_time = last_bar['timestamp']
                exit_price = float(last_bar['close'])
                exit_reason = "TIME_EXIT (Early)"
            else:
                exit_time = entry_time
                exit_price = entry_price
                exit_reason = "NO_DATA"

        # Financial Calculations
        pts_change = round(exit_price - entry_price, 2)
        return_pct = round((pts_change / entry_price) * 100, 2)
        gross_pnl = round(pts_change * lot_size, 2)
        net_pnl = round(gross_pnl - brokerage_per_trade, 2)

        trades.append({
            'date': day_str,
            'spot_open_0915': round(spot_open_0915, 2),
            'spot_close_0929': round(spot_close_0929, 2),
            'spot_delta': round(spot_delta, 2),
            'signal': signal,
            'expiry': nearest_expiry.strftime('%Y-%m-%d'),
            'strike': int(atm_strike),
            'type': opt_type,
            'entry_time': entry_time.strftime('%H:%M:%S'),
            'entry_price': round(entry_price, 2),
            'exit_time': exit_time.strftime('%H:%M:%S'),
            'exit_price': round(exit_price, 2),
            'exit_reason': exit_reason,
            'pts_change': pts_change,
            'return_pct': return_pct,
            'gross_pnl': gross_pnl,
            'brokerage': brokerage_per_trade,
            'net_pnl': net_pnl
        })

    df_trades = pd.DataFrame(trades)
    df_trades.to_csv(out_csv, index=False)

    # --------------------------------------------------------------------------
    # PRINT COMPLETE TRADE TABLE
    # --------------------------------------------------------------------------
    print("\n=======================================================================================================================")
    print("                                      JANUARY 2025 TRADE-BY-TRADE AUDIT LOG                                            ")
    print("=======================================================================================================================")
    
    header_fmt = "{:<10} | {:<7} | {:<6} | {:<2} | {:<10} | {:<8} | {:>7} | {:<8} | {:>7} | {:<16} | {:>7} | {:>9}"
    row_fmt    = "{:<10} | {:<7} | {:<6} | {:<2} | {:<10} | {:<8} | {:>7.2f} | {:<8} | {:>7.2f} | {:<16} | {:>7.2f} | {:>9.2f}"
    
    print(header_fmt.format(
        "Date", "Signal", "Strike", "CP", "Expiry", "EntryAt", "EntryPx", "ExitAt", "ExitPx", "ExitReason", "Pts", "NetPnL(Rs)"
    ))
    print("-" * 119)

    for _, r in df_trades.iterrows():
        pnl_str = f"{r['net_pnl']:+,.2f}"
        print(row_fmt.format(
            r['date'], r['signal'][:7], r['strike'], r['type'], r['expiry'],
            r['entry_time'], r['entry_price'], r['exit_time'], r['exit_price'],
            r['exit_reason'], r['pts_change'], r['net_pnl']
        ))

    print("=" * 119)

    # --------------------------------------------------------------------------
    # SUMMARY PERFORMANCE STATISTICS
    # --------------------------------------------------------------------------
    total_trades = len(df_trades)
    wins = (df_trades['net_pnl'] > 0).sum()
    losses = (df_trades['net_pnl'] <= 0).sum()
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    total_gross = df_trades['gross_pnl'].sum()
    total_comm = df_trades['brokerage'].sum()
    total_net = df_trades['net_pnl'].sum()
    
    # Target / SL breakdown
    target_hits = (df_trades['exit_reason'].str.contains('TARGET')).sum()
    sl_hits = (df_trades['exit_reason'].str.contains('STOP_LOSS')).sum()
    time_exits = (df_trades['exit_reason'].str.contains('TIME_EXIT')).sum()

    # Drawdown calculation
    df_trades['cum_pnl'] = df_trades['net_pnl'].cumsum()
    df_trades['peak'] = df_trades['cum_pnl'].cummax()
    df_trades['drawdown'] = df_trades['cum_pnl'] - df_trades['peak']
    max_dd = df_trades['drawdown'].min()

    # Profit Factor
    total_win_amt = df_trades[df_trades['gross_pnl'] > 0]['gross_pnl'].sum()
    total_loss_amt = abs(df_trades[df_trades['gross_pnl'] < 0]['gross_pnl'].sum())
    profit_factor = (total_win_amt / total_loss_amt) if total_loss_amt > 0 else float('inf')

    print("\n================================================================================")
    print("                        JANUARY 2025 STRATEGY SCORECARD                        ")
    print("================================================================================")
    print(f"Total Trading Days:         {len(trading_days)}")
    print(f"Total Trades Taken:         {total_trades}")
    print(f"Winning Trades:             {wins} ({win_rate:.1f}%)")
    print(f"Losing Trades:              {losses} ({100-win_rate:.1f}%)")
    print(f"Target (+30%) Hits:         {target_hits}")
    print(f"Stop-Loss (-20%) Hits:      {sl_hits}")
    print(f"Time Exits (11:30):         {time_exits}")
    print("--------------------------------------------------------------------------------")
    print(f"Total Gross PnL:            Rs. {total_gross:+,.2f}")
    print(f"Total Brokerage (Rs.40):    Rs. {total_comm:,.2f}")
    print(f"Total Net PnL:              Rs. {total_net:+,.2f}")
    print(f"Profit Factor:              {profit_factor:.2f}")
    print(f"Max Drawdown:               Rs. {max_dd:,.2f}")
    print(f"Trade Log Saved To:         {out_csv.resolve()}")
    print("================================================================================")
    print(f"Execution completed in {time.time()-t0:.2f} seconds.")

if __name__ == "__main__":
    run_mini_morning_backtest()
