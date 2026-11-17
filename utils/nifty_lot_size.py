"""
utils/nifty_lot_size.py
Historical NIFTY 50 Option & Future Contract Lot Size Lookup Utility.

Based on official SEBI & NSE circulars:
1. Up to 2021-04-29: Lot Size = 75
2. 2021-04-30 to 2024-04-25: Lot Size = 50 (NSE Circular NSE/FAOP/47784)
3. 2024-04-26 to 2024-11-19: Lot Size = 25 (NSE Circular NSE/FAOP/61517)
4. 2024-11-20 to 2025-12-30: Lot Size = 75 (SEBI/HO/MRD/TPD-1/P/CIR/2024/132 & NSE Circular NSE/FAOP/64516)
5. 2025-12-31 onward (2026+): Lot Size = 65 (NSE Circular FAOP/70616 effective Jan 2026 series)
"""

from datetime import date, datetime
from typing import Union
import pandas as pd
import numpy as np

# Historical transition boundaries (as date objects)
LOT_SIZE_SCHEDULE = [
    (date(2021, 4, 29), 75, "Pre-May 2021 Contracts (75 qty)"),
    (date(2024, 4, 25), 50, "May 2021 - Apr 2024 Revision (50 qty)"),
    (date(2024, 11, 19), 25, "May 2024 - Nov 2024 Revision (25 qty)"),
    (date(2025, 12, 30), 75, "Nov 20, 2024 - Dec 30, 2025 Revision (75 qty)"),
    (date(2099, 12, 31), 65, "Dec 31, 2025 Onward / 2026 Series (65 qty, NSE FAOP/70616)")
]

def _to_date(trade_date: Union[str, date, datetime, pd.Timestamp]) -> date:
    """Normalize any date format into a standard datetime.date object."""
    if isinstance(trade_date, str):
        return pd.to_datetime(trade_date).date()
    elif isinstance(trade_date, pd.Timestamp):
        return trade_date.date()
    elif isinstance(trade_date, datetime):
        return trade_date.date()
    elif isinstance(trade_date, date):
        return trade_date
    else:
        raise TypeError(f"Unsupported trade_date type: {type(trade_date)}")

def get_nifty_lot_size(trade_date: Union[str, date, datetime, pd.Timestamp]) -> int:
    """
    Returns the exact historical NIFTY 50 contract lot size for any trade date.
    
    Parameters:
        trade_date: String 'YYYY-MM-DD', datetime, date, or pd.Timestamp
        
    Returns:
        int: 75, 50, 25, 75, or 65
    """
    d = _to_date(trade_date)
    for end_date, lot_size, _ in LOT_SIZE_SCHEDULE:
        if d <= end_date:
            return lot_size
    return 65

def get_nifty_lot_size_series(dates_series: pd.Series) -> pd.Series:
    """
    Vectorized lot size lookup for high-performance backtesting on pandas Series.
    """
    dt_series = pd.to_datetime(dates_series)
    
    conds = [
        dt_series <= pd.to_datetime('2021-04-29'),
        (dt_series > pd.to_datetime('2021-04-29')) & (dt_series <= pd.to_datetime('2024-04-25')),
        (dt_series > pd.to_datetime('2024-04-25')) & (dt_series <= pd.to_datetime('2024-11-19')),
        (dt_series > pd.to_datetime('2024-11-19')) & (dt_series <= pd.to_datetime('2025-12-30')),
        dt_series > pd.to_datetime('2025-12-30')
    ]
    choices = [75, 50, 25, 75, 65]
    return pd.Series(np.select(conds, choices, default=65), index=dates_series.index)

def get_lot_size_history() -> pd.DataFrame:
    """Returns a DataFrame summarizing all 5 historical NIFTY lot size periods."""
    records = [
        {
            "Period": "Period 1",
            "Start Date": "Inception",
            "End Date": "2021-04-29",
            "Lot Size": 75,
            "Circular Reference": "Traditional Contract Size"
        },
        {
            "Period": "Period 2",
            "Start Date": "2021-04-30",
            "End Date": "2024-04-25",
            "Lot Size": 50,
            "Circular Reference": "NSE Circular NSE/FAOP/47784 (Mar 25, 2021)"
        },
        {
            "Period": "Period 3",
            "Start Date": "2024-04-26",
            "End Date": "2024-11-19",
            "Lot Size": 25,
            "Circular Reference": "NSE Circular NSE/FAOP/61517 (Apr 05, 2024)"
        },
        {
            "Period": "Period 4",
            "Start Date": "2024-11-20",
            "End Date": "2025-12-30",
            "Lot Size": 75,
            "Circular Reference": "SEBI/HO/MRD/TPD-1/P/CIR/2024/132 & NSE/FAOP/64516"
        },
        {
            "Period": "Period 5",
            "Start Date": "2025-12-31",
            "End Date": "Present (2026+)",
            "Lot Size": 65,
            "Circular Reference": "NSE Circular FAOP/70616 (Effective Jan 2026 Series)"
        }
    ]
    return pd.DataFrame(records)

if __name__ == "__main__":
    print("================================================================================")
    print("      NIFTY 50 HISTORICAL LOT SIZE VERIFICATION (5 REGULATORY PERIODS)         ")
    print("================================================================================")
    sample_dates = [
        ("2020 Sample", "2020-07-15"),
        ("2022 Sample", "2022-09-15"),
        ("Aug 2024 Sample", "2024-08-15"),
        ("Jan 2025 Sample", "2025-01-15"),
        ("Feb 2026 Sample", "2026-02-15")
    ]
    for label, d in sample_dates:
        ls = get_nifty_lot_size(d)
        print(f"  • {label:<16} ({d}) -> Lot Size = {ls} Qty")

    print("\n--------------------------------------------------------------------------------")
    print("Complete Historical Schedule:")
    print("--------------------------------------------------------------------------------")
    print(get_lot_size_history().to_string(index=False))
    print("================================================================================")
