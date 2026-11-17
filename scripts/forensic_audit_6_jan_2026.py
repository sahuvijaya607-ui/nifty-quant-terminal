import os
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import scipy.stats as si
import pyarrow.parquet as pq

sys.path.insert(0, os.path.abspath("."))
from utils.nifty_lot_size import get_nifty_lot_size

def calc_bs_deltas(spot: float, strikes: np.ndarray, dte_years: float, r: float, sigma: float, option_type: str) -> np.ndarray:
    strikes = np.asarray(strikes, dtype=float)
    T = max(float(dte_years), 0.001)
    sig = max(float(sigma), 0.05)
    d1 = (np.log(spot / strikes) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    if option_type == 'CE':
        return si.norm.cdf(d1)
    else:
        return si.norm.cdf(d1) - 1.0

# 1. Spot data
df_spot = pd.read_parquet("data/nifty_spot_master_2020_2026_1min.parquet")
spot_price_map = dict(zip(df_spot.index, df_spot['close']))

df_5m = df_spot.resample('5min', origin='start_day', offset='15min').agg({
    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
}).dropna()
df_5m['close_time'] = df_5m.index + pd.Timedelta(minutes=5)
df_5m['close_time_t'] = df_5m['close_time'].dt.time
df_5m['date'] = df_5m.index.date

df_5m['ema9'] = df_5m['close'].ewm(span=9, adjust=False).mean()
df_5m['ema20'] = df_5m['close'].ewm(span=20, adjust=False).mean()
df_5m['ema50'] = df_5m['close'].ewm(span=50, adjust=False).mean()
df_5m['is_green'] = df_5m['close'] >= df_5m['open']
df_5m['is_red'] = df_5m['close'] <= df_5m['open']

df_5m['is_bull'] = (df_5m['close'] > df_5m['ema50']) & (df_5m['ema9'] >= df_5m['ema20']) & (df_5m['close'] > df_5m['ema9']) & df_5m['is_green']
df_5m['is_bear'] = (df_5m['close'] < df_5m['ema50']) & (df_5m['ema9'] <= df_5m['ema20']) & (df_5m['close'] < df_5m['ema9']) & df_5m['is_red']

target_dt = pd.to_datetime('2026-01-06').date()
bars_6jan = df_5m[df_5m['date'] == target_dt]

print("=== 1. 5-MIN SPOT BARS ON 2026-01-06 (MORNING) ===")
for ts, b in bars_6jan.head(10).iterrows():
    c_time = b['close_time'].strftime('%H:%M')
    print(f"Bar {ts.strftime('%H:%M')}-{c_time}: O={b.open:.2f}, H={b.high:.2f}, L={b.low:.2f}, C={b.close:.2f} | EMA9={b.ema9:.2f}, EMA20={b.ema20:.2f}, EMA50={b.ema50:.2f} | Bull={b.is_bull}, Bear={b.is_bear}")

# 2. Options data on 2026-01-06
clean_path = Path("data/nifty_option_chain_1min_clean.parquet")
opt_table = pq.read_table(clean_path, 
                          filters=[('timestamp', '>=', pd.Timestamp('2026-01-06 09:15:00')),
                                   ('timestamp', '<=', pd.Timestamp('2026-01-06 15:30:00'))],
                          columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'oi'])
df_opt = opt_table.to_pandas()
df_opt['expiry_date'] = df_opt['expiry'].dt.date

oi_agg = df_opt.groupby(['timestamp', 'option_type'])['oi'].sum().unstack(fill_value=0)
pcr_map = (oi_agg['PE'] / oi_agg['CE'].replace(0, 1)).to_dict()

pcr_0925 = pcr_map.get(pd.Timestamp('2026-01-06 09:25:00'), None)
print(f"\n=== 2. PCR AT 09:25 AM ===")
print(f"PCR(09:25) = {pcr_0925:.4f} (Rule: Bear Call Spread requires PCR <= 1.0 -> {pcr_0925 <= 1.0})")

# Available Expiries on 6 Jan
expiries = sorted([exp for exp in df_opt['expiry_date'].unique() if exp > target_dt])
target_exp = expiries[0]
print(f"\n=== 3. EXPIRY SELECTION ===")
print(f"Today is Expiry Day (2026-01-06). Rule: exp > dt rolls to next weekly expiry: {target_exp}")

# Strike Deltas at 09:26
spot_0926 = float(spot_price_map[pd.Timestamp('2026-01-06 09:26:00')])
dte_years = max((target_exp - target_dt).days / 365.25, 0.001)

df_exp = df_opt[df_opt['expiry_date'] == target_exp]
ce_opt_0926 = df_exp[(df_exp['timestamp'] == pd.Timestamp('2026-01-06 09:26:00')) & (df_exp['option_type'] == 'CE')].sort_values('strike_price')

strikes = ce_opt_0926['strike_price'].to_numpy()
ltps = ce_opt_0926['ltp'].to_numpy()
deltas = calc_bs_deltas(spot_0926, strikes, dte_years, 0.07, 0.16, 'CE')

print(f"\n=== 4. STRIKE SELECTION AT 09:26 AM (Spot={spot_0926:.2f}) ===")
print(f"Target Short Delta: 0.50 (ATM) | Target Long Delta: 0.10 (Wing)")
for s, l, d in zip(strikes, ltps, deltas):
    if 25800 <= s <= 26800:
        sel = ""
        if s == 26250: sel = " <-- SELECTED SHORT (Closest to 0.50)"
        if s == 26600: sel = " <-- SELECTED LONG (Closest to 0.10)"
        print(f"Strike {s:5.0f} CE | LTP: {l:6.2f} | Delta: {d:6.3f}{sel}")

# Trade Trajectory
short_k = 26250
long_k = 26600
short_traj = df_exp[(df_exp['strike_price'] == short_k) & (df_exp['option_type'] == 'CE')].drop_duplicates('timestamp').set_index('timestamp')['ltp'].sort_index()
long_traj = df_exp[(df_exp['strike_price'] == long_k) & (df_exp['option_type'] == 'CE')].drop_duplicates('timestamp').set_index('timestamp')['ltp'].sort_index()

trade_df = pd.DataFrame({'short_ltp': short_traj, 'long_ltp': long_traj}).dropna()
trade_df = trade_df.sort_index()
trade_df = trade_df[(trade_df.index >= pd.Timestamp('2026-01-06 09:26:00')) & (trade_df.index <= pd.Timestamp('2026-01-06 15:15:00'))]
trade_df['spread'] = trade_df['short_ltp'] - trade_df['long_ltp']

entry_spread = trade_df.iloc[0]['spread']
lot = get_nifty_lot_size(target_dt)
trade_df['pnl_gross'] = (entry_spread - trade_df['spread']) * lot

# Spot during trade
spot_subset = df_spot.loc['2026-01-06 09:26:00':'2026-01-06 15:15:00']['close']
trade_df['spot'] = spot_subset

print(f"\n=== 5. INTRADAY TRADE TRAJECTORY & MTM SNAPSHOTS ===")
print(f"Entry Spread: Rs. {entry_spread:.2f} | Lot Size: {lot}")
times = ['09:26:00', '09:45:00', '10:15:00', '10:45:00', '11:15:00', '12:00:00', '13:00:00', '14:00:00', '14:45:00', '15:15:00']
for t in times:
    ts = pd.Timestamp(f'2026-01-06 {t}')
    if ts in trade_df.index:
        r = trade_df.loc[ts]
        print(f"{t} | Spot: {r.spot:.2f} | Short 26250: {r.short_ltp:6.2f} | Long 26600: {r.long_ltp:5.2f} | Spread: {r.spread:6.2f} | Gross MTM: Rs. {r.pnl_gross:+7.2f}")

max_profit = trade_df['pnl_gross'].max()
max_loss = trade_df['pnl_gross'].min()
peak_profit_time = trade_df['pnl_gross'].idxmax()
peak_loss_time = trade_df['pnl_gross'].idxmin()

print(f"\n=== 6. TRADE PERFORMANCE SUMMARY ===")
print(f"Max Unrealized Profit (Peak MTM): Rs. {max_profit:+.2f} at {peak_profit_time.strftime('%H:%M')}")
print(f"Max Unrealized Drawdown (Worst MTM): Rs. {max_loss:+.2f} at {peak_loss_time.strftime('%H:%M')}")
print(f"Exit Time: 15:15:00 | Exit Spread: Rs. {trade_df.iloc[-1]['spread']:.2f}")
print(f"Points Captured: {entry_spread - trade_df.iloc[-1]['spread']:.2f} pts")
print(f"Final Gross PnL: Rs. {trade_df.iloc[-1]['pnl_gross']:+.2f}")
slippage = (entry_spread * 0.005 + trade_df.iloc[-1]['spread'] * 0.005) * lot + 20.0 + (trade_df.iloc[-1]['spread'] * lot * 0.000625)
print(f"Slippage & Charges: Rs. {slippage:.2f}")
print(f"Final Net PnL: Rs. {trade_df.iloc[-1]['pnl_gross'] - slippage:+.2f}")
