import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow.dataset as ds

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.nifty_lot_size import get_nifty_lot_size

def test_model_b():
    t0 = time.time()
    spot_path = Path("data/nifty_spot_master_2020_2026_1min.parquet")
    options_path = Path("data/nifty_options_master_2020_2026_1min.parquet")
    
    df_spot = pd.read_parquet(spot_path)
    trading_days = sorted(df_spot.index.normalize().unique())
    trading_days_dates = [d.date() for d in trading_days]
    
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
            'entry_day': entry_date.strftime('%A'),
            'expiry_day': exp_date.strftime('%A'),
            'atm': int(atm),
            'sc': atm + 150.0,
            'lc': atm + 450.0,
            'sp': atm - 150.0,
            'lp': atm - 450.0
        })
        
    print(f"Mapped {len(cycles)} weekly cycles across 2020-2026 in {time.time()-t0:.2f}s.")
    
    # Process cycles year-by-year
    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    cols = ['timestamp', 'expiry', 'strike_price', 'option_type', 'close']
    
    all_b_trades = []
    initial_capital = 200000.0
    running_cap = initial_capital
    lots_multiplier = 2
    sl_pct = 0.90
    
    for yr in years:
        t_yr = time.time()
        # In a weekly cycle, entry might be late in yr-1 or exit early in yr+1.
        # But filter by cycle entry_date.year == yr
        yr_cycles = [c for c in cycles if c['entry_date'].year == yr]
        if not yr_cycles:
            continue
            
        min_date = yr_cycles[0]['entry_date']
        max_date = yr_cycles[-1]['expiry_date']
        
        filt = (
            (ds.field('timestamp') >= pd.to_datetime(f"{min_date} 00:00:00")) & 
            (ds.field('timestamp') <= pd.to_datetime(f"{max_date} 23:59:59"))
        )
        tbl = dataset.to_table(filter=filt, columns=cols)
        df_opt_yr = tbl.to_pandas()
        
        yr_valid = 0
        for c in yr_cycles:
            exp_d = c['expiry_date']
            ent_d = c['entry_date']
            lot_size = get_nifty_lot_size(ent_d) * lots_multiplier
            
            sub = df_opt_yr[df_opt_yr['expiry'].dt.date == exp_d]
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
            ts_expiry_exit = pd.to_datetime(f"{exp_d} 15:15:00")
            
            if ts_entry not in m.index:
                near_bars = m[(m.index >= ts_entry) & (m.index <= ts_entry + pd.Timedelta(minutes=5))]
                if near_bars.empty:
                    continue
                actual_entry_ts = near_bars.index[0]
            else:
                actual_entry_ts = ts_entry
                
            entry_row = m.loc[actual_entry_ts]
            sc_entry = float(entry_row['sc'])
            lc_entry = float(entry_row['lc'])
            sp_entry = float(entry_row['sp'])
            lp_entry = float(entry_row['lp'])
            
            net_credit_pts = (sc_entry - lc_entry) + (sp_entry - lp_entry)
            if net_credit_pts <= 5.0:
                continue
                
            track_df = m[(m.index > actual_entry_ts) & (m.index <= ts_expiry_exit)].copy()
            if track_df.empty:
                continue
                
            track_df['spread'] = (track_df['sc'] - track_df['lc']) + (track_df['sp'] - track_df['lp'])
            
            tgt_thresh = net_credit_pts * 0.35
            sl_thresh = net_credit_pts * (1.0 + sl_pct)
            
            spreads = track_df['spread'].to_numpy()
            times = track_df.index.to_numpy()
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
                elif times[i] >= ts_expiry_exit:
                    exit_idx = i
                    exit_reason = "TIME_EXIT (Expiry)"
                    break
                    
            if exit_idx == -1:
                exit_idx = last_idx
                exit_reason = "TIME_EXIT (Close)"
                
            exit_cost = spreads[exit_idx]
            exit_time = times[exit_idx]
            sc_ex = track_df['sc'].to_numpy()[exit_idx]
            lc_ex = track_df['lc'].to_numpy()[exit_idx]
            sp_ex = track_df['sp'].to_numpy()[exit_idx]
            lp_ex = track_df['lp'].to_numpy()[exit_idx]
            
            pnl_pts = net_credit_pts - exit_cost
            gross_pnl = round(pnl_pts * lot_size, 2)
            
            brokerage = 0.0
            entry_turnover = (sc_entry + lc_entry + sp_entry + lp_entry) * lot_size
            exit_turnover = (sc_ex + lc_ex + sp_ex + lp_ex) * lot_size
            slippage = round((entry_turnover + exit_turnover) * 0.0015, 2)
            total_costs = round(brokerage + slippage, 2)
            net_pnl = round(gross_pnl - total_costs, 2)
            
            running_cap = round(running_cap + net_pnl, 2)
            
            all_b_trades.append({
                'entry_date': ent_d,
                'expiry_date': exp_d,
                'year': yr,
                'lot_size': lot_size,
                'atm': c['atm'],
                'net_credit_pts': round(net_credit_pts, 2),
                'exit_time': str(exit_time)[:19],
                'exit_reason': exit_reason,
                'gross_pnl': gross_pnl,
                'total_costs': total_costs,
                'net_pnl': net_pnl,
                'running_capital': running_cap
            })
            yr_valid += 1
            
        print(f"Year {yr}: Simulated {yr_valid} weekly cycles in {time.time()-t_yr:.2f}s")
        
    df_b = pd.DataFrame(all_b_trades)
    print("\nMODEL B (WEEKLY POSITIONAL) RESULTS:")
    print(f"Total Cycles Simulated: {len(df_b)}")
    print(f"Gross PnL: Rs. {df_b['gross_pnl'].sum():,.2f}")
    print(f"Total Costs: Rs. {df_b['total_costs'].sum():,.2f}")
    print(f"Net PnL: Rs. {df_b['net_pnl'].sum():,.2f}")
    print(f"Final Capital: Rs. {running_cap:,.2f}")
    print(f"Win Rate: {(df_b['net_pnl'] > 0).mean()*100:.2f}%")
    print("\nExit Breakdown:")
    print(df_b['exit_reason'].value_counts())
    
    print("\nYear-by-Year Breakdown:")
    for yr, grp in df_b.groupby('year'):
        g = grp['gross_pnl'].sum()
        c = grp['total_costs'].sum()
        n = grp['net_pnl'].sum()
        wr = (grp['net_pnl'] > 0).mean() * 100
        print(f"  {yr}: Cycles={len(grp)}, Gross=Rs. {g:>10,.2f}, Costs=Rs. {c:>8,.2f}, Net=Rs. {n:>10,.2f}, WinRate={wr:.1f}%")

if __name__ == "__main__":
    test_model_b()
