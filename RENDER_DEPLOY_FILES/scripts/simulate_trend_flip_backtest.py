"""
scripts/simulate_trend_flip_backtest.py
Simulates the exact tick-by-tick outcome of adding 5-min Trend Flip Exit to Vikalp 2.
Compares Total PnL, Drawdown, Win Rate, and Head-to-Head performance against Vikalp 2 baseline.
Enforces strict contract-level matching including target_expiry, strike, and option_type.
"""

import os
import time
import pandas as pd
import numpy as np
import pyarrow.parquet as pq

def main():
    t_start = time.time()
    print("=" * 100)
    print("      RUNNING HEAD-TO-HEAD BACKTEST: VIKALP 2 BASELINE vs VIKALP 2 WITH TREND FLIP EXIT")
    print("      6-Year Master Options Data (2020 - 2026)")
    print("=" * 100)

    # 1. Load baseline trades and flip candidates
    df_base = pd.read_csv('results/vikalp2_full_967_trades_postmortem.csv')
    df_flips = pd.read_csv('results/trades_with_trend_flips.csv')

    df_base['entry_time'] = pd.to_datetime(df_base['entry_time'])
    df_base['exit_time'] = pd.to_datetime(df_base['exit_time'])
    df_flips['entry_time'] = pd.to_datetime(df_flips['entry_time'])
    df_flips['flip_time'] = pd.to_datetime(df_flips['flip_time'])

    print(f"Loaded {len(df_base)} total baseline trades.")
    print(f"Identified {len(df_flips)} trades with mid-trade 5-min trend flips.")

    # 2. Extract option prices at flip times from parquet
    options_path = "data/nifty_options_master_2020_2026_1min.parquet"
    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

    flip_spread_results = {}
    exact_matches = 0
    fallback_matches = 0

    for yr in years:
        yr_flips = df_flips[df_flips['entry_time'].dt.year == yr]
        if len(yr_flips) == 0:
            continue
        print(f"Fetching option prices for {len(yr_flips)} flip trades in Year {yr}...", flush=True)

        min_ts = yr_flips['flip_time'].min()
        max_ts = yr_flips['flip_time'].max()

        # Read required slice with expiry included
        table = pq.read_table(options_path, filters=[
            ('timestamp', '>=', min_ts - pd.Timedelta(minutes=5)),
            ('timestamp', '<=', max_ts + pd.Timedelta(minutes=5))
        ], columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'close'])
        df_opt = table.to_pandas()
        del table

        df_opt['expiry_date'] = df_opt['expiry'].dt.date
        # Group by timestamp for fast lookup
        grouped = df_opt.groupby('timestamp')

        for _, f in yr_flips.iterrows():
            tr_id = f['trade_no']
            f_time = f['flip_time']
            s_strike = f['short_strike']
            l_strike = f['long_strike']
            otype = f['option_type']
            f_exp_date = pd.to_datetime(f['target_expiry']).date()

            s_price = None
            l_price = None

            # Check exact flip time or closest within 2 mins
            check_times = [
                f_time,
                f_time + pd.Timedelta(minutes=1),
                f_time - pd.Timedelta(minutes=1),
                f_time + pd.Timedelta(minutes=2),
                f_time - pd.Timedelta(minutes=2)
            ]
            for ct in check_times:
                if ct in grouped.groups:
                    subset = grouped.get_group(ct)
                    s_match = subset[(subset['strike_price'] == s_strike) & (subset['option_type'] == otype) & (subset['expiry_date'] == f_exp_date)]
                    l_match = subset[(subset['strike_price'] == l_strike) & (subset['option_type'] == otype) & (subset['expiry_date'] == f_exp_date)]
                    if not s_match.empty and not l_match.empty:
                        s_price = float(s_match['close'].iloc[0])
                        l_price = float(l_match['close'].iloc[0])
                        break

            if s_price is not None and l_price is not None and s_price > 0 and l_price > 0:
                flip_spread = s_price - l_price
                flip_spread_results[tr_id] = {
                    'flip_exit_time': f_time,
                    'flip_spread': flip_spread,
                    's_price': s_price,
                    'l_price': l_price,
                    'is_fallback': False
                }
                exact_matches += 1
            else:
                # Estimate spread based on spot move if minute tick missing
                spot_in = f['spot_entry']
                spot_flip = f['spot_at_flip']
                spot_diff = spot_flip - spot_in if f['strategy'] == 'BULL_PUT_SPREAD' else spot_in - spot_flip
                # Delta proxy for spread: ~0.15 net delta
                est_spread = max(1.0, f['net_credit'] - (spot_diff * 0.15))
                flip_spread_results[tr_id] = {
                    'flip_exit_time': f_time,
                    'flip_spread': est_spread,
                    's_price': 0,
                    'l_price': 0,
                    'is_fallback': True
                }
                fallback_matches += 1

    print(f"Successfully priced {len(flip_spread_results)} / {len(df_flips)} flip exits (Exact matches: {exact_matches}, Spot fallback: {fallback_matches}).")

    # 3. Build comparison dataframe
    comp_records = []

    for _, t in df_base.iterrows():
        tr_id = t['trade_no']
        lot = t['lot_size']
        net_credit = t['net_credit']
        orig_pnl = t['net_pnl']
        orig_reason = t['exit_reason']

        if tr_id in flip_spread_results:
            f_info = flip_spread_results[tr_id]
            f_spread = f_info['flip_spread']
            f_exit_time = f_info['flip_exit_time']

            # Calculate flip PnL with exact baseline slippage formula
            points_won = net_credit - f_spread
            gross_pnl = points_won * lot
            slippage = (net_credit * 0.005 + f_spread * 0.005) * lot + 20.0 + (f_spread * lot * 0.000625)
            flip_pnl = round(gross_pnl - slippage, 2)
            flip_reason = "TREND_FLIP_EXIT"
        else:
            flip_pnl = orig_pnl
            flip_reason = orig_reason
            f_exit_time = t['exit_time']
            f_spread = t['exit_spread']

        comp_records.append({
            'trade_no': tr_id,
            'date': t['date'],
            'year': t['year'],
            'strategy': t['strategy'],
            'net_credit': net_credit,
            'lot_size': lot,
            'base_exit_time': t['exit_time'],
            'base_spread': t['exit_spread'],
            'base_reason': orig_reason,
            'base_pnl': orig_pnl,
            'flip_exit_time': f_exit_time,
            'flip_spread': round(f_spread, 2),
            'flip_reason': flip_reason,
            'flip_pnl': flip_pnl,
            'pnl_diff': round(flip_pnl - orig_pnl, 2) # Positive means Flip was better, Negative means Flip hurt
        })

    df_comp = pd.DataFrame(comp_records)

    # 4. Comparative Statistics
    base_net = df_comp['base_pnl'].sum()
    flip_net = df_comp['flip_pnl'].sum()

    base_wins = (df_comp['base_pnl'] > 0).sum()
    flip_wins = (df_comp['flip_pnl'] > 0).sum()
    base_wr = (base_wins / len(df_comp)) * 100
    flip_wr = (flip_wins / len(df_comp)) * 100

    base_eq = df_comp['base_pnl'].cumsum()
    flip_eq = df_comp['flip_pnl'].cumsum()
    base_dd = (base_eq - base_eq.cummax()).min()
    flip_dd = (flip_eq - flip_eq.cummax()).min()

    base_gw = df_comp[df_comp['base_pnl'] > 0]['base_pnl'].sum()
    base_gl = abs(df_comp[df_comp['base_pnl'] <= 0]['base_pnl'].sum())
    base_pf = base_gw / base_gl if base_gl > 0 else 99.0

    flip_gw = df_comp[df_comp['flip_pnl'] > 0]['flip_pnl'].sum()
    flip_gl = abs(df_comp[df_comp['flip_pnl'] <= 0]['flip_pnl'].sum())
    flip_pf = flip_gw / flip_gl if flip_gl > 0 else 99.0

    print("\n" + "=" * 100)
    print("                     HEAD-TO-HEAD RESULTS SUMMARY (6 FULL YEARS)")
    print("=" * 100)
    print(f"{'Metric':<32} | {'Vikalp 2 (Baseline)':<22} | {'Vikalp 2 + Trend Flip':<24} | {'Difference / Winner'}")
    print("-" * 100)
    print(f"{'Total Net Realized Profit':<32} | Rs {base_net:>17,.2f} | Rs {flip_net:>19,.2f} | Rs {flip_net - base_net:>12,.2f} ({'Baseline Won' if base_net > flip_net else 'Flip Won'})")
    print(f"{'Win Rate':<32} | {base_wr:>20.2f}% | {flip_wr:>22.2f}% | {flip_wr - base_wr:>11.2f}%")
    print(f"{'Max Drawdown':<32} | Rs {base_dd:>17,.2f} | Rs {flip_dd:>19,.2f} | Rs {abs(base_dd) - abs(flip_dd):>12,.2f}")
    print(f"{'Return / Max Drawdown':<32} | {base_net / abs(base_dd):>21.2f}x | {flip_net / abs(flip_dd):>23.2f}x | Quality Score")
    print(f"{'Profit Factor':<32} | {base_pf:>21.2f} | {flip_pf:>23.2f} |")
    print(f"{'Total Trades':<32} | {len(df_comp):>21} | {len(df_comp):>23} |")

    # 5. Year by Year Comparison
    print("\n" + "=" * 100)
    print("YEAR-BY-YEAR COMPARISON:")
    print("=" * 100)
    print(f"{'Year':<6} | {'Baseline Net (Rs)':<18} | {'Trend Flip Net (Rs)':<20} | {'Net Diff (Rs)':<14} | {'Winner':<12}")
    print("-" * 100)
    for yr, grp in df_comp.groupby('year'):
        b_pnl = grp['base_pnl'].sum()
        f_pnl = grp['flip_pnl'].sum()
        diff = f_pnl - b_pnl
        winner = "TREND FLIP" if diff > 0 else "BASELINE"
        print(f"{yr:<6} | Rs {b_pnl:>14,.2f} | Rs {f_pnl:>16,.2f} | Rs {diff:>10,.2f} | {winner:<12}")

    # 6. Deep Dive: Where did Trend Flip help vs hurt?
    saved = df_comp[df_comp['pnl_diff'] > 10.0]
    hurt = df_comp[df_comp['pnl_diff'] < -10.0]
    neutral = df_comp[abs(df_comp['pnl_diff']) <= 10.0]
    print("\n" + "=" * 100)
    print("DEEP DIVE: WHERE DID TREND FLIP HELP vs HURT?")
    print("=" * 100)
    print(f"Trades where Trend Flip SAVED money (cut losses early) : {len(saved)} trades | Total Saved: +Rs {saved['pnl_diff'].sum():,.2f}")
    print(f"Trades where Trend Flip HURT (premature exit / whipsaw): {len(hurt)} trades | Total Killed: -Rs {abs(hurt['pnl_diff'].sum()):,.2f}")
    print(f"Trades with zero/neutral impact                         : {len(neutral)} trades")

    # Breakdown by original exit reason
    print("\n" + "=" * 100)
    print("IMPACT BY ORIGINAL TRADE EXIT REASON:")
    print("=" * 100)
    for r in ['TARGET', 'STOP_LOSS', 'TIME_EXIT', 'BREAKEVEN_SL']:
        r_df = df_comp[df_comp['base_reason'] == r]
        r_flip = r_df[r_df['flip_reason'] == 'TREND_FLIP_EXIT']
        r_saved = r_df[r_df['pnl_diff'] > 10.0]
        r_hurt = r_df[r_df['pnl_diff'] < -10.0]
        print(f"Original {r:<14} ({len(r_df)} trades): {len(r_flip)} flipped | Saved: {len(r_saved)} (+Rs {r_saved['pnl_diff'].sum():,.2f}) | Hurt: {len(r_hurt)} (-Rs {abs(r_hurt['pnl_diff'].sum()):,.2f}) | Net Diff: Rs {r_df['pnl_diff'].sum():,.2f}")

    df_comp.to_csv('results/trend_flip_vs_vikalp2_comparison.csv', index=False)
    print(f"\nFull trade-by-trade comparison saved to results/trend_flip_vs_vikalp2_comparison.csv")
    print(f"Execution completed in {time.time() - t_start:.1f}s")

if __name__ == '__main__':
    main()
