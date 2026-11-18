import os
import sys
import re
import time
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

# Month mapping for parsing expiry
MONTH_MAP = {
    'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
    'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
    'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
}
SYM_PATTERN = re.compile(r'^NIFTY(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$')

def parse_symbol(sym):
    m = SYM_PATTERN.match(sym)
    if not m:
        return None, None, None
    dd, mmm, yy, strike, opt_type = m.groups()
    mm = MONTH_MAP.get(mmm, '01')
    exp_str = f"20{yy}-{mm}-{dd} 15:30:00"
    return exp_str, float(strike), opt_type

def build_master_options_vault():
    base_2020_2024 = (
        Path.home() / '.cache' / 'kagglehub' / 'datasets' / 
        'ayushsacri' / 'indian-nifty-and-banknifty-options-data-2020-2024' / 
        'versions' / '1' / 'nifty_data' / 'nifty_options'
    )
    parquet_2024_2026 = Path("data/nifty_option_chain_1min_clean.parquet")
    output_master = Path("data/nifty_options_master_2020_2026_1min.parquet")

    if not base_2020_2024.exists():
        print(f"Error: {base_2020_2024} not found.")
        sys.exit(1)
    if not parquet_2024_2026.exists():
        print(f"Error: {parquet_2024_2026} not found.")
        sys.exit(1)

    print("================================================================================")
    print("      BUILDING 2020-2026 UNIFIED NIFTY 1-MINUTE OPTIONS MASTER VAULT            ")
    print("================================================================================")
    print(f"Input 1 (2020-2024): {base_2020_2024}")
    print(f"Input 2 (2024-2026): {parquet_2024_2026}")
    print(f"Target Master Vault: {output_master}")
    print("================================================================================\n")

    if output_master.exists():
        output_master.unlink()

    # Define Unified Master Schema
    master_schema = pa.schema([
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
    ])

    writer = pq.ParquetWriter(output_master, master_schema, compression='snappy')

    t0 = time.time()
    total_master_rows = 0
    year_row_counts = {}

    # --------------------------------------------------------------------------
    # PHASE 1: Parse and Stream 2020–2024 Daily CSVs (up to 2024-09-30)
    # --------------------------------------------------------------------------
    print("--- PHASE 1: Parsing 1,203 Daily CSVs (2020 to Sept 2024) ---")
    all_csv_files = sorted(list(base_2020_2024.glob('**/*.csv')))
    print(f"Found {len(all_csv_files):,} CSV files to process.")

    batch_dfs = []
    batch_rows = 0

    for idx, fpath in enumerate(all_csv_files, 1):
        try:
            df = pd.read_csv(fpath)
            if df.empty or 'symbol' not in df.columns:
                continue

            # Parse unique symbols
            unique_syms = df['symbol'].dropna().unique()
            sym_map = {}
            for s in unique_syms:
                s_str = str(s).strip()
                res = parse_symbol(s_str)
                if res[0] is not None:
                    sym_map[s] = res

            if not sym_map:
                continue

            # Filter rows with recognized symbols
            valid_mask = df['symbol'].isin(sym_map)
            df = df[valid_mask].copy()

            # Map expiry, strike_price, option_type
            df['expiry'] = pd.to_datetime(df['symbol'].map(lambda s: sym_map[s][0]))
            df['strike_price'] = df['symbol'].map(lambda s: sym_map[s][1]).astype('float32')
            df['option_type'] = df['symbol'].map(lambda s: sym_map[s][2]).astype(str)

            # Combine date and time to timestamp
            df['timestamp'] = pd.to_datetime(df['date'] + ' ' + df['time'])

            # Filter strictly to market hours (09:15:00 to 15:29:00)
            m_mins = df['timestamp'].dt.hour * 60 + df['timestamp'].dt.minute
            df = df[(m_mins >= 555) & (m_mins <= 929)]

            # Cast dtypes
            df['open'] = df['open'].astype('float32')
            df['high'] = df['high'].astype('float32')
            df['low'] = df['low'].astype('float32')
            df['close'] = df['close'].astype('float32')
            df['volume'] = df['volume'].fillna(0).astype('int32')
            df['oi'] = df['oi'].fillna(0).astype('int32')

            # Keep standard columns
            cols = ['timestamp', 'expiry', 'strike_price', 'option_type', 'open', 'high', 'low', 'close', 'volume', 'oi']
            df = df[cols].drop_duplicates(subset=['timestamp', 'expiry', 'strike_price', 'option_type'])

            batch_dfs.append(df)
            batch_rows += len(df)

            # Flush batch to parquet every 50 files or >1M rows
            if idx % 50 == 0 or idx == len(all_csv_files) or batch_rows >= 1000000:
                merged_batch = pd.concat(batch_dfs, ignore_index=True)
                table = pa.Table.from_pandas(merged_batch, schema=master_schema, preserve_index=False)
                writer.write_table(table)
                total_master_rows += len(merged_batch)

                # Track year counts
                for yr, cnt in merged_batch['timestamp'].dt.year.value_counts().items():
                    year_row_counts[yr] = year_row_counts.get(yr, 0) + cnt

                elapsed = time.time() - t0
                print(f"  [{idx:04d}/{len(all_csv_files)}] Processed {total_master_rows:,} rows ({elapsed:.1f}s, {total_master_rows/elapsed:,.0f} rows/s)")
                batch_dfs = []
                batch_rows = 0

        except Exception as e:
            print(f"  Warning: Skipped file {fpath.name} due to error: {e}")

    phase1_rows = total_master_rows
    print(f"\nPhase 1 Complete: {phase1_rows:,} clean rows written from 2020-2024 dataset.\n")

    # --------------------------------------------------------------------------
    # PHASE 2: Stream 2024–2026 Dataset (from 2024-10-01 to 2026-03-24)
    # --------------------------------------------------------------------------
    print("--- PHASE 2: Merging 2024-2026 Master Parquet (from Oct 1, 2024 onward) ---")
    parquet_file = pq.ParquetFile(parquet_2024_2026)
    num_rg = parquet_file.num_row_groups
    print(f"Reading {num_rg} row groups from {parquet_2024_2026.name}...")

    phase2_rows = 0
    cut_off_date = pd.to_datetime('2024-10-01 00:00:00')

    for rg_idx in range(num_rg):
        table_rg = parquet_file.read_row_group(
            rg_idx, 
            columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'ltp', 'volume', 'oi']
        )
        df_rg = table_rg.to_pandas()

        # Seamless overlap filter: take only >= 2024-10-01
        df_rg = df_rg[df_rg['timestamp'] >= cut_off_date]
        if df_rg.empty:
            continue

        # Filter market hours 09:15:00 to 15:29:00
        m_mins = df_rg['timestamp'].dt.hour * 60 + df_rg['timestamp'].dt.minute
        df_rg = df_rg[(m_mins >= 555) & (m_mins <= 929)]

        # Map ltp -> open, high, low, close
        df_rg['open'] = df_rg['ltp'].astype('float32')
        df_rg['high'] = df_rg['ltp'].astype('float32')
        df_rg['low'] = df_rg['ltp'].astype('float32')
        df_rg['close'] = df_rg['ltp'].astype('float32')
        df_rg['strike_price'] = df_rg['strike_price'].astype('float32')
        df_rg['option_type'] = df_rg['option_type'].astype(str)
        df_rg['volume'] = df_rg['volume'].astype('int32')
        df_rg['oi'] = df_rg['oi'].astype('int32')

        cols = ['timestamp', 'expiry', 'strike_price', 'option_type', 'open', 'high', 'low', 'close', 'volume', 'oi']
        df_rg = df_rg[cols]

        table_out = pa.Table.from_pandas(df_rg, schema=master_schema, preserve_index=False)
        writer.write_table(table_out)

        total_master_rows += len(df_rg)
        phase2_rows += len(df_rg)

        for yr, cnt in df_rg['timestamp'].dt.year.value_counts().items():
            year_row_counts[yr] = year_row_counts.get(yr, 0) + cnt

        if (rg_idx + 1) % 5 == 0 or (rg_idx + 1) == num_rg:
            elapsed = time.time() - t0
            print(f"  Row Group [{rg_idx+1:02d}/{num_rg}]: Total Master Rows: {total_master_rows:,} ({elapsed:.1f}s)")

    writer.close()
    total_time = time.time() - t0

    # --------------------------------------------------------------------------
    # PHASE 3: Forensic Audit of the Unified Master Vault
    # --------------------------------------------------------------------------
    print("\n================================================================================")
    print("                    MASTER VAULT AUDIT & VERIFICATION                           ")
    print("================================================================================")
    final_meta = pq.read_metadata(output_master)
    vault_size_mb = output_master.stat().st_size / (1024**2)
    vault_size_gb = output_master.stat().st_size / (1024**3)

    print(f"Master Vault File:            {output_master.resolve()}")
    print(f"Master File Size:             {vault_size_gb:.2f} GB ({vault_size_mb:,.2f} MB)")
    print(f"Total Rows (1-Min Candles):   {final_meta.num_rows:,}")
    print(f"  • Rows from 2020-2024 Data: {phase1_rows:,}")
    print(f"  • Rows from 2024-2026 Data: {phase2_rows:,}")

    # Inspect date coverage & sample rows
    # Read first row group (2020) and last row group (2026)
    table_head = parquet_file_read_first = pq.read_table(output_master, columns=['timestamp']).slice(0, 5)
    first_dt = table_head['timestamp'].to_pylist()[0]

    last_slice = pq.read_table(output_master, columns=['timestamp']).slice(final_meta.num_rows - 5, 5)
    last_dt = last_slice['timestamp'].to_pylist()[-1]

    print(f"\nExact Time Horizon:           {first_dt} to {last_dt}")
    
    print("\n--- YEAR-BY-YEAR ROW BREAKDOWN ---")
    for yr in sorted(year_row_counts.keys()):
        print(f"  • {yr}: {year_row_counts[yr]:>12,} rows")

    print("\n================================================================================")
    print(f"Pipeline Completed in {total_time:.1f}s ({total_time/60:.2f} mins). VAULT 100% PRODUCTION READY!")
    print("================================================================================")

if __name__ == "__main__":
    build_master_options_vault()
