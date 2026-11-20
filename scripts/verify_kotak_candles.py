import os
from dotenv import load_dotenv
from neo_api_client import NeoAPI
import pandas as pd
import numpy as np

load_dotenv()
client = NeoAPI(consumer_key=os.getenv('CONSUMER_KEY'), environment='prod')

# Fetch 5min candles from 2026-11-01 to 2026-11-20 for full EMA stability
data = client.historical_data(neosymbol='nse_cm|Nifty 50', interval='5min', from_date='2026-11-01', to_date='2026-11-20')
candles = data['data']['candles']
df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['timestamp'] = pd.to_datetime(df['timestamp'])
df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()

# Filter for 2026-11-20
df_today = df[df['timestamp'].dt.date == pd.to_datetime('2026-11-20').date()].copy()
df_today['color'] = np.where(df_today['close'] >= df_today['open'], 'GREEN', 'RED')
df_today['stack_ok'] = (df_today['ema9'] > df_today['ema20']) & (df_today['ema20'] > df_today['ema50'])

print("=== KOTAK NEO OFFICIAL 5-MIN CANDLES FOR 20-NOV-2026 ===")
for idx, r in df_today.iterrows():
    t_str = r['timestamp'].strftime('%H:%M')
    print(f"{t_str} | Open: {r['open']:.2f} | High: {r['high']:.2f} | Low: {r['low']:.2f} | Close: {r['close']:.2f} | Color: {r['color']:<5} | EMA9: {r['ema9']:.2f} | EMA20: {r['ema20']:.2f} | EMA50: {r['ema50']:.2f} | 9>20>50: {r['stack_ok']}")
