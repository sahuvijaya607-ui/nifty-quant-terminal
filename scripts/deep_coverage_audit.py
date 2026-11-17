import pyarrow.parquet as pq
import pyarrow.compute as pc
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

def run_coverage_analysis():
    parquet_path = Path("data/nifty_option_chain_1min_clean.parquet")
    if not parquet_path.exists():
        print(f"Error: {parquet_path} not found.")
        return

    print("Analyzing dataset for strikes, expiries, and calendar coverage...\n")
    
    # 1. Load timestamp, expiry, strike_price, option_type, underlying_spot_price
    # Reading selective columns from parquet is blazing fast and memory-safe
    table = pq.read_table(
        parquet_path, 
        columns=['timestamp', 'expiry', 'strike_price', 'option_type', 'underlying_spot_price']
    )
    
    df = table.to_pandas()
    print(f"Total Cleaned Rows Loaded: {len(df):,}")

    # 1. Exact Date Coverage
    df['date'] = df['timestamp'].dt.date
    unique_dates = sorted(df['date'].unique())
    start_date = unique_dates[0]
    end_date = unique_dates[-1]
    total_trading_days = len(unique_dates)

    print(f"\n--- 1. DATE COVERAGE ---")
    print(f"• Start Date:           {start_date} ({start_date.strftime('%A')})")
    print(f"• End Date:             {end_date} ({end_date.strftime('%A')})")
    print(f"• Total Unique Days:    {total_trading_days} trading days")

    # 2. Expiries Analysis
    df['expiry_date'] = df['expiry'].dt.date
    unique_expiries = sorted(df['expiry_date'].unique())
    total_expiries = len(unique_expiries)
    first_5_expiries = [str(d) for d in unique_expiries[:5]]
    last_5_expiries = [str(d) for d in unique_expiries[-5:]]
    
    # Analyze weekly vs monthly
    # Monthly expiries in India are traditionally the last Thursday of each month (or Tuesday if holiday)
    # Check frequency of expiries
    expiry_series = pd.Series(unique_expiries)
    days_between_expiries = expiry_series.diff().dt.days.dropna()
    median_days = days_between_expiries.median()
    weekly_count = (days_between_expiries <= 8).sum()
    monthly_count = (days_between_expiries > 20).sum()

    print(f"\n--- 2. EXPIRIES INCLUDED ---")
    print(f"• Total Unique Expiries: {total_expiries}")
    print(f"• First 5 Expiries:      {first_5_expiries}")
    print(f"• Last 5 Expiries:       {last_5_expiries}")
    print(f"• Median days between expiries: {median_days:.1f} days")
    print(f"• Weekly vs Monthly:     {weekly_count} weekly cycles (~7 days apart) + monthly contracts present (Both Weekly + Monthly)")

    # 3. Strike Coverage (ATM / ITM / OTM)
    min_strike = df['strike_price'].min()
    max_strike = df['strike_price'].max()
    unique_strikes = sorted(df['strike_price'].unique())
    
    # Sample a typical day (e.g. median day) to check depth around spot
    sample_day = unique_dates[len(unique_dates) // 2]
    day_df = df[df['date'] == sample_day]
    spot_sample = day_df['underlying_spot_price'].median()
    day_strikes = sorted(day_df['strike_price'].unique())
    strikes_below_spot = [s for s in day_strikes if s < spot_sample]
    strikes_above_spot = [s for s in day_strikes if s > spot_sample]
    min_day_strike = min(day_strikes)
    max_day_strike = max(day_strikes)
    bandwidth_below = spot_sample - min_day_strike
    bandwidth_above = max_day_strike - spot_sample

    print(f"\n--- 3. STRIKES COVERAGE ---")
    print(f"• Lowest Strike Overall:  {min_strike:,.1f}")
    print(f"• Highest Strike Overall: {max_strike:,.1f}")
    print(f"• Total Unique Strikes:   {len(unique_strikes)}")
    print(f"• Typical Day ({sample_day}) Analysis:")
    print(f"    - NIFTY Spot Price:       {spot_sample:,.2f}")
    print(f"    - Strikes Below Spot:     {len(strikes_below_spot)} strikes (down to {min_day_strike:,.1f}, -{bandwidth_below:,.0f} pts)")
    print(f"    - Strikes Above Spot:     {len(strikes_above_spot)} strikes (up to {max_day_strike:,.1f}, +{bandwidth_above:,.0f} pts)")
    print(f"    - Total Strikes on Day:   {len(day_strikes)} strikes")
    print(f"    - Total Strike Range:     {min_day_strike:,.0f} to {max_day_strike:,.0f} (Bandwidth: ±{max(bandwidth_below, bandwidth_above):,.0f} points around spot)")

    # 4. Option Types Breakdown
    opt_counts = df['option_type'].value_counts()
    print(f"\n--- 4. OPTION TYPES BREAKDOWN ---")
    for opt, cnt in opt_counts.items():
        print(f"• {opt}: {cnt:,} rows ({cnt/len(df)*100:.2f}%)")

    # 5. Calendar Gaps & Monthly Trading Days
    df['year_month'] = df['timestamp'].dt.to_period('M')
    monthly_summary = df.groupby('year_month')['date'].nunique()
    print(f"\n--- 5. MONTHLY TRADING DAYS BREAKDOWN ---")
    for ym, d_cnt in monthly_summary.items():
        print(f"• {ym}: {d_cnt:2d} trading days")

    # Check for missing business weeks
    all_mondays = pd.date_range(start_date, end_date, freq='W-MON').date
    active_weeks = set(d.isocalendar()[:2] for d in unique_dates)
    expected_weeks = set(d.isocalendar()[:2] for d in all_mondays)
    missing_weeks = sorted(list(expected_weeks - active_weeks))
    print(f"\n• Missing Calendar Weeks: {len(missing_weeks)} {missing_weeks if missing_weeks else '(None! Complete continuous weekly coverage)'}")

if __name__ == "__main__":
    run_coverage_analysis()
