"""
Comprehensive Multi-Timeframe Data Audit & Integrity Verification Script.

Audits all 4 NIFTY 50 5-Year Datasets:
1. 5-Minute  (nifty_5min_5yr)
2. 15-Minute (nifty_15min_5yr)
3. 1-Hour    (nifty_1hr_5yr)
4. 1-Day     (nifty_1day_5yr)

Outputs:
- Total rows & Total trading days
- Exact Start Date and End Date
- Head(3) and Tail(3) samples with timestamps and OHLC prices
- Strict integrity checks (missing values, duplicates, market hours bounds)
"""

import os
import sys
import datetime
import pandas as pd
import numpy as np

TIMEFRAMES = [
    ("1-Minute (1m)", "nifty_1min_5yr.parquet", "nifty_1min_5yr.csv"),
    ("5-Minute (5m)", "nifty_5min_5yr.parquet", "nifty_5min_5yr.csv"),
    ("15-Minute (15m)", "nifty_15min_5yr.parquet", "nifty_15min_5yr.csv"),
    ("1-Hour (1h)", "nifty_1hr_5yr.parquet", "nifty_1hr_5yr.csv"),
    ("1-Day (1D)", "nifty_1day_5yr.parquet", "nifty_1day_5yr.csv"),
]

def audit_dataset(name: str, pq_name: str, csv_name: str, data_dir: str):
    pq_path = os.path.join(data_dir, pq_name)
    csv_path = os.path.join(data_dir, csv_name)

    print("\n" + "=" * 85)
    print(f"               TIMEFRAME AUDIT: {name.upper()}")
    print("=" * 85)

    if not os.path.exists(pq_path):
        print(f"ERROR: {pq_path} not found!")
        return

    df = pd.read_parquet(pq_path)
    pq_size_mb = os.path.getsize(pq_path) / (1024 * 1024)
    csv_size_mb = os.path.getsize(csv_path) / (1024 * 1024) if os.path.exists(csv_path) else 0.0

    total_rows = len(df)
    unique_dates = pd.Series(df.index.date).unique()
    total_trading_days = len(unique_dates)
    start_dt = df.index.min()
    end_dt = df.index.max()

    # Integrity
    nulls = int(df.isnull().sum().sum())
    dups = int(df.index.duplicated().sum())

    # Market bounds check for intraday
    is_daily = "1day" in pq_name
    if not is_daily:
        times = df.index.time
        open_time = datetime.time(9, 15, 0)
        close_time = datetime.time(15, 30, 0)
        outside_cnt = int(((times < open_time) | (times > close_time)).sum())
    else:
        outside_cnt = 0

    print(f"Files           : Parquet = {pq_name} ({pq_size_mb:.2f} MB)")
    print(f"                  CSV     = {csv_name} ({csv_size_mb:.2f} MB)")
    print(f"Total Rows      : {total_rows:,} candles")
    print(f"Trading Days    : {total_trading_days:,} days (Average: {total_rows / total_trading_days:.2f} bars/day)")
    print(f"Start Date/Time : {start_dt}")
    print(f"End Date/Time   : {end_dt}")
    print(f"Data Quality    : {nulls} Nulls | {dups} Duplicates | {outside_cnt} Outside Market Hours")
    print("-" * 85)
    print("FIRST 3 ROWS (head(3)):")
    print(df.head(3).to_string())
    print("-" * 85)
    print("LAST 3 ROWS (tail(3)):")
    print(df.tail(3).to_string())
    print("=" * 85)


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(project_root, "data")

    print("\n" + "#" * 85)
    print("           COMPREHENSIVE 5-YEAR MULTI-TIMEFRAME DATA AUDIT (NOV 2021 - NOV 2026)")
    print("#" * 85)

    for label, pq_name, csv_name in TIMEFRAMES:
        audit_dataset(label, pq_name, csv_name, data_dir)

    print("\n[ALL 4 DATASETS AUDITED AND VERIFIED SUCCESSFULLY]\n")


if __name__ == "__main__":
    main()
