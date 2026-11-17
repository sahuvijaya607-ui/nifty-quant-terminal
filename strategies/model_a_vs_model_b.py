import os
import sys
import time
import pickle
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import numpy as np
import pyarrow.dataset as ds

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.nifty_lot_size import get_nifty_lot_size

def run_model_a_vs_model_b():
    t_start = time.time()
    
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    options_path = Path("data/nifty_options_master_2020_2026_1min.parquet")
    cached_intraday_path = Path("data/cache_ic_spreads_2020_2026.pkl")
    cached_weekly_path = Path("data/cache_weekly_positional_spreads_2020_2026.pkl")
    
    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)
    
    comparison_csv = results_dir / "model_a_vs_model_b_comparison.csv"
    model_a_csv = results_dir / "model_a_intraday_trades.csv"
    model_b_csv = results_dir / "model_b_weekly_positional_trades.csv"
    
    initial_capital = 200000.0
    lots_multiplier = 2  # 2 Lots
    sl_pct = 0.90        # 90% Stop-Loss
    tgt_pct = 0.65       # 65% Target
    brokerage_per_trade = 0.0  # Kotak Neo Zero-Brokerage Plan
    slippage_rate = 0.0015     # 0.15% Slippage / Turnover charges
    
    print("=" * 120)
    print("      QUANTITATIVE STRATEGY BENCHMARK: MODEL A (INTRADAY) VS MODEL B (WEEKLY POSITIONAL)      ")
    print("                                6-YEAR BACKTEST (2020 - 2026)                                 ")
    print("=" * 120)
    print(f"Starting Capital:       Rs. {initial_capital:,.2f}")
    print(f"Position Sizing:        {lots_multiplier} Lots (2 x get_nifty_lot_size(trade_date))")
    print(f"Stop-Loss Level:        90% of Net Credit Received")
    print(f"Profit Target:          65% of Net Credit Received")
    print(f"Fee Structure:          Kotak Neo Plan: Rs. 0 Brokerage + 0.15% Turnover Slippage/Exchange Fees")
    print(f"Leg Geometry:           SELL ATM + 150 CE  |  BUY ATM + 450 CE")
    print(f"                        SELL ATM - 150 PE  |  BUY ATM - 450 PE")
    print("Model A Paradigm:       Daily Intraday Iron Condor (Enter 09:21, Force Close 15:15 daily, ~1,278 trades)")
    print("Model B Paradigm:       Weekly Positional Iron Condor (Enter Day 1 of cycle 09:21, Hold to Expiry 15:15, ~315 trades)")
    print("=" * 120 + "\n")
    
    # --------------------------------------------------------------------------
    # PART 1: SIMULATE MODEL A (INTRADAY 90% SL, 2 LOTS, ZERO-BROKERAGE)
    # --------------------------------------------------------------------------
    print(">>> [1/3] Simulating MODEL A (Daily Intraday Iron Condor)...")
    if not cached_intraday_path.exists():
        print(f"Error: {cached_intraday_path} not found! Please run strategies/ic_sl_40_to_150_comparison.py first.")
        sys.exit(1)
        
    t_a0 = time.time()
    with open(cached_intraday_path, 'rb') as f:
        daily_spread_contexts = pickle.load(f)
    print(f"  * Loaded {len(daily_spread_contexts):,} daily sessions from cache.")
    
    trades_a = []
    cap_a = initial_capital
    cap_curve_a = [initial_capital]
    
    for ctx in daily_spread_contexts:
        lot_size = ctx['lot_size'] * lots_multiplier
        net_credit = ctx['net_credit_pts']
        
        tgt_thresh = net_credit * (1.0 - tgt_pct)  # 0.35x
        sl_thresh = net_credit * (1.0 + sl_pct)    # 1.90x
        
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
                exit_reason = "STOP_LOSS (-90%)"
                break
            elif sp_val <= tgt_thresh:
                exit_idx = i
                exit_reason = "TARGET (+65%)"
                break
            elif times[i] >= ctx['ts_1515']:
                exit_idx = i
                exit_reason = "TIME_EXIT (15:15)"
                break
                
        if exit_idx == -1:
            exit_idx = last_idx
            exit_reason = "TIME_EXIT (Close)"
            
        exit_cost = spreads[exit_idx]
        sc_ex = ctx['sc'][exit_idx]
        lc_ex = ctx['lc'][exit_idx]
        sp_ex = ctx['sp'][exit_idx]
        lp_ex = ctx['lp'][exit_idx]
        
        pnl_pts = net_credit - exit_cost
        gross_pnl = round(pnl_pts * lot_size, 2)
        
        entry_turnover = (ctx['sc_entry'] + ctx['lc_entry'] + ctx['sp_entry'] + ctx['lp_entry']) * lot_size
        exit_turnover = (sc_ex + lc_ex + sp_ex + lp_ex) * lot_size
        slippage = round((entry_turnover + exit_turnover) * slippage_rate, 2)
        brokerage = brokerage_per_trade
        total_costs = round(brokerage + slippage, 2)
        net_pnl = round(gross_pnl - total_costs, 2)
        
        cap_a = round(cap_a + net_pnl, 2)
        cap_curve_a.append(cap_a)
        
        trades_a.append({
            'date': ctx['date'],
            'year': pd.to_datetime(ctx['date']).year,
            'expiry': ctx['expiry'].strftime('%Y-%m-%d') if hasattr(ctx['expiry'], 'strftime') else str(ctx['expiry'])[:10],
            'lot_size': lot_size,
            'atm': ctx['atm'],
            'net_credit_pts': round(net_credit, 2),
            'exit_time': str(times[exit_idx])[:19],
            'exit_reason': exit_reason,
            'pnl_pts': round(pnl_pts, 2),
            'gross_pnl': gross_pnl,
            'slippage': slippage,
            'brokerage': brokerage,
            'total_costs': total_costs,
            'net_pnl': net_pnl,
            'running_capital': cap_a
        })
        
    df_trades_a = pd.DataFrame(trades_a)
    df_trades_a.to_csv(model_a_csv, index=False)
    print(f"  * Model A simulation finished in {time.time()-t_a0:.2f}s ({len(df_trades_a)} trades).\n")
    
    # --------------------------------------------------------------------------
    # PART 2: EXTRACT & SIMULATE MODEL B (WEEKLY POSITIONAL 90% SL, 2 LOTS)
    # --------------------------------------------------------------------------
    print(">>> [2/3] Simulating MODEL B (Weekly Positional Iron Condor)...")
    t_b0 = time.time()
    
    df_spot = pd.read_parquet(spot_path)
    trading_days = sorted(df_spot.index.normalize().unique())
    trading_days_dates = [d.date() for d in trading_days]
    
    # Check if weekly spreads cache exists
    weekly_spread_contexts = []
    if cached_weekly_path.exists():
        print(f"  * Loading pre-computed weekly positional spreads from {cached_weekly_path}...")
        with open(cached_weekly_path, 'rb') as f:
            weekly_spread_contexts = pickle.load(f)
        print(f"  * Loaded {len(weekly_spread_contexts):,} weekly cycles from cache in {time.time()-t_b0:.2f}s.")
    else:
        dataset = ds.dataset(options_path, format='parquet')
        expiries_df = dataset.to_table(columns=['expiry']).to_pandas().drop_duplicates()
        unique_expiries = sorted([pd.to_datetime(e).date() for e in expiries_df['expiry'].dropna().unique()])
        
        cycles = []
        for i in range(len(unique_expiries)):
            exp_date = unique_expiries[i]
            prev_exp = unique_expiries[i-1] if i > 0 else None
            
            if prev_exp:
                entry_candidates = [d for d in trading_days_dates if d > prev_exp and d <= exp_date]
            else:
                entry_candidates = [d for d in trading_days_dates if d <= exp_date]
                
            if not entry_candidates:
                continue
            entry_date = entry_candidates[0]
            if entry_date >= exp_date:
                continue
                
            day_str = entry_date.strftime('%Y-%m-%d')
            ts_0920 = pd.to_datetime(f"{day_str} 09:20:00")
            ts_0921 = pd.to_datetime(f"{day_str} 09:21:00")
            if day_str not in df_spot.index:
                continue
            spot_day = df_spot.loc[day_str]
            if ts_0920 not in spot_day.index or ts_0921 not in spot_day.index:
                continue
            spot_0920 = spot_day.loc[ts_0920, 'close']
            atm = round(spot_0920 / 50.0) * 50.0
            
            cycles.append({
                'entry_date': entry_date,
                'expiry_date': exp_date,
                'atm': int(atm),
                'sc': atm + 150.0,
                'lc': atm + 450.0,
                'sp': atm - 150.0,
                'lp': atm - 450.0
            })
            
        print(f"  * Identified {len(cycles)} weekly cycles across 2020-2026.")
        
        years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
        cols = ['timestamp', 'expiry', 'strike_price', 'option_type', 'close']
        
        for yr in years:
            t_yr = time.time()
            yr_cycles = [c for c in cycles if c['entry_date'].year == yr]
            if not yr_cycles:
                continue
                
            filt = (
                (ds.field('timestamp') >= pd.to_datetime(f"{yr}-01-01 00:00:00")) & 
                (ds.field('timestamp') <= pd.to_datetime(f"{yr+1}-01-10 23:59:59"))
            )
            tbl = dataset.to_table(filter=filt, columns=cols)
            df_opt_yr = tbl.to_pandas()
            df_opt_yr['expiry_date'] = df_opt_yr['expiry'].dt.date
            exp_dict = {exp: grp for exp, grp in df_opt_yr.groupby('expiry_date')}
            
            yr_count = 0
            for c in yr_cycles:
                exp_d = c['expiry_date']
                ent_d = c['entry_date']
                if exp_d not in exp_dict:
                    continue
                sub = exp_dict[exp_d]
                needed = [c['sc'], c['lc'], c['sp'], c['lp']]
                legs_df = sub[sub['strike_price'].isin(needed)]
                if legs_df.empty:
                    continue
                legs_df = legs_df.drop_duplicates(subset=['timestamp', 'strike_price', 'option_type'])
                piv = legs_df.pivot(index='timestamp', columns=['strike_price', 'option_type'], values='close')
                req_cols = [(c['sc'], 'CE'), (c['lc'], 'CE'), (c['sp'], 'PE'), (c['lp'], 'PE')]
                if not all(col in piv.columns for col in req_cols):
                    continue
                sc_s = piv[(c['sc'], 'CE')].ffill()
                lc_s = piv[(c['lc'], 'CE')].ffill()
                sp_s = piv[(c['sp'], 'PE')].ffill()
                lp_s = piv[(c['lp'], 'PE')].ffill()
                m = pd.DataFrame({'sc': sc_s, 'lc': lc_s, 'sp': sp_s, 'lp': lp_s}).dropna()
                
                ts_entry = pd.to_datetime(f"{ent_d} 09:21:00")
                ts_exit = pd.to_datetime(f"{exp_d} 15:15:00")
                if ts_entry not in m.index:
                    near = m[(m.index >= ts_entry) & (m.index <= ts_entry + pd.Timedelta(minutes=5))]
                    if near.empty:
                        continue
                    actual_entry_ts = near.index[0]
                else:
                    actual_entry_ts = ts_entry
                    
                entry_row = m.loc[actual_entry_ts]
                sc_ent = float(entry_row['sc'])
                lc_ent = float(entry_row['lc'])
                sp_ent = float(entry_row['sp'])
                lp_ent = float(entry_row['lp'])
                net_credit = (sc_ent - lc_ent) + (sp_ent - lp_ent)
                if net_credit <= 5.0:
                    continue
                    
                track = m[(m.index > actual_entry_ts) & (m.index <= ts_exit)].copy()
                if track.empty:
                    continue
                track['spread'] = (track['sc'] - track['lc']) + (track['sp'] - track['lp'])
                
                weekly_spread_contexts.append({
                    'entry_date': ent_d,
                    'expiry_date': exp_d,
                    'year': yr,
                    'atm': c['atm'],
                    'sc_entry': sc_ent,
                    'lc_entry': lc_ent,
                    'sp_entry': sp_ent,
                    'lp_entry': lp_ent,
                    'net_credit_pts': net_credit,
                    'ts_exit': ts_exit,
                    'times': track.index.to_numpy(),
                    'spreads': track['spread'].to_numpy(),
                    'sc': track['sc'].to_numpy(),
                    'lc': track['lc'].to_numpy(),
                    'sp': track['sp'].to_numpy(),
                    'lp': track['lp'].to_numpy()
                })
                yr_count += 1
            print(f"    - Year {yr}: Processed {yr_count} weekly positional cycles in {time.time()-t_yr:.2f}s")
            
        try:
            with open(cached_weekly_path, 'wb') as f:
                pickle.dump(weekly_spread_contexts, f)
            print(f"  * Cached {len(weekly_spread_contexts)} weekly cycles to {cached_weekly_path}")
        except Exception as e:
            print(f"Cache save note: {e}")
            
    # Simulate Model B on weekly_spread_contexts
    trades_b = []
    cap_b = initial_capital
    cap_curve_b = [initial_capital]
    
    for ctx in weekly_spread_contexts:
        ent_d = ctx['entry_date']
        exp_d = ctx['expiry_date']
        lot_size = get_nifty_lot_size(ent_d) * lots_multiplier
        net_credit = ctx['net_credit_pts']
        
        tgt_thresh = net_credit * (1.0 - tgt_pct)  # 0.35x
        sl_thresh = net_credit * (1.0 + sl_pct)    # 1.90x
        
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
                exit_reason = "STOP_LOSS (-90%)"
                break
            elif sp_val <= tgt_thresh:
                exit_idx = i
                exit_reason = "TARGET (+65%)"
                break
            elif times[i] >= ctx['ts_exit']:
                exit_idx = i
                exit_reason = "TIME_EXIT (Expiry)"
                break
                
        if exit_idx == -1:
            exit_idx = last_idx
            exit_reason = "TIME_EXIT (Close)"
            
        exit_cost = spreads[exit_idx]
        sc_ex = ctx['sc'][exit_idx]
        lc_ex = ctx['lc'][exit_idx]
        sp_ex = ctx['sp'][exit_idx]
        lp_ex = ctx['lp'][exit_idx]
        
        pnl_pts = net_credit - exit_cost
        gross_pnl = round(pnl_pts * lot_size, 2)
        
        entry_turnover = (ctx['sc_entry'] + ctx['lc_entry'] + ctx['sp_entry'] + ctx['lp_entry']) * lot_size
        exit_turnover = (sc_ex + lc_ex + sp_ex + lp_ex) * lot_size
        slippage = round((entry_turnover + exit_turnover) * slippage_rate, 2)
        brokerage = brokerage_per_trade
        total_costs = round(brokerage + slippage, 2)
        net_pnl = round(gross_pnl - total_costs, 2)
        
        cap_b = round(cap_b + net_pnl, 2)
        cap_curve_b.append(cap_b)
        
        trades_b.append({
            'entry_date': str(ent_d),
            'expiry_date': str(exp_d),
            'year': ctx['year'],
            'lot_size': lot_size,
            'atm': ctx['atm'],
            'net_credit_pts': round(net_credit, 2),
            'exit_time': str(times[exit_idx])[:19],
            'exit_reason': exit_reason,
            'pnl_pts': round(pnl_pts, 2),
            'gross_pnl': gross_pnl,
            'slippage': slippage,
            'brokerage': brokerage,
            'total_costs': total_costs,
            'net_pnl': net_pnl,
            'running_capital': cap_b
        })
        
    df_trades_b = pd.DataFrame(trades_b)
    df_trades_b.to_csv(model_b_csv, index=False)
    print(f"  * Model B simulation finished ({len(df_trades_b)} weekly trades).\n")
    
    # --------------------------------------------------------------------------
    # PART 3: YEAR-BY-YEAR PERFORMANCE AGGREGATIONS
    # --------------------------------------------------------------------------
    years_all = sorted(list(set(df_trades_a['year'].unique()).union(set(df_trades_b['year'].unique()))))
    
    yearly_comparison = []
    for yr in years_all:
        grp_a = df_trades_a[df_trades_a['year'] == yr]
        grp_b = df_trades_b[df_trades_b['year'] == yr]
        
        # Model A stats
        tr_a = len(grp_a)
        gross_a = grp_a['gross_pnl'].sum() if tr_a > 0 else 0.0
        costs_a = grp_a['total_costs'].sum() if tr_a > 0 else 0.0
        net_a = grp_a['net_pnl'].sum() if tr_a > 0 else 0.0
        wr_a = ((grp_a['net_pnl'] > 0).mean() * 100.0) if tr_a > 0 else 0.0
        
        # Model B stats
        tr_b = len(grp_b)
        gross_b = grp_b['gross_pnl'].sum() if tr_b > 0 else 0.0
        costs_b = grp_b['total_costs'].sum() if tr_b > 0 else 0.0
        net_b = grp_b['net_pnl'].sum() if tr_b > 0 else 0.0
        wr_b = ((grp_b['net_pnl'] > 0).mean() * 100.0) if tr_b > 0 else 0.0
        
        yearly_comparison.append({
            'Year': yr,
            'Trades_A': tr_a,
            'WinRate_A': round(wr_a, 1),
            'GrossPnL_A': round(gross_a, 2),
            'Costs_A': round(costs_a, 2),
            'NetPnL_A': round(net_a, 2),
            'Trades_B': tr_b,
            'WinRate_B': round(wr_b, 1),
            'GrossPnL_B': round(gross_b, 2),
            'Costs_B': round(costs_b, 2),
            'NetPnL_B': round(net_b, 2),
            'NetDelta_B_minus_A': round(net_b - net_a, 2)
        })
        
    df_yearly = pd.DataFrame(yearly_comparison)
    
    # --------------------------------------------------------------------------
    # PART 4: EXECUTIVE METRICS & COMPARISON SCORECARD
    # --------------------------------------------------------------------------
    def compute_metrics(df_t, cap_curve, label):
        n_trades = len(df_t)
        gross = df_t['gross_pnl'].sum()
        costs = df_t['total_costs'].sum()
        net = df_t['net_pnl'].sum()
        final_cap = cap_curve[-1]
        roi_pct = (net / initial_capital) * 100.0
        
        wins = (df_t['net_pnl'] > 0).sum()
        losses = (df_t['net_pnl'] <= 0).sum()
        win_rate = (wins / n_trades * 100.0) if n_trades > 0 else 0.0
        
        sl_hits = df_t['exit_reason'].str.contains('STOP_LOSS').sum()
        tgt_hits = df_t['exit_reason'].str.contains('TARGET').sum()
        time_exits = df_t['exit_reason'].str.contains('TIME_EXIT').sum()
        
        cap_s = pd.Series(cap_curve)
        peak_s = cap_s.cummax()
        dd_s = cap_s - peak_s
        dd_pct_s = (dd_s / peak_s) * 100.0
        max_dd_rs = dd_s.min()
        max_dd_pct = dd_pct_s.min()
        
        gains = df_t[df_t['net_pnl'] > 0]['net_pnl'].sum()
        loss_abs = abs(df_t[df_t['net_pnl'] < 0]['net_pnl'].sum())
        profit_factor = (gains / loss_abs) if loss_abs > 0 else np.nan
        
        avg_win = df_t[df_t['net_pnl'] > 0]['net_pnl'].mean() if wins > 0 else 0.0
        avg_loss = df_t[df_t['net_pnl'] < 0]['net_pnl'].mean() if losses > 0 else 0.0
        
        ret_to_dd = (net / abs(max_dd_rs)) if abs(max_dd_rs) > 0 else 0.0
        
        return {
            'Model': label,
            'Trades': n_trades,
            'Win_Rate_Pct': round(win_rate, 2),
            'SL_Hits': sl_hits,
            'Target_Hits': tgt_hits,
            'Time_Exits': time_exits,
            'Gross_PnL': round(gross, 2),
            'Total_Friction': round(costs, 2),
            'Net_PnL': round(net, 2),
            'Final_Capital': round(final_cap, 2),
            'ROI_Pct': round(roi_pct, 2),
            'Max_DD_Rs': round(max_dd_rs, 2),
            'Max_DD_Pct': round(max_dd_pct, 2),
            'Profit_Factor': round(profit_factor, 2),
            'Avg_Win': round(avg_win, 2),
            'Avg_Loss': round(avg_loss, 2),
            'Return_to_DD': round(ret_to_dd, 2)
        }
        
    met_a = compute_metrics(df_trades_a, cap_curve_a, "Model A (Intraday)")
    met_b = compute_metrics(df_trades_b, cap_curve_b, "Model B (Weekly Positional)")
    
    df_metrics = pd.DataFrame([met_a, met_b])
    
    # Save comparison CSV
    with open(comparison_csv, 'w') as f:
        f.write("# =========================================================================\n")
        f.write("# OVERALL EXECUTIVE SCORECARD: MODEL A VS MODEL B (2020-2026)\n")
        f.write("# =========================================================================\n")
        df_metrics.to_csv(f, index=False)
        f.write("\n# =========================================================================\n")
        f.write("# YEAR-BY-YEAR BREAKDOWN: MODEL A VS MODEL B (2020-2026)\n")
        f.write("# =========================================================================\n")
        df_yearly.to_csv(f, index=False)
        
    print(f"Saved complete benchmark results to {comparison_csv}\n")
    
    # --------------------------------------------------------------------------
    # PRINT TERMINAL REPORTS
    # --------------------------------------------------------------------------
    print("=" * 145)
    print("                               YEAR-BY-YEAR PnL BREAKDOWN: MODEL A (INTRADAY) VS MODEL B (WEEKLY POSITIONAL)                           ")
    print("=" * 145)
    y_head = "{:<6} | {:>6} | {:>7} | {:>13} | {:>11} | {:>13} || {:>6} | {:>7} | {:>13} | {:>11} | {:>13} || {:>13}"
    y_row  = "{:<6} | {:>6} | {:>6.1f}% | {:>13.2f} | {:>11.2f} | {:>13.2f} || {:>6} | {:>6.1f}% | {:>13.2f} | {:>11.2f} | {:>13.2f} || {:>13.2f}"
    
    print(y_head.format(
        "Year", "Tr_A", "WinRt_A", "GrossPnL_A", "Costs_A", "NetPnL_A",
        "Tr_B", "WinRt_B", "GrossPnL_B", "Costs_B", "NetPnL_B", "Delta(B-A)"
    ))
    print("-" * 145)
    
    for _, r in df_yearly.iterrows():
        print(y_row.format(
            r['Year'], r['Trades_A'], r['WinRate_A'], r['GrossPnL_A'], r['Costs_A'], r['NetPnL_A'],
            r['Trades_B'], r['WinRate_B'], r['GrossPnL_B'], r['Costs_B'], r['NetPnL_B'], r['NetDelta_B_minus_A']
        ))
        
    print("-" * 145)
    total_tr_a = df_yearly['Trades_A'].sum()
    total_gr_a = df_yearly['GrossPnL_A'].sum()
    total_co_a = df_yearly['Costs_A'].sum()
    total_nt_a = df_yearly['NetPnL_A'].sum()
    
    total_tr_b = df_yearly['Trades_B'].sum()
    total_gr_b = df_yearly['GrossPnL_B'].sum()
    total_co_b = df_yearly['Costs_B'].sum()
    total_nt_b = df_yearly['NetPnL_B'].sum()
    
    print(y_head.format(
        "TOTAL", total_tr_a, f"{met_a['Win_Rate_Pct']:.1f}%", f"{total_gr_a:,.2f}", f"{total_co_a:,.2f}", f"{total_nt_a:,.2f}",
        total_tr_b, f"{met_b['Win_Rate_Pct']:.1f}%", f"{total_gr_b:,.2f}", f"{total_co_b:,.2f}", f"{total_nt_b:,.2f}", f"{total_nt_b - total_nt_a:,.2f}"
    ))
    print("=" * 145 + "\n")
    
    # Overall Executive Summary Table
    print("=" * 115)
    print("                              EXECUTIVE QUANTITATIVE SCORECARD: SIDE-BY-SIDE                                       ")
    print("=" * 115)
    s_head = "{:<32} | {:>36} | {:>36}"
    s_row  = "{:<32} | {:>36} | {:>36}"
    print(s_head.format("Metric", "Model A (Intraday, 2 Lots)", "Model B (Weekly Positional, 2 Lots)"))
    print("-" * 115)
    
    print(s_row.format("Starting Capital", f"Rs. {initial_capital:,.2f}", f"Rs. {initial_capital:,.2f}"))
    print(s_row.format("Final Capital", f"Rs. {met_a['Final_Capital']:,.2f}", f"Rs. {met_b['Final_Capital']:,.2f}"))
    print(s_row.format("Net Realized Alpha", f"Rs. {met_a['Net_PnL']:,.2f} ({met_a['ROI_Pct']:+.2f}%)", f"Rs. {met_b['Net_PnL']:,.2f} ({met_b['ROI_Pct']:+.2f}%)"))
    print(s_row.format("Gross PnL (Alpha before costs)", f"Rs. {met_a['Gross_PnL']:,.2f}", f"Rs. {met_b['Gross_PnL']:,.2f}"))
    print(s_row.format("Total Friction (Slippage/Fees)", f"Rs. {met_a['Total_Friction']:,.2f}", f"Rs. {met_b['Total_Friction']:,.2f}"))
    print(s_row.format("Friction Reduction (%)", "Baseline (1,278 Trades)", f"-{(1.0 - met_b['Total_Friction']/met_a['Total_Friction'])*100:.1f}% Less Friction"))
    print(s_row.format("Total Executed Trades", f"{met_a['Trades']} trades", f"{met_b['Trades']} weekly cycles"))
    print(s_row.format("Win Rate", f"{met_a['Win_Rate_Pct']:.2f}%", f"{met_b['Win_Rate_Pct']:.2f}%"))
    print(s_row.format("Profit Factor", f"{met_a['Profit_Factor']:.2f}", f"{met_b['Profit_Factor']:.2f}"))
    print(s_row.format("Exit Profile (SL / Target / Time)", f"{met_a['SL_Hits']} / {met_a['Target_Hits']} / {met_a['Time_Exits']}", f"{met_b['SL_Hits']} / {met_b['Target_Hits']} / {met_b['Time_Exits']}"))
    print(s_row.format("Average Win / Average Loss", f"Rs. {met_a['Avg_Win']:,.2f} / Rs. {met_a['Avg_Loss']:,.2f}", f"Rs. {met_b['Avg_Win']:,.2f} / Rs. {met_b['Avg_Loss']:,.2f}"))
    print(s_row.format("Maximum Drawdown (Rs & %)", f"Rs. {met_a['Max_DD_Rs']:,.2f} ({met_a['Max_DD_Pct']:.2f}%)", f"Rs. {met_b['Max_DD_Rs']:,.2f} ({met_b['Max_DD_Pct']:.2f}%)"))
    print(s_row.format("Return-to-Drawdown Ratio", f"{met_a['Return_to_DD']:.2f}", f"{met_b['Return_to_DD']:.2f}"))
    print("=" * 115 + "\n")
    
    print(f"Total Execution Runtime: {time.time()-t_start:.2f} seconds.")
    print("=" * 120)

if __name__ == "__main__":
    run_model_a_vs_model_b()
