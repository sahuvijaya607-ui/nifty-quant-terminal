"""
========================================================================================
NIFTY 50 1-MINUTE OPTION DATA EXTRACTOR & EXPORTER
========================================================================================
Fast, zero-memory-leak extractor for the 7.43 Crore row Master Options Database:
'data/nifty_options_master_2020_2026_1min.parquet'

Usage Examples:
1. Export entire day's 1-min options across all strikes:
   python scripts/get_1min_option_data.py --date 2026-03-05

2. Export specific strike and option type (CE or PE):
   python scripts/get_1min_option_data.py --date 2026-03-05 --strike 22500 --type PE

3. Export date range:
   python scripts/get_1min_option_data.py --start 2026-03-01 --end 2026-03-05 --strike 22500
========================================================================================
"""

import os
import sys
import argparse
from pathlib import Path
import time
import pandas as pd
import pyarrow.dataset as ds

MASTER_PARQUET = Path("data/nifty_options_master_2020_2026_1min.parquet")
EXPORT_DIR = Path("exports")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def extract_option_data(target_date=None, start_date=None, end_date=None, strike=None, opt_type=None, output_format="csv"):
    if not MASTER_PARQUET.exists():
        print(f"[ERROR] Master options parquet not found at: {MASTER_PARQUET}")
        return None

    if target_date:
        start_ts = pd.Timestamp(f"{target_date} 00:00:00")
        end_ts = pd.Timestamp(f"{target_date} 23:59:59")
        file_tag = f"nifty_1min_options_{target_date}"
    elif start_date and end_date:
        start_ts = pd.Timestamp(f"{start_date} 00:00:00")
        end_ts = pd.Timestamp(f"{end_date} 23:59:59")
        file_tag = f"nifty_1min_options_{start_date}_to_{end_date}"
    else:
        print("[ERROR] Please provide either --date YYYY-MM-DD or both --start and --end dates.")
        return None

    print(f"\n>>> [1/3] Scanning Master Options Database (7.43 Crore Rows)...")
    print(f"    Date Filter: {start_ts} to {end_ts}")
    if strike:
        print(f"    Strike Filter: {strike}")
    if opt_type:
        print(f"    Type Filter: {opt_type.upper()}")

    t0 = time.time()
    dataset = ds.dataset(str(MASTER_PARQUET))

    # Build filter predicate
    filter_expr = (ds.field("timestamp") >= start_ts) & (ds.field("timestamp") <= end_ts)
    if strike is not None:
        filter_expr = filter_expr & (ds.field("strike_price") == float(strike))
        file_tag += f"_strike_{int(strike)}"
    if opt_type:
        opt_upper = opt_type.strip().upper()
        filter_expr = filter_expr & (ds.field("option_type") == opt_upper)
        file_tag += f"_{opt_upper}"

    print(">>> [2/3] Extracting matching 1-minute rows...")
    table = dataset.to_table(filter=filter_expr)
    row_count = table.num_rows

    if row_count == 0:
        print(f"[!] No rows found for the specified filters. Try checking the date range (2020-01-01 to 2026-03-16).")
        return None

    elapsed = round(time.time() - t0, 2)
    print(f"    Found {row_count:,} 1-minute option records in {elapsed} seconds!")

    print(f">>> [3/3] Exporting to file...")
    df = table.to_pandas().sort_values(["timestamp", "strike_price", "option_type"])

    out_file = EXPORT_DIR / f"{file_tag}.{output_format}"
    if output_format == "csv":
        df.to_csv(out_file, index=False)
    else:
        df.to_parquet(out_file, index=False)

    print(f"\n========================================================================")
    print(f"SUCCESS: Exported {len(df):,} rows of 1-minute option data to:")
    print(f"Path: {out_file.resolve()}")
    print(f"File Size: {os.path.getsize(out_file):,} bytes")
    print(f"========================================================================\n")
    print("Sample Preview (First 5 Rows):")
    print(df.head(5).to_string(index=False))
    return out_file


def main():
    parser = argparse.ArgumentParser(description="Extract NIFTY 1-minute option data from 7.43 Crore row database")
    parser.add_argument("--date", help="Specific date (YYYY-MM-DD), e.g. 2026-03-05")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--strike", type=float, help="Strike price (e.g. 22500, 24000)")
    parser.add_argument("--type", choices=["CE", "PE", "ce", "pe"], help="Option type (CE or PE)")
    parser.add_argument("--format", choices=["csv", "parquet"], default="csv", help="Output format (default: csv)")

    args = parser.parse_args()

    # Default to 2026-03-05 if no date provided
    target_date = args.date or ("2026-03-05" if not args.start else None)
    extract_option_data(
        target_date=target_date,
        start_date=args.start,
        end_date=args.end,
        strike=args.strike,
        opt_type=args.type,
        output_format=args.format
    )


if __name__ == "__main__":
    main()
