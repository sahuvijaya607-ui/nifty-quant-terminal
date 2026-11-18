import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

def clean_and_optimize_options():
    raw_path = Path("data/kaggle_raw/final_merged_output.csv")
    output_parquet = Path("data/nifty_option_chain_1min_clean.parquet")
    sample_csv = Path("data/options_sample_1000.csv")

    if not raw_path.exists():
        print(f"Error: {raw_path} not found.")
        sys.exit(1)

    print("================================================================================")
    print("        NIFTY OPTION CHAIN (1-MIN) CLEANING & PARQUET OPTIMIZATION             ")
    print("================================================================================")
    print(f"Raw Input:       {raw_path} ({raw_path.stat().st_size / (1024**3):.2f} GB)")
    print(f"Target Parquet:  {output_parquet}")
    print(f"Sample CSV:      {sample_csv}")
    print("================================================================================\n")

    # Define PyArrow Schema with optimized data types
    pa_schema = pa.schema([
        pa.field('timestamp', pa.timestamp('ns')),
        pa.field('expiry', pa.timestamp('ns')),
        pa.field('strike_price', pa.float32()),
        pa.field('option_type', pa.string()),
        pa.field('ltp', pa.float32()),
        pa.field('volume', pa.int32()),
        pa.field('oi', pa.int32()),
        pa.field('underlying_spot_price', pa.float32()),
        pa.field('strike_spot_diff', pa.float32()),
        pa.field('time_to_expiry', pa.string()),
        pa.field('tte_years', pa.float32()),
        pa.field('IAO', pa.string()),
        pa.field('pcr', pa.float32()),
        pa.field('flag', pa.string()),
        pa.field('iv', pa.float32()),
        pa.field('delta', pa.float32()),
        pa.field('gamma', pa.float32()),
        pa.field('theta', pa.float32()),
        pa.field('vega', pa.float32()),
        pa.field('rho', pa.float32()),
    ])

    # Remove existing parquet if exists
    if output_parquet.exists():
        output_parquet.unlink()

    # Initialize ParquetWriter
    writer = pq.ParquetWriter(output_parquet, pa_schema, compression='snappy')

    chunk_size = 1000000 # 1 Million rows per chunk for optimal throughput & memory safety
    total_raw_rows = 0
    total_cleaned_rows = 0
    dropped_after_hours = 0
    dropped_ltp_zero = 0
    dropped_duplicates = 0
    
    first_sample_df = None
    min_cleaned_dt = None
    max_cleaned_dt = None

    t0 = time.time()
    chunk_num = 0

    print("Streaming CSV chunks, filtering market hours, deduplicating, and casting dtypes...")
    
    for chunk in pd.read_csv(raw_path, chunksize=chunk_size, low_memory=False):
        chunk_num += 1
        n_raw = len(chunk)
        total_raw_rows += n_raw

        # 1. Convert timestamp and expiry to datetime64[ns]
        chunk['timestamp'] = pd.to_datetime(chunk['timestamp'], errors='coerce')
        chunk['expiry'] = pd.to_datetime(chunk['expiry'], errors='coerce')

        # Drop any corrupted datetime rows
        valid_dt_mask = chunk['timestamp'].notnull() & chunk['expiry'].notnull()
        chunk = chunk[valid_dt_mask]

        # 2. Strict Indian Market Hours Filter: 09:15:00 <= time <= 15:30:00
        # Fast vectorized integer minute comparison: 9*60+15 = 555, 15*60+30 = 930
        minute_of_day = chunk['timestamp'].dt.hour * 60 + chunk['timestamp'].dt.minute
        market_mask = (minute_of_day >= 555) & (minute_of_day <= 930)
        n_after_hours = (~market_mask).sum()
        dropped_after_hours += n_after_hours
        chunk = chunk[market_mask]

        # 3. Filter LTP > 0
        ltp_mask = chunk['ltp'] > 0
        n_zero_ltp = (~ltp_mask).sum()
        dropped_ltp_zero += n_zero_ltp
        chunk = chunk[ltp_mask]

        # 4. Intra-chunk Deduplication on (timestamp, expiry, strike_price, option_type)
        n_before_dedup = len(chunk)
        chunk = chunk.drop_duplicates(subset=['timestamp', 'expiry', 'strike_price', 'option_type'])
        n_dups = n_before_dedup - len(chunk)
        dropped_duplicates += n_dups

        if len(chunk) == 0:
            continue

        # Track min/max timestamp
        c_min = chunk['timestamp'].min()
        c_max = chunk['timestamp'].max()
        if min_cleaned_dt is None or c_min < min_cleaned_dt:
            min_cleaned_dt = c_min
        if max_cleaned_dt is None or c_max > max_cleaned_dt:
            max_cleaned_dt = c_max

        # 5. Type conversions to optimized memory dtypes
        chunk['strike_price'] = chunk['strike_price'].astype('float32')
        chunk['option_type'] = chunk['option_type'].astype(str)
        chunk['ltp'] = chunk['ltp'].astype('float32')
        chunk['volume'] = chunk['volume'].fillna(0).astype('int32')
        chunk['oi'] = chunk['oi'].fillna(0).astype('int32')
        chunk['underlying_spot_price'] = chunk['underlying_spot_price'].astype('float32')
        chunk['strike_spot_diff'] = chunk['strike_spot_diff'].astype('float32')
        chunk['time_to_expiry'] = chunk['time_to_expiry'].astype(str)
        chunk['tte_years'] = chunk['tte_years'].astype('float32')
        chunk['IAO'] = chunk['IAO'].astype(str)
        chunk['pcr'] = chunk['pcr'].astype('float32')
        chunk['flag'] = chunk['flag'].astype(str)
        chunk['iv'] = chunk['iv'].astype('float32')
        chunk['delta'] = chunk['delta'].astype('float32')
        chunk['gamma'] = chunk['gamma'].astype('float32')
        chunk['theta'] = chunk['theta'].astype('float32')
        chunk['vega'] = chunk['vega'].astype('float32')
        chunk['rho'] = chunk['rho'].astype('float32')

        # Keep first 1,000 rows for sample CSV
        if first_sample_df is None:
            first_sample_df = chunk.head(1000).copy()
        elif len(first_sample_df) < 1000:
            needed = 1000 - len(first_sample_df)
            first_sample_df = pd.concat([first_sample_df, chunk.head(needed)], ignore_index=True)

        # Write to Parquet Table
        table = pa.Table.from_pandas(chunk, schema=pa_schema, preserve_index=False)
        writer.write_table(table)

        total_cleaned_rows += len(chunk)
        elapsed = time.time() - t0
        print(f"  Chunk {chunk_num:02d}: Cleaned {total_cleaned_rows:,} / {total_raw_rows:,} raw rows ({elapsed:.1f}s, {total_cleaned_rows/elapsed:,.0f} rows/s)")

    writer.close()
    total_elapsed = time.time() - t0

    # Save 1,000-row sample CSV
    if first_sample_df is not None:
        first_sample_df.to_csv(sample_csv, index=False)
        print(f"\nSaved 1,000-row sample CSV to: {sample_csv}")

    parquet_size_bytes = output_parquet.stat().st_size
    parquet_size_mb = parquet_size_bytes / (1024 ** 2)
    parquet_size_gb = parquet_size_bytes / (1024 ** 3)
    raw_size_mb = raw_path.stat().st_size / (1024 ** 2)
    compression_ratio = raw_size_mb / parquet_size_mb

    print("\n================================================================================")
    print("                    DATA PIPELINE CLEANING COMPLETE                             ")
    print("================================================================================")
    print(f"Total Raw Rows Processed:     {total_raw_rows:,}")
    print(f"Dropped After-Hours Rows:     {dropped_after_hours:,}")
    print(f"Dropped LTP <= 0 Rows:        {dropped_ltp_zero:,}")
    print(f"Dropped Intra-Chunk Dups:     {dropped_duplicates:,}")
    print(f"Final Cleaned Rows:           {total_cleaned_rows:,} (Retention: {total_cleaned_rows/total_raw_rows*100:.2f}%)")
    print(f"Date Coverage (Start to End): {min_cleaned_dt} to {max_cleaned_dt}")
    print(f"Original CSV Size:            {raw_size_mb:.2f} MB ({raw_path.stat().st_size / (1024**3):.2f} GB)")
    print(f"Cleaned Parquet Size:         {parquet_size_mb:.2f} MB ({parquet_size_gb:.2f} GB)")
    print(f"Compression Efficiency:       {compression_ratio:.2f}x reduction!")
    print(f"Total Processing Time:        {total_elapsed:.1f} seconds ({total_elapsed/60:.2f} mins)")
    print("================================================================================")

if __name__ == "__main__":
    clean_and_optimize_options()
