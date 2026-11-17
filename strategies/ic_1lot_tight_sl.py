import os
import sys
import argparse
import time
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import numpy as np
import pyarrow.dataset as ds

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.nifty_lot_size import get_nifty_lot_size

def run_tight_sl_iron_condor_backtest(start_date="2025-01-01", end_date="2025-01-31", initial_capital=200000.0):
    spot_path = Path("data/nifty_1min_5yr.parquet")
    options_path = Path("data/nifty_options_master_2020_2026_1min.parquet")
    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)
    
    daily_csv = results_dir / "ic_1lot_tight_sl_daily.csv"
    monthly_csv = results_dir / "ic_1lot_tight_sl_monthly.csv"

    if not spot_path.exists():
        print(f"Error: {spot_path} not found.")
        sys.exit(1)
    if not options_path.exists():
        print(f"Error: {options_path} not found.")
        sys.exit(1)

    print("================================================================================")
    print("      INTRADAY NIFTY IRON CONDOR (TIGHT STOP-LOSS: 40% CREDIT)                  ")
    print("================================================================================")
    print(f"Initial Capital:          Rs. {initial_capital:,.2f}")
    print(f"Backtest Period:          {start_date} to {end_date}")
    print("Strategy Setup:")
    print("  * Signal Time:          09:20:00 (Find ATM Spot)")
    print("  * Execution Time:       09:21:00 IST")
    print("  * Expiry Selection:     Nearest Weekly Expiry strictly after trade date (expiry > date)")
    print("  * Call Wing:            SELL ATM + 200 CE  |  BUY ATM + 500 CE")
    print("  * Put Wing:             SELL ATM - 200 PE  |  BUY ATM - 500 PE")
    print("  * Target:               +65% of Net Credit Received")
    print("  * Tight Stop-Loss:      -40% of Net Credit Received (0.40x Credit)")
    print("  * Time Exit:            15:15:00 IST (Forced Square-off)")
    print("  * Transaction Costs:    Rs. 160 Brokerage (8 orders x Rs.20) + 0.15% Slippage on Turnover")
    print("================================================================================\n")

    t0 = time.time()

    # 1. Load NIFTY Spot Data
    print(f"Loading Spot Data from {start_date} to {end_date}...")
    df_spot = pd.read_parquet(spot_path)
    df_spot_range = df_spot.loc[start_date:end_date].copy()
    if df_spot_range.empty:
        print("No spot data found in date range!")
        sys.exit(1)
    trading_days = sorted(df_spot_range.index.normalize().unique())
    print(f"Loaded {len(df_spot_range):,} spot candles across {len(trading_days)} trading days.")

    # 2. Load Options Data via PyArrow Predicate Pushdown (<1s)
    print(f"Loading Options Vault from {start_date} to {end_date} via PyArrow Predicate Pushdown...")
    dataset = ds.dataset(options_path, format='parquet')
    filter_expr = (
        (ds.field('timestamp') >= pd.to_datetime(f"{start_date} 00:00:00")) & 
        (ds.field('timestamp') <= pd.to_datetime(f"{end_date} 23:59:59"))
    )
    table = dataset.to_table(filter=filter_expr)
    df_opt = table.to_pandas()
    print(f"Loaded {len(df_opt):,} options rows in {time.time()-t0:.2f} seconds.\n")

    df_opt['date'] = df_opt['timestamp'].dt.date

    trades = []
    running_capital = initial_capital

    print("Simulating Tight-SL Iron Condors minute-by-minute...")

    for current_day in trading_days:
        day_str = current_day.strftime('%Y-%m-%d')
        date_obj = current_day.date()
        day_name = current_day.day_name()
        lot_size = get_nifty_lot_size(date_obj)

        spot_day = df_spot_range.loc[day_str]
        if spot_day.empty:
            continue

        ts_0920 = pd.to_datetime(f"{day_str} 09:20:00")
        ts_0921 = pd.to_datetime(f"{day_str} 09:21:00")
        ts_1515 = pd.to_datetime(f"{day_str} 15:15:00")

        if ts_0920 not in spot_day.index or ts_0921 not in spot_day.index:
            continue

        spot_0920 = spot_day.loc[ts_0920, 'close']
        atm = round(spot_0920 / 50.0) * 50.0

        # Smart Expiry Selection: nearest expiry strictly > today's date
        opt_day = df_opt[df_opt['date'] == date_obj]
        valid_expiries = sorted(opt_day[opt_day['expiry'].dt.date > date_obj]['expiry'].unique())
        if not valid_expiries:
            continue
        target_expiry = valid_expiries[0]
        dte = (target_expiry.date() - date_obj).days

        # Define 4 Iron Condor Legs
        sc_strike = atm + 200.0  # Sell Call
        lc_strike = atm + 500.0  # Buy Call
        sp_strike = atm - 200.0  # Sell Put
        lp_strike = atm - 500.0  # Buy Put

        # Extract minute series for each leg
        sc_series = opt_day[(opt_day['expiry'] == target_expiry) & (opt_day['strike_price'] == sc_strike) & (opt_day['option_type'] == 'CE')].set_index('timestamp')['close']
        lc_series = opt_day[(opt_day['expiry'] == target_expiry) & (opt_day['strike_price'] == lc_strike) & (opt_day['option_type'] == 'CE')].set_index('timestamp')['close']
        sp_series = opt_day[(opt_day['expiry'] == target_expiry) & (opt_day['strike_price'] == sp_strike) & (opt_day['option_type'] == 'PE')].set_index('timestamp')['close']
        lp_series = opt_day[(opt_day['expiry'] == target_expiry) & (opt_day['strike_price'] == lp_strike) & (opt_day['option_type'] == 'PE')].set_index('timestamp')['close']

        if sc_series.empty or lc_series.empty or sp_series.empty or lp_series.empty:
            continue

        # Synchronize minute-by-minute
        m = pd.DataFrame({'sc': sc_series, 'lc': lc_series, 'sp': sp_series, 'lp': lp_series}).sort_index()
        m = m.ffill().dropna()

        # Check entry at 09:21:00
        if ts_0921 not in m.index:
            near_bars = m[(m.index >= ts_0921) & (m.index <= ts_0921 + pd.Timedelta(minutes=2))]
            if near_bars.empty:
                continue
            entry_ts = near_bars.index[0]
        else:
            entry_ts = ts_0921

        entry_row = m.loc[entry_ts]
        sc_entry = float(entry_row['sc'])
        lc_entry = float(entry_row['lc'])
        sp_entry = float(entry_row['sp'])
        lp_entry = float(entry_row['lp'])

        # Net Credit = (Short Call - Long Call) + (Short Put - Long Put)
        call_credit = sc_entry - lc_entry
        put_credit = sp_entry - lp_entry
        net_credit_pts = call_credit + put_credit

        if net_credit_pts <= 5.0:
            continue

        net_credit_rs = net_credit_pts * lot_size

        # Exit thresholds:
        # Target: Profit >= 65% of Net Credit -> Current spread <= 35% of entry credit
        target_spread = net_credit_pts * 0.35
        # Tight Stop-Loss: Loss >= 40% of Net Credit -> Current spread >= 140% of entry credit
        sl_spread = net_credit_pts * 1.40
        sl_limit_pts = round(net_credit_pts * 0.40, 2)
        sl_limit_rs = round(sl_limit_pts * lot_size, 2)

        # Track minute-by-minute from entry_ts + 1min up to 15:15:00
        track_df = m[(m.index > entry_ts) & (m.index <= ts_1515)].copy()
        if track_df.empty:
            continue

        track_df['spread'] = (track_df['sc'] - track_df['lc']) + (track_df['sp'] - track_df['lp'])

        exit_time = None
        exit_cost_pts = None
        exit_reason = None
        sc_exit, lc_exit, sp_exit, lp_exit = None, None, None, None

        for t, row in track_df.iterrows():
            spread_val = float(row['spread'])

            # 1. Tight Stop Loss (-40% of Net Credit)
            if spread_val >= sl_spread:
                exit_time = t
                exit_cost_pts = spread_val
                exit_reason = "STOP_LOSS (-40%)"
                sc_exit, lc_exit, sp_exit, lp_exit = row['sc'], row['lc'], row['sp'], row['lp']
                break

            # 2. Target (+65% of Net Credit)
            elif spread_val <= target_spread:
                exit_time = t
                exit_cost_pts = spread_val
                exit_reason = "TARGET (+65%)"
                sc_exit, lc_exit, sp_exit, lp_exit = row['sc'], row['lc'], row['sp'], row['lp']
                break

            # 3. Time Exit at 15:15:00
            if t >= ts_1515:
                exit_time = t
                exit_cost_pts = spread_val
                exit_reason = "TIME_EXIT (15:15)"
                sc_exit, lc_exit, sp_exit, lp_exit = row['sc'], row['lc'], row['sp'], row['lp']
                break

        if exit_time is None:
            last_row = track_df.iloc[-1]
            exit_time = track_df.index[-1]
            exit_cost_pts = float(last_row['spread'])
            exit_reason = "TIME_EXIT (Close)"
            sc_exit, lc_exit, sp_exit, lp_exit = last_row['sc'], last_row['lc'], last_row['sp'], last_row['lp']

        # Financial Calculations
        pnl_pts = net_credit_pts - exit_cost_pts
        gross_pnl = round(pnl_pts * lot_size, 2)

        # Realistic Transaction Costs
        brokerage = 160.0  # 8 orders x Rs. 20
        entry_turnover = (sc_entry + lc_entry + sp_entry + lp_entry) * lot_size
        exit_turnover = (sc_exit + lc_exit + sp_exit + lp_exit) * lot_size
        total_turnover = entry_turnover + exit_turnover
        slippage = round(total_turnover * 0.0015, 2)

        total_costs = round(brokerage + slippage, 2)
        net_pnl = round(gross_pnl - total_costs, 2)
        running_capital = round(running_capital + net_pnl, 2)

        trades.append({
            'date': day_str,
            'day_name': day_name,
            'expiry': target_expiry.strftime('%Y-%m-%d'),
            'dte': dte,
            'lot_size': lot_size,
            'atm': int(atm),
            'net_credit_pts': round(net_credit_pts, 2),
            'net_credit_rs': round(net_credit_rs, 2),
            'sl_limit_pts': sl_limit_pts,
            'sl_limit_rs': sl_limit_rs,
            'exit_time': exit_time.strftime('%H:%M:%S'),
            'exit_cost_pts': round(exit_cost_pts, 2),
            'exit_reason': exit_reason,
            'pnl_pts': round(pnl_pts, 2),
            'gross_pnl': gross_pnl,
            'brokerage': brokerage,
            'slippage': slippage,
            'total_costs': total_costs,
            'net_pnl': net_pnl,
            'running_capital': running_capital
        })

    df_trades = pd.DataFrame(trades)
    if df_trades.empty:
        print("No trades triggered!")
        return

    df_trades.to_csv(daily_csv, index=False)

    # --------------------------------------------------------------------------
    # (a) PRINT FULL DAY-BY-DAY TRADE TABLE
    # --------------------------------------------------------------------------
    print("=======================================================================================================================================================")
    print("                                      INTRADAY IRON CONDOR (TIGHT SL 40%) DAY-BY-DAY LEDGER                                            ")
    print("=======================================================================================================================================================")
    header_fmt = "{:<10} | {:<9} | {:<10} | {:<3} | {:<3} | {:<5} | {:>7} | {:>7} | {:<8} | {:<18} | {:>9} | {:>7} | {:>10} | {:>13}"
    row_fmt    = "{:<10} | {:<9} | {:<10} | {:<3} | {:<3} | {:<5} | {:>7.2f} | {:>7.2f} | {:<8} | {:<18} | {:>9.2f} | {:>7.2f} | {:>10.2f} | {:>13.2f}"
    
    print(header_fmt.format(
        "Date", "Day", "Expiry", "DTE", "Lot", "ATM", "Credit", "SLLimit", "ExitAt", "ExitReason", "GrossPnL", "Costs", "NetPnL(Rs)", "Capital(Rs)"
    ))
    print("-" * 153)

    for _, r in df_trades.iterrows():
        print(row_fmt.format(
            r['date'], r['day_name'][:9], r['expiry'], r['dte'], r['lot_size'], r['atm'],
            r['net_credit_pts'], r['sl_limit_pts'], r['exit_time'], r['exit_reason'],
            r['gross_pnl'], r['total_costs'], r['net_pnl'], r['running_capital']
        ))

    print("=" * 153)

    # --------------------------------------------------------------------------
    # (b) MONTH-BY-MONTH SUMMARY TABLE
    # --------------------------------------------------------------------------
    df_trades['month'] = pd.to_datetime(df_trades['date']).dt.to_period('M')
    monthly_summary = []
    
    for mth, grp in df_trades.groupby('month'):
        m_trades = len(grp)
        m_wins = (grp['net_pnl'] > 0).sum()
        m_losses = (grp['net_pnl'] <= 0).sum()
        m_win_rate = (m_wins / m_trades * 100) if m_trades > 0 else 0
        m_gross = grp['gross_pnl'].sum()
        m_costs = grp['total_costs'].sum()
        m_net = grp['net_pnl'].sum()
        m_ret_pct = (m_net / initial_capital) * 100

        monthly_summary.append({
            'month': str(mth),
            'trades': m_trades,
            'wins': m_wins,
            'losses': m_losses,
            'win_rate_pct': round(m_win_rate, 1),
            'gross_pnl': round(m_gross, 2),
            'total_costs': round(m_costs, 2),
            'net_pnl': round(m_net, 2),
            'return_on_cap_pct': round(m_ret_pct, 2)
        })

    df_monthly = pd.DataFrame(monthly_summary)
    df_monthly.to_csv(monthly_csv, index=False)

    print("\n===================================================================================================")
    print("                                MONTH-BY-MONTH PERFORMANCE BREAKDOWN                               ")
    print("===================================================================================================")
    m_head_fmt = "{:<8} | {:>6} | {:>4} | {:>6} | {:>8} | {:>12} | {:>10} | {:>12} | {:>14}"
    m_row_fmt  = "{:<8} | {:>6} | {:>4} | {:>6} | {:>7.1f}% | {:>12.2f} | {:>10.2f} | {:>12.2f} | {:>13.2f}%"
    print(m_head_fmt.format("Month", "Trades", "Wins", "Losses", "WinRate", "GrossPnL(Rs)", "Costs(Rs)", "NetPnL(Rs)", "ReturnOnCap"))
    print("-" * 99)
    for _, r in df_monthly.iterrows():
        print(m_row_fmt.format(
            r['month'], r['trades'], r['wins'], r['losses'], r['win_rate_pct'],
            r['gross_pnl'], r['total_costs'], r['net_pnl'], r['return_on_cap_pct']
        ))
    print("=" * 99)

    # --------------------------------------------------------------------------
    # (c) EXECUTIVE QUANT SUMMARY SCORECARD
    # --------------------------------------------------------------------------
    total_trades = len(df_trades)
    total_wins = (df_trades['net_pnl'] > 0).sum()
    total_losses = (df_trades['net_pnl'] <= 0).sum()
    overall_win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    
    total_gross_pnl = df_trades['gross_pnl'].sum()
    total_brokerage = df_trades['brokerage'].sum()
    total_slippage = df_trades['slippage'].sum()
    total_all_costs = df_trades['total_costs'].sum()
    total_net_pnl = df_trades['net_pnl'].sum()
    final_capital = running_capital
    total_return_pct = (total_net_pnl / initial_capital) * 100

    target_hits = (df_trades['exit_reason'].str.contains('TARGET')).sum()
    sl_hits = (df_trades['exit_reason'].str.contains('STOP_LOSS')).sum()
    time_exits = (df_trades['exit_reason'].str.contains('TIME_EXIT')).sum()

    # Drawdown
    df_trades['peak'] = df_trades['running_capital'].cummax()
    df_trades['dd_rs'] = df_trades['running_capital'] - df_trades['peak']
    df_trades['dd_pct'] = (df_trades['dd_rs'] / df_trades['peak']) * 100
    max_dd_rs = df_trades['dd_rs'].min()
    max_dd_pct = df_trades['dd_pct'].min()

    # Max Win / Max Loss
    max_win_row = df_trades.loc[df_trades['net_pnl'].idxmax()]
    max_loss_row = df_trades.loc[df_trades['net_pnl'].idxmin()]

    # Average Win vs Average Loss
    win_trades = df_trades[df_trades['net_pnl'] > 0]
    loss_trades = df_trades[df_trades['net_pnl'] < 0]
    avg_win = win_trades['net_pnl'].mean() if not win_trades.empty else 0.0
    avg_loss = loss_trades['net_pnl'].mean() if not loss_trades.empty else 0.0

    # Profit Factor
    gross_win_sum = win_trades['gross_pnl'].sum() if not win_trades.empty else 0.0
    gross_loss_sum = abs(loss_trades['gross_pnl'].sum()) if not loss_trades.empty else 0.0
    profit_factor = (gross_win_sum / gross_loss_sum) if gross_loss_sum > 0 else float('inf')

    print("\n================================================================================")
    print("                    EXECUTIVE QUANTITATIVE SCORECARD                            ")
    print("================================================================================")
    print(f"Starting Capital:                 Rs. {initial_capital:,.2f}")
    print(f"Final Capital:                    Rs. {final_capital:,.2f}")
    print(f"Total Net Profit / Loss:          Rs. {total_net_pnl:+,.2f} ({total_return_pct:+.2f}%)")
    print("--------------------------------------------------------------------------------")
    print(f"Total Trades Taken:               {total_trades}")
    print(f"Target Hits (+65% Credit):        {target_hits} ({target_hits/total_trades*100:.1f}%)")
    print(f"Tight SL Hits (-40% Credit):      {sl_hits} ({sl_hits/total_trades*100:.1f}%)")
    print(f"Time Exits (15:15 IST):           {time_exits} ({time_exits/total_trades*100:.1f}%)")
    print(f"Win Rate:                         {overall_win_rate:.1f}% ({total_wins} Wins / {total_losses} Losses)")
    print("--------------------------------------------------------------------------------")
    print(f"Total Gross PnL:                  Rs. {total_gross_pnl:+,.2f}")
    print(f"Total Brokerage (Rs.160/trade):   Rs. {total_brokerage:,.2f}")
    print(f"Total Slippage (0.15% Turnover):  Rs. {total_slippage:,.2f}")
    print(f"Total Execution Costs:            Rs. {total_all_costs:,.2f}")
    print("--------------------------------------------------------------------------------")
    print(f"Max Drawdown (Rs):                Rs. {max_dd_rs:,.2f} ({max_dd_pct:.2f}%)")
    print(f"Max Single-Day Profit:            Rs. {max_win_row['net_pnl']:+,.2f} (on {max_win_row['date']} | {max_win_row['exit_reason']})")
    print(f"Max Single-Day Loss:              Rs. {max_loss_row['net_pnl']:+,.2f} (on {max_loss_row['date']} | {max_loss_row['exit_reason']})")
    print(f"Average Win Trade:                Rs. {avg_win:+,.2f}")
    print(f"Average Loss Trade:               Rs. {avg_loss:+,.2f}")
    print(f"Profit Factor (Gross):            {profit_factor:.2f}")
    print("--------------------------------------------------------------------------------")
    print(f"Day-by-Day Trade Ledger:          {daily_csv.resolve()}")
    print(f"Monthly Summary CSV:              {monthly_csv.resolve()}")
    print("================================================================================")
    print(f"Execution completed in {time.time()-t0:.2f} seconds.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Intraday Iron Condor (Tight SL) Backtest Engine")
    parser.add_argument("--start", type=str, default="2025-01-01", help="Start Date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="2025-01-31", help="End Date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=200000.0, help="Initial Capital (Rs.)")
    args = parser.parse_args()

    run_tight_sl_iron_condor_backtest(args.start, args.end, args.capital)
