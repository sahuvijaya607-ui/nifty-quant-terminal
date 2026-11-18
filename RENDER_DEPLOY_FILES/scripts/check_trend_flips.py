"""
scripts/check_trend_flips.py
Checks how many of the 967 Vikalp 2 trades experienced a 5-min Trend Flip before exit,
and categorizes them by original outcome (Target vs SL vs Time Exit).
"""

import pandas as pd
import numpy as np

def run_check():
    # 1. Load spot data and build 5m bars
    spot_df = pd.read_parquet('data/nifty_spot_master_2020_2026_1min.parquet')
    df_5m = spot_df.resample('5min', origin='start_day', offset='15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_5m['ema9'] = df_5m['close'].ewm(span=9, adjust=False).mean()
    df_5m['ema20'] = df_5m['close'].ewm(span=20, adjust=False).mean()
    df_5m['ema50'] = df_5m['close'].ewm(span=50, adjust=False).mean()
    df_5m['is_green'] = df_5m['close'] >= df_5m['open']
    df_5m['is_red'] = df_5m['close'] <= df_5m['open']
    df_5m['bar_end'] = df_5m.index + pd.Timedelta(minutes=5)

    df_5m['is_bull'] = (df_5m['close'] > df_5m['ema50']) & (df_5m['ema9'] >= df_5m['ema20']) & (df_5m['close'] > df_5m['ema9']) & df_5m['is_green']
    df_5m['is_bear'] = (df_5m['close'] < df_5m['ema50']) & (df_5m['ema9'] <= df_5m['ema20']) & (df_5m['close'] < df_5m['ema9']) & df_5m['is_red']

    # 2. Load Vikalp 2 trades
    trades = pd.read_csv('results/vikalp2_full_967_trades_postmortem.csv')
    trades['entry_time'] = pd.to_datetime(trades['entry_time'])
    trades['exit_time'] = pd.to_datetime(trades['exit_time'])

    print(f"Loaded {len(trades)} trades and {len(df_5m)} 5-minute bars.")

    # Counters
    flip_count_full_signal = 0 # Opposite is_bull/is_bear
    flip_count_ema_cross = 0   # 9 EMA cross 20 EMA
    flip_count_ema50_break = 0 # Close cross 50 EMA

    early_exit_on_target_trades = 0
    early_exit_on_sl_trades = 0
    early_exit_on_time_trades = 0

    results = []

    for idx, t in trades.iterrows():
        t_in = t['entry_time']
        t_out = t['exit_time']
        strat = t['strategy']
        orig_reason = t['exit_reason']
        orig_pnl = t['net_pnl']

        # Get 5m bars during this trade's duration
        bars = df_5m[(df_5m['bar_end'] > t_in) & (df_5m['bar_end'] < t_out)]

        had_signal_flip = False
        first_signal_flip_time = None
        spot_at_flip = None

        if strat == 'BULL_PUT_SPREAD':
            for _, b in bars.iterrows():
                # Signal flip = is_bear is True
                if b['is_bear']:
                    had_signal_flip = True
                    first_signal_flip_time = b['bar_end']
                    spot_at_flip = b['close']
                    break
        else: # BEAR_CALL_SPREAD
            for _, b in bars.iterrows():
                # Signal flip = is_bull is True
                if b['is_bull']:
                    had_signal_flip = True
                    first_signal_flip_time = b['bar_end']
                    spot_at_flip = b['close']
                    break

        if had_signal_flip:
            flip_count_full_signal += 1
            if orig_reason == 'TARGET':
                early_exit_on_target_trades += 1
            elif orig_reason == 'STOP_LOSS':
                early_exit_on_sl_trades += 1
            elif orig_reason == 'TIME_EXIT':
                early_exit_on_time_trades += 1

            results.append({
                'trade_no': t['trade_no'],
                'date': t['date'],
                'strategy': strat,
                'entry_time': t_in,
                'orig_exit_time': t_out,
                'flip_time': first_signal_flip_time,
                'orig_reason': orig_reason,
                'orig_pnl': orig_pnl,
                'spot_entry': t['spot_entry'],
                'spot_orig_exit': t['spot_exit'],
                'spot_at_flip': spot_at_flip,
                'net_credit': t['net_credit'],
                'target_expiry': t['target_expiry'],
                'lot_size': t['lot_size'],
                'short_strike': t['short_strike'],
                'long_strike': t['long_strike'],
                'option_type': t['option_type']
            })

    print("\n" + "=" * 90)
    print("ANALYSIS OF 5-MIN TREND FLIPS (OPPOSITE SIGNAL GENERATED DURING TRADE)")
    print("=" * 90)
    print(f"Total Trades with Trend Flip BEFORE original exit: {flip_count_full_signal} / {len(trades)} ({flip_count_full_signal/len(trades)*100:.1f}%)")
    print(f"  • Of Original TARGET (+40% Win) Trades : {early_exit_on_target_trades} trades had a flip! (WHIPSAW RISK)")
    print(f"  • Of Original STOP_LOSS Trades         : {early_exit_on_sl_trades} trades had a flip! (POTENTIAL SAVE)")
    print(f"  • Of Original TIME_EXIT Trades         : {early_exit_on_time_trades} trades had a flip!")

    df_flips = pd.DataFrame(results)
    df_flips.to_csv('results/trades_with_trend_flips.csv', index=False)
    print("Saved flip trades to results/trades_with_trend_flips.csv")

if __name__ == '__main__':
    run_check()
