import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

def resample_options_pipeline():
    src_parquet = Path("data/nifty_option_chain_1min_clean.parquet")
    if not src_parquet.exists():
        print(f"Error: {src_parquet} not found.")
        sys.exit(1)

    out_5min = Path("data/nifty_options_5min.parquet")
    out_15min = Path("data/nifty_options_15min.parquet")
    out_1hr = Path("data/nifty_options_1hr.parquet")
    out_1day = Path("data/nifty_options_1day.parquet")
    out_1day_csv = Path("data/nifty_options_1day.csv")

    # Clean up existing target files
    for p in [out_5min, out_15min, out_1hr, out_1day, out_1day_csv]:
        if p.exists():
            p.unlink()

    print("================================================================================")
    print("      MULTI-TIMEFRAME RESAMPLING PIPELINE: NIFTY OPTIONS OHLCV + OI            ")
    print("================================================================================")
    print(f"Source 1-Min Master: {src_parquet} ({src_parquet.stat().st_size / (1024**2):.2f} MB)")
    print("Target Outputs:")
    print(f"  • 5-Minute:  {out_5min}")
    print(f"  • 15-Minute: {out_15min}")
    print(f"  • 1-Hour:    {out_1hr}")
    print(f"  • 1-Day:     {out_1day} & {out_1day_csv}")
    print("================================================================================\n")

    # PyArrow Schema for OHLCV+OI resampled tables
    resampled_schema = pa.schema([
        pa.field('timestamp', pa.timestamp('ns')),
        pa.field('expiry', pa.timestamp('ns')),
        pa.field('strike_price', pa.float32()),
        pa.field('option_type', pa.string()),
        pa.field('open', pa.float32()),
        pa.field('high', pa.float32()),
        pa.field('low', pa.float32()),
        pa.field('close', pa.float32()),
        pa.field('volume', pa.int32()),
        pa.field('oi', pa.int32()),
        pa.field('underlying_spot_price', pa.float32()),
    ])

    writer_5m = pq.ParquetWriter(out_5min, resampled_schema, compression='snappy')
    writer_15m = pq.ParquetWriter(out_15min, resampled_schema, compression='snappy')
    writer_1h = pq.ParquetWriter(out_1hr, resampled_schema, compression='snappy')
    writer_1d = pq.ParquetWriter(out_1day, resampled_schema, compression='snappy')

    print("Reading source 1-minute dataset into memory...")
    t0 = time.time()
    table = pq.read_table(
        src_parquet,
        columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'volume', 'oi', 'underlying_spot_price']
    )
    df = table.to_pandas()
    print(f"Loaded {len(df):,} rows in {time.time()-t0:.2f} seconds.\n")

    unique_expiries = sorted(df['expiry'].unique())
    total_expiries = len(unique_expiries)
    print(f"Processing {total_expiries} unique expiries independently for exact memory safety...")

    total_rows_5m = 0
    total_rows_15m = 0
    total_rows_1h = 0
    total_rows_1d = 0
    list_1d_dfs = []

    t_start = time.time()

    for idx, exp in enumerate(unique_expiries, 1):
        t_exp = time.time()
        sub = df[df['expiry'] == exp].copy()

        # -------------------------------------------------------------
        # 1. 5-Minute Resampling: Aligned to 09:15:00
        # -------------------------------------------------------------
        sub['ts_5m'] = sub['timestamp'].dt.floor('5min')
        df_5m = sub.groupby(['ts_5m', 'expiry', 'strike_price', 'option_type']).agg(
            open=('ltp', 'first'),
            high=('ltp', 'max'),
            low=('ltp', 'min'),
            close=('ltp', 'last'),
            volume=('volume', 'sum'),
            oi=('oi', 'last'),
            underlying_spot_price=('underlying_spot_price', 'last')
        ).reset_index().rename(columns={'ts_5m': 'timestamp'})

        # Cast types & write
        df_5m['strike_price'] = df_5m['strike_price'].astype('float32')
        df_5m['option_type'] = df_5m['option_type'].astype(str)
        df_5m['open'] = df_5m['open'].astype('float32')
        df_5m['high'] = df_5m['high'].astype('float32')
        df_5m['low'] = df_5m['low'].astype('float32')
        df_5m['close'] = df_5m['close'].astype('float32')
        df_5m['volume'] = df_5m['volume'].astype('int32')
        df_5m['oi'] = df_5m['oi'].astype('int32')
        df_5m['underlying_spot_price'] = df_5m['underlying_spot_price'].astype('float32')

        table_5m = pa.Table.from_pandas(df_5m, schema=resampled_schema, preserve_index=False)
        writer_5m.write_table(table_5m)
        total_rows_5m += len(df_5m)

        # -------------------------------------------------------------
        # 2. 15-Minute Resampling: Aligned to 09:15:00
        # -------------------------------------------------------------
        sub['ts_15m'] = sub['timestamp'].dt.floor('15min')
        df_15m = sub.groupby(['ts_15m', 'expiry', 'strike_price', 'option_type']).agg(
            open=('ltp', 'first'),
            high=('ltp', 'max'),
            low=('ltp', 'min'),
            close=('ltp', 'last'),
            volume=('volume', 'sum'),
            oi=('oi', 'last'),
            underlying_spot_price=('underlying_spot_price', 'last')
        ).reset_index().rename(columns={'ts_15m': 'timestamp'})

        df_15m['strike_price'] = df_15m['strike_price'].astype('float32')
        df_15m['option_type'] = df_15m['option_type'].astype(str)
        df_15m['open'] = df_15m['open'].astype('float32')
        df_15m['high'] = df_15m['high'].astype('float32')
        df_15m['low'] = df_15m['low'].astype('float32')
        df_15m['close'] = df_15m['close'].astype('float32')
        df_15m['volume'] = df_15m['volume'].astype('int32')
        df_15m['oi'] = df_15m['oi'].astype('int32')
        df_15m['underlying_spot_price'] = df_15m['underlying_spot_price'].astype('float32')

        table_15m = pa.Table.from_pandas(df_15m, schema=resampled_schema, preserve_index=False)
        writer_15m.write_table(table_15m)
        total_rows_15m += len(df_15m)

        # -------------------------------------------------------------
        # 3. 1-Hour Resampling: Aligned to 09:15:00, 10:15:00, etc.
        # -------------------------------------------------------------
        sub['ts_1h'] = (sub['timestamp'] - pd.Timedelta(minutes=15)).dt.floor('1h') + pd.Timedelta(minutes=15)
        df_1h = sub.groupby(['ts_1h', 'expiry', 'strike_price', 'option_type']).agg(
            open=('ltp', 'first'),
            high=('ltp', 'max'),
            low=('ltp', 'min'),
            close=('ltp', 'last'),
            volume=('volume', 'sum'),
            oi=('oi', 'last'),
            underlying_spot_price=('underlying_spot_price', 'last')
        ).reset_index().rename(columns={'ts_1h': 'timestamp'})

        df_1h['strike_price'] = df_1h['strike_price'].astype('float32')
        df_1h['option_type'] = df_1h['option_type'].astype(str)
        df_1h['open'] = df_1h['open'].astype('float32')
        df_1h['high'] = df_1h['high'].astype('float32')
        df_1h['low'] = df_1h['low'].astype('float32')
        df_1h['close'] = df_1h['close'].astype('float32')
        df_1h['volume'] = df_1h['volume'].astype('int32')
        df_1h['oi'] = df_1h['oi'].astype('int32')
        df_1h['underlying_spot_price'] = df_1h['underlying_spot_price'].astype('float32')

        table_1h = pa.Table.from_pandas(df_1h, schema=resampled_schema, preserve_index=False)
        writer_1h.write_table(table_1h)
        total_rows_1h += len(df_1h)

        # -------------------------------------------------------------
        # 4. 1-Day Resampling: 1 Bar Per Contract Per Day
        # -------------------------------------------------------------
        sub['ts_1d'] = sub['timestamp'].dt.floor('1D')
        df_1d = sub.groupby(['ts_1d', 'expiry', 'strike_price', 'option_type']).agg(
            open=('ltp', 'first'),
            high=('ltp', 'max'),
            low=('ltp', 'min'),
            close=('ltp', 'last'),
            volume=('volume', 'sum'),
            oi=('oi', 'last'),
            underlying_spot_price=('underlying_spot_price', 'last')
        ).reset_index().rename(columns={'ts_1d': 'timestamp'})

        df_1d['strike_price'] = df_1d['strike_price'].astype('float32')
        df_1d['option_type'] = df_1d['option_type'].astype(str)
        df_1d['open'] = df_1d['open'].astype('float32')
        df_1d['high'] = df_1d['high'].astype('float32')
        df_1d['low'] = df_1d['low'].astype('float32')
        df_1d['close'] = df_1d['close'].astype('float32')
        df_1d['volume'] = df_1d['volume'].astype('int32')
        df_1d['oi'] = df_1d['oi'].astype('int32')
        df_1d['underlying_spot_price'] = df_1d['underlying_spot_price'].astype('float32')

        table_1d = pa.Table.from_pandas(df_1d, schema=resampled_schema, preserve_index=False)
        writer_1d.write_table(table_1d)
        total_rows_1d += len(df_1d)
        list_1d_dfs.append(df_1d)

        if idx % 10 == 0 or idx == total_expiries:
            elapsed = time.time() - t_start
            print(f"  [{idx:02d}/{total_expiries}] Processed expiry {str(exp)[:10]} | 5m: {total_rows_5m:,} | 15m: {total_rows_15m:,} | 1h: {total_rows_1h:,} | 1d: {total_rows_1d:,} ({elapsed:.1f}s)")

    # Close parquet writers
    writer_5m.close()
    writer_15m.close()
    writer_1h.close()
    writer_1d.close()

    # Save 1-day CSV for Excel
    print("\nSaving 1-day consolidated dataset to CSV...")
    full_1d_df = pd.concat(list_1d_dfs, ignore_index=True)
    full_1d_df = full_1d_df.sort_values(['timestamp', 'expiry', 'strike_price', 'option_type'])
    full_1d_df.to_csv(out_1day_csv, index=False)

    total_time = time.time() - t_start

    # Final Summary Table
    size_1m = src_parquet.stat().st_size / (1024**2)
    size_5m = out_5min.stat().st_size / (1024**2)
    size_15m = out_15min.stat().st_size / (1024**2)
    size_1h = out_1hr.stat().st_size / (1024**2)
    size_1d = out_1day.stat().st_size / (1024**2)
    size_1d_csv = out_1day_csv.stat().st_size / (1024**2)

    print("\n================================================================================")
    print("                 MULTI-TIMEFRAME GENERATION COMPLETE                            ")
    print("================================================================================")
    print(f"{'Timeframe':<14} | {'Format':<10} | {'File Size':<12} | {'Total Rows':<14} | {'Path'}")
    print("-" * 80)
    print(f"{'1-Minute':<14} | {'Parquet':<10} | {size_1m:>9.2f} MB | {len(df):>14,} | {src_parquet}")
    print(f"{'5-Minute':<14} | {'Parquet':<10} | {size_5m:>9.2f} MB | {total_rows_5m:>14,} | {out_5min}")
    print(f"{'15-Minute':<14} | {'Parquet':<10} | {size_15m:>9.2f} MB | {total_rows_15m:>14,} | {out_15min}")
    print(f"{'1-Hour':<14} | {'Parquet':<10} | {size_1h:>9.2f} MB | {total_rows_1h:>14,} | {out_1hr}")
    print(f"{'1-Day':<14} | {'Parquet':<10} | {size_1d:>9.2f} MB | {total_rows_1d:>14,} | {out_1day}")
    print(f"{'1-Day (Excel)':<14} | {'CSV':<10} | {size_1d_csv:>9.2f} MB | {total_rows_1d:>14,} | {out_1day_csv}")
    print("================================================================================")
    print(f"Total Resampling Execution Time: {total_time:.1f} seconds ({total_time/60:.2f} mins)")
    print("================================================================================")

if __name__ == "__main__":
    resample_options_pipeline()
