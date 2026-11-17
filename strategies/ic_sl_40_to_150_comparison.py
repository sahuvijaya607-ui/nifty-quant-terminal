import os
import sys
import time
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import numpy as np
import pyarrow.dataset as ds

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.nifty_lot_size import get_nifty_lot_size

def run_sl_sensitivity_study():
    t_start = time.time()
    
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    options_path = Path("data/nifty_options_master_2020_2026_1min.parquet")
    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)
    
    output_csv = results_dir / "ic_6yr_sl_40_to_150.csv"
    
    initial_capital = 200000.0
    sl_levels = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.15, 1.30, 1.50]
    
    print("=" * 110)
    print("      NIFTY INTRADAY IRON CONDOR: 6-YEAR STOP-LOSS SENSITIVITY STUDY (2020 - 2026)      ")
    print("=" * 110)
    print(f"Starting Capital:       Rs. {initial_capital:,.2f}")
    print("Position Sizing:        1 Lot (1 x get_nifty_lot_size(trade_date))")
    print("Signal Time:            09:20:00 IST (Find ATM Spot = round(spot / 50) * 50)")
    print("Execution Time:         09:21:00 IST (Close prices of 1-min candle)")
    print("Expiry Selection:       Nearest Weekly Expiry strictly after trade date (expiry > date)")
    print("Leg Architecture:       SELL ATM + 150 CE  |  BUY ATM + 450 CE")
    print("                        SELL ATM - 150 PE  |  BUY ATM - 450 PE")
    print("Profit Target:          +65% of Net Credit Received")
    print("Forced Square-off:      15:15:00 IST")
    print("Transaction Costs:      Rs. 160 Brokerage (8 orders x Rs. 20) + 0.15% Slippage on Turnover")
    print(f"Tested SL Levels (10):  {[f'{int(round(s*100))}%' for s in sl_levels]}")
    print("=" * 110 + "\n")
    
    # 1. Load Master Spot Data
    print("Loading 2020-2026 Master Spot Data...")
    df_spot = pd.read_parquet(spot_path)
    all_trading_days = sorted(df_spot.index.normalize().unique())
    print(f"Loaded {len(df_spot):,} spot candles across {len(all_trading_days)} trading days ({all_trading_days[0].strftime('%Y-%m-%d')} to {all_trading_days[-1].strftime('%Y-%m-%d')}).\n")
    
    import pickle
    cache_path = Path("data/cache_ic_spreads_2020_2026.pkl")
    daily_spread_contexts = []
    
    if cache_path.exists():
        print(f"Loading pre-computed 1-minute Iron Condor spreads from {cache_path}...")
        t_c = time.time()
        with open(cache_path, 'rb') as f:
            daily_spread_contexts = pickle.load(f)
        print(f"Loaded {len(daily_spread_contexts):,} trading sessions from cache in {time.time()-t_c:.2f}s!\n")
    else:
        # 2. Extract Spreads Year-by-Year
        dataset = ds.dataset(options_path, format='parquet')
        cols = ['timestamp', 'expiry', 'strike_price', 'option_type', 'close']
        years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
        
        print("Extracting 1-minute Iron Condor spreads from Options Vault year-by-year...")
    
        for yr in years:
            t_yr = time.time()
            filt = (
                (ds.field('timestamp') >= pd.to_datetime(f"{yr}-01-01 00:00:00")) & 
                (ds.field('timestamp') <= pd.to_datetime(f"{yr}-12-31 23:59:59"))
            )
            tbl = dataset.to_table(filter=filt, columns=cols)
            df_opt = tbl.to_pandas()
            df_opt['date'] = df_opt['timestamp'].dt.date
            
            trading_days_yr = [d for d in all_trading_days if d.year == yr]
            days_dict = {d: grp for d, grp in df_opt.groupby('date')}
            
            yr_trades = 0
            for current_day in trading_days_yr:
                date_obj = current_day.date()
                day_str = current_day.strftime('%Y-%m-%d')
                day_name = current_day.day_name()
                
                if date_obj not in days_dict or day_str not in df_spot.index:
                    continue
                
                opt_day = days_dict[date_obj]
                spot_day = df_spot.loc[day_str]
                
                ts_0920 = pd.to_datetime(f"{day_str} 09:20:00")
                ts_0921 = pd.to_datetime(f"{day_str} 09:21:00")
                ts_1515 = pd.to_datetime(f"{day_str} 15:15:00")
                
                if ts_0920 not in spot_day.index or ts_0921 not in spot_day.index:
                    continue
                    
                spot_0920 = spot_day.loc[ts_0920, 'close']
                atm = round(spot_0920 / 50.0) * 50.0
                
                val_exp = sorted(opt_day[opt_day['expiry'].dt.date > date_obj]['expiry'].unique())
                if not val_exp:
                    continue
                target_expiry = val_exp[0]
                dte = (target_expiry.date() - date_obj).days
                lot_size = get_nifty_lot_size(date_obj)
                
                # 4 Legs
                sc_strike = atm + 150.0
                lc_strike = atm + 450.0
                sp_strike = atm - 150.0
                lp_strike = atm - 450.0
                
                sub = opt_day[opt_day['expiry'] == target_expiry]
                needed = [sc_strike, lc_strike, sp_strike, lp_strike]
                legs_df = sub[sub['strike_price'].isin(needed)]
                if legs_df.empty:
                    continue
                    
                legs_df = legs_df.drop_duplicates(subset=['timestamp', 'strike_price', 'option_type'])
                piv = legs_df.pivot(index='timestamp', columns=['strike_price', 'option_type'], values='close')
                
                req_cols = [(sc_strike, 'CE'), (lc_strike, 'CE'), (sp_strike, 'PE'), (lp_strike, 'PE')]
                if not all(col in piv.columns for col in req_cols):
                    continue
                    
                sc_s = piv[(sc_strike, 'CE')].ffill()
                lc_s = piv[(lc_strike, 'CE')].ffill()
                sp_s = piv[(sp_strike, 'PE')].ffill()
                lp_s = piv[(lp_strike, 'PE')].ffill()
                
                m = pd.DataFrame({'sc': sc_s, 'lc': lc_s, 'sp': sp_s, 'lp': lp_s}).dropna()
                
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
                
                net_credit_pts = (sc_entry - lc_entry) + (sp_entry - lp_entry)
                if net_credit_pts <= 5.0:
                    continue
                    
                track_df = m[(m.index > entry_ts) & (m.index <= ts_1515)].copy()
                if track_df.empty:
                    continue
                    
                track_df['spread'] = (track_df['sc'] - track_df['lc']) + (track_df['sp'] - track_df['lp'])
                
                daily_spread_contexts.append({
                    'date': day_str,
                    'date_obj': date_obj,
                    'day_name': day_name,
                    'expiry': target_expiry,
                    'dte': dte,
                    'lot_size': lot_size,
                    'atm': int(atm),
                    'entry_ts': entry_ts,
                    'sc_entry': sc_entry,
                    'lc_entry': lc_entry,
                    'sp_entry': sp_entry,
                    'lp_entry': lp_entry,
                    'net_credit_pts': net_credit_pts,
                    'ts_1515': ts_1515,
                    'times': track_df.index.to_numpy(),
                    'spreads': track_df['spread'].to_numpy(),
                    'sc': track_df['sc'].to_numpy(),
                    'lc': track_df['lc'].to_numpy(),
                    'sp': track_df['sp'].to_numpy(),
                    'lp': track_df['lp'].to_numpy()
                })
                yr_trades += 1
                
            print(f"  * Year {yr}: Extracted {yr_trades} qualifying Iron Condors in {time.time()-t_yr:.2f}s")
            
        total_valid_days = len(daily_spread_contexts)
        print(f"\nSpread extraction complete! Total trading sessions: {total_valid_days} days across 2020-2026.\n")
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(daily_spread_contexts, f)
            print(f"Cached 1-minute spread trajectories to {cache_path}\n")
        except Exception as e:
            print(f"Cache notice: {e}\n")
    
    # 3. Simulate All 10 Stop-Loss Levels in Parallel
    print("Evaluating 10 Stop-Loss levels [40% to 150%] across all 6 years...")
    t_sim = time.time()
    
    summary_results = []
    
    for sl_pct in sl_levels:
        sl_label = f"{int(round(sl_pct * 100))}%"
        
        trades = []
        running_cap = initial_capital
        cap_curve = [initial_capital]
        
        sl_hits = 0
        tgt_hits = 0
        time_exits = 0
        
        for ctx in daily_spread_contexts:
            net_credit = ctx['net_credit_pts']
            lot_size = ctx['lot_size']
            
            tgt_thresh = net_credit * 0.35
            sl_thresh = net_credit * (1.0 + sl_pct)
            
            spreads = ctx['spreads']
            times = ctx['times']
            n_bars = len(spreads)
            last_idx = n_bars - 1
            
            exit_idx = -1
            exit_reason = None
            
            for i in range(n_bars):
                sp_val = spreads[i]
                if sp_val >= sl_thresh:
                    exit_idx = i
                    exit_reason = f"STOP_LOSS (-{sl_label})"
                    sl_hits += 1
                    break
                elif sp_val <= tgt_thresh:
                    exit_idx = i
                    exit_reason = "TARGET (+65%)"
                    tgt_hits += 1
                    break
                elif times[i] >= ctx['ts_1515']:
                    exit_idx = i
                    exit_reason = "TIME_EXIT (15:15)"
                    time_exits += 1
                    break
                    
            if exit_idx == -1:
                exit_idx = last_idx
                exit_reason = "TIME_EXIT (Close)"
                time_exits += 1
                
            exit_cost = spreads[exit_idx]
            sc_ex = ctx['sc'][exit_idx]
            lc_ex = ctx['lc'][exit_idx]
            sp_ex = ctx['sp'][exit_idx]
            lp_ex = ctx['lp'][exit_idx]
            
            pnl_pts = net_credit - exit_cost
            gross_pnl = round(pnl_pts * lot_size, 2)
            
            brokerage = 160.0
            entry_turnover = (ctx['sc_entry'] + ctx['lc_entry'] + ctx['sp_entry'] + ctx['lp_entry']) * lot_size
            exit_turnover = (sc_ex + lc_ex + sp_ex + lp_ex) * lot_size
            slippage = round((entry_turnover + exit_turnover) * 0.0015, 2)
            total_costs = round(brokerage + slippage, 2)
            net_pnl = round(gross_pnl - total_costs, 2)
            
            running_cap = round(running_cap + net_pnl, 2)
            cap_curve.append(running_cap)
            
            trades.append({
                'date': ctx['date'],
                'gross_pnl': gross_pnl,
                'total_costs': total_costs,
                'net_pnl': net_pnl,
                'running_capital': running_cap,
                'exit_reason': exit_reason
            })
            
        # Performance Metrics for this SL level
        df_t = pd.DataFrame(trades)
        total_trades = len(df_t)
        wins = (df_t['net_pnl'] > 0).sum()
        losses = (df_t['net_pnl'] <= 0).sum()
        win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
        
        gross_pnl_total = df_t['gross_pnl'].sum()
        costs_total = df_t['total_costs'].sum()
        net_pnl_total = df_t['net_pnl'].sum()
        final_cap = running_cap
        
        # Drawdown calculation
        cap_series = pd.Series(cap_curve)
        peak_series = cap_series.cummax()
        dd_series = cap_series - peak_series
        dd_pct_series = (dd_series / peak_series) * 100.0
        
        max_dd_rs = dd_series.min()
        max_dd_pct = dd_pct_series.min()
        
        # Profit Factor
        sum_gains = df_t[df_t['net_pnl'] > 0]['net_pnl'].sum()
        sum_losses = abs(df_t[df_t['net_pnl'] < 0]['net_pnl'].sum())
        profit_factor = (sum_gains / sum_losses) if sum_losses > 0 else np.nan
        
        # Return-to-Drawdown Ratio: Net Profit / abs(Max DD)
        return_to_dd = (net_pnl_total / abs(max_dd_rs)) if abs(max_dd_rs) > 0 else 0.0
        
        summary_results.append({
            'sl_pct': sl_pct,
            'sl_label': sl_label,
            'total_trades': total_trades,
            'sl_hits': sl_hits,
            'tgt_hits': tgt_hits,
            'time_exits': time_exits,
            'win_rate_pct': round(win_rate, 2),
            'gross_pnl': round(gross_pnl_total, 2),
            'total_costs': round(costs_total, 2),
            'net_pnl': round(net_pnl_total, 2),
            'final_capital': round(final_cap, 2),
            'max_dd_rs': round(max_dd_rs, 2),
            'max_dd_pct': round(max_dd_pct, 2),
            'profit_factor': round(profit_factor, 2),
            'return_to_dd': round(return_to_dd, 2)
        })
        
    print(f"Simulation of all 10 SL levels completed in {time.time()-t_sim:.2f} seconds.\n")
    
    # 4. Rank Leaderboard by Return-to-Drawdown Ratio descending
    df_summary = pd.DataFrame(summary_results)
    df_summary = df_summary.sort_values(by='return_to_dd', ascending=False).reset_index(drop=True)
    df_summary['rank'] = df_summary.index + 1
    
    # Save CSV
    export_df = df_summary[[
        'rank', 'sl_label', 'total_trades', 'sl_hits', 'tgt_hits', 'win_rate_pct',
        'gross_pnl', 'net_pnl', 'final_capital', 'max_dd_rs', 'max_dd_pct',
        'profit_factor', 'return_to_dd'
    ]].copy()
    
    export_df.columns = [
        'Rank', 'SL %', 'Total Trades', 'SL Hits', 'Target Hits', 'Win Rate (%)',
        'Gross PnL (Rs)', 'Net PnL (Rs)', 'Final Capital (Rs)', 'Max Drawdown (Rs)',
        'Max Drawdown (%)', 'Profit Factor', 'Return/DD Score'
    ]
    export_df.to_csv(output_csv, index=False)
    print(f"Saved complete comparative rankings to {output_csv}\n")
    
    # 5. Print Clean TOP 10 STOP-LOSS LEADERBOARD
    print("=" * 158)
    print("                                              TOP 10 STOP-LOSS LEADERBOARD (2020 - 2026)                                               ")
    print("                                            Ranked by Return-to-Drawdown Ratio (Net PnL / Max DD)                                      ")
    print("=" * 158)
    
    head_fmt = "{:<5} | {:<5} | {:>6} | {:>7} | {:>8} | {:>8} | {:>14} | {:>14} | {:>14} | {:>14} | {:>10} | {:>8} | {:>10}"
    row_fmt  = "{:<5} | {:<5} | {:>6} | {:>7} | {:>8} | {:>7.2f}% | {:>14.2f} | {:>14.2f} | {:>14.2f} | {:>14.2f} | {:>9.2f}% | {:>8.2f} | {:>10.2f}"
    
    print(head_fmt.format(
        "Rank", "SL %", "Trades", "SL Hits", "Tgt Hits", "Win Rate", "Gross PnL (Rs)", "Net PnL (Rs)", "Final Cap (Rs)", "Max DD (Rs)", "Max DD %", "ProfitFac", "Return/DD"
    ))
    print("-" * 158)
    
    for _, r in df_summary.iterrows():
        print(row_fmt.format(
            f"#{r['rank']}", r['sl_label'], r['total_trades'], r['sl_hits'], r['tgt_hits'],
            r['win_rate_pct'], r['gross_pnl'], r['net_pnl'], r['final_capital'],
            r['max_dd_rs'], r['max_dd_pct'], r['profit_factor'], r['return_to_dd']
        ))
    print("=" * 158 + "\n")
    
    # Best vs Baseline Commentary
    best = df_summary.iloc[0]
    baseline_100 = df_summary[df_summary['sl_label'] == '100%'].iloc[0]
    tight_40 = df_summary[df_summary['sl_label'] == '40%'].iloc[0]
    
    print("------------------------------------------------------------------------------------------------------------------------------")
    print("                                                 EXECUTIVE QUANTITATIVE FINDINGS                                              ")
    print("------------------------------------------------------------------------------------------------------------------------------")
    print(f"  • Optimal SL Configuration:  Rank #1 is {best['sl_label']} SL with Return/DD Score of {best['return_to_dd']:.2f}")
    print(f"  • Net Alpha Generated:       Rs. {best['net_pnl']:,.2f} on Rs. 2,00,000 capital (Final Capital: Rs. {best['final_capital']:,.2f})")
    print(f"  • Maximum Drawdown Control:  Rs. {best['max_dd_rs']:,.2f} ({best['max_dd_pct']:.2f}%)")
    print(f"  • Win Rate & Profit Factor:  {best['win_rate_pct']:.2f}% Win Rate | Profit Factor: {best['profit_factor']:.2f}")
    print(f"  • Comparison vs 100% SL:     100% SL generated Rs. {baseline_100['net_pnl']:,.2f} Net with Max DD of Rs. {baseline_100['max_dd_rs']:,.2f} (Score: {baseline_100['return_to_dd']:.2f})")
    print(f"  • Comparison vs 40% SL:      40% SL generated Rs. {tight_40['net_pnl']:,.2f} Net with Max DD of Rs. {tight_40['max_dd_rs']:,.2f} (Score: {tight_40['return_to_dd']:.2f})")
    print("------------------------------------------------------------------------------------------------------------------------------")
    print(f"Total Workflow Runtime: {time.time()-t_start:.2f} seconds.")
    print("=" * 110)

if __name__ == "__main__":
    run_sl_sensitivity_study()
