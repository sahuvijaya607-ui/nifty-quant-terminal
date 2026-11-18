"""
5-Year 1-Minute Historical OHLCV Data Pipeline for NIFTY 50 (Kotak Neo API).

Workflow:
1. Authenticates with Kotak Neo using .env credentials & automated TOTP.
2. Fetches 5 years of 1-minute historical candles looping backward from today to Nov 2021.
3. Uses safe 20-day chunks with rate-limit delay and backoff retry.
4. Normalizes timestamps to IST (Asia/Kolkata), strips timezone offset, sorts chronologically,
   and strictly filters intraday trading hours (09:15:00 to 15:29:00).
5. Enforces mathematical OHLC boundary envelope (High >= max, Low <= min).
6. Saves to both data/nifty_1min_5yr.parquet and data/nifty_1min_5yr.csv.
7. Executes a rigorous quantitative audit.
"""

import os
import sys
import time
import datetime
import logging
from datetime import datetime as dt, timedelta
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("1MinPipeline")

load_dotenv()

try:
    from neo_api_client import NeoAPI
except ImportError:
    logger.error("neo_api_client is not installed.")
    sys.exit(1)

try:
    import pyotp
except ImportError:
    pyotp = None


class Kotak1MinPipeline:
    def __init__(self):
        self.consumer_key = os.getenv("CONSUMER_KEY") or os.getenv("KOTAK_CONSUMER_KEY", "").strip()
        mobile = os.getenv("MOBILE_NUMBER") or os.getenv("KOTAK_MOBILE_NUMBER", "").strip()
        self.mobile_number = f"+91{mobile}" if mobile and not mobile.startswith("+91") else mobile
        self.ucc = os.getenv("UCC") or os.getenv("KOTAK_UCC", "").strip()
        self.mpin = os.getenv("MPIN") or os.getenv("KOTAK_MPIN", "").strip()
        self.totp_secret = os.getenv("TOTP_SECRET") or os.getenv("KOTAK_TOTP_SECRET", "").strip()
        self.environment = os.getenv("ENVIRONMENT") or os.getenv("KOTAK_ENVIRONMENT", "prod").strip()

        if not self.consumer_key or not self.ucc or not self.mobile_number:
            raise ValueError("Credentials missing in .env.")

        self.client = None
        self.neosymbol = "nse_cm|Nifty 50"

    def authenticate(self) -> NeoAPI:
        logger.info("Authenticating with Kotak Neo API...")
        self.client = NeoAPI(
            consumer_key=self.consumer_key,
            environment=self.environment,
        )

        totp_code = pyotp.TOTP(self.totp_secret).now()
        login_res = self.client.totp_login(
            mobile_number=self.mobile_number,
            ucc=self.ucc,
            totp=totp_code,
        )
        validate_res = self.client.totp_validate(mpin=self.mpin)
        k_type = validate_res.get("data", {}).get("kType")
        greeting = validate_res.get("data", {}).get("greetingName", "Trader")
        logger.info(f"Authentication SUCCESSFUL. Welcome {greeting}! (Session: {k_type})")
        return self.client

    def fetch_1min_candles_5yr(
        self,
        chunk_days: int = 20,
        delay_seconds: float = 0.5,
        years: int = 5,
    ) -> pd.DataFrame:
        """
        Fetches 1-minute historical candles looping backward from today to 2021-11-11.
        Chunk size = 20 days (strictly within Kotak Neo's 30-day limit for 1min interval).
        """
        end_dt = dt.now()
        start_dt = max(end_dt - timedelta(days=years * 365), dt(2021, 11, 11))

        logger.info(
            f"\n>>> Starting 5-Year 1-Minute download: {end_dt.strftime('%Y-%m-%d')} down to {start_dt.strftime('%Y-%m-%d')} (Chunk size: {chunk_days} days)..."
        )

        all_raw_candles = []
        curr_to = end_dt
        chunk_idx = 1

        while curr_to > start_dt:
            curr_from = max(curr_to - timedelta(days=chunk_days), start_dt)
            from_str = curr_from.strftime("%Y-%m-%d")
            to_str = curr_to.strftime("%Y-%m-%d")

            logger.info(f"  [Chunk {chunk_idx:03d}] Fetching: {from_str} -> {to_str}...")
            time.sleep(delay_seconds)

            success = False
            for attempt in range(1, 4):
                try:
                    res = self.client.historical_data(
                        neosymbol=self.neosymbol,
                        interval="1min",
                        from_date=from_str,
                        to_date=to_str,
                    )
                    if isinstance(res, dict) and res.get("status") == "SUCCESS":
                        candles = res.get("data", {}).get("candles", [])
                        logger.info(f"    -> Received {len(candles):,} candles (Accumulated: {len(all_raw_candles) + len(candles):,})")
                        all_raw_candles.extend(candles)
                        success = True
                        break
                    elif isinstance(res, dict) and res.get("code") == 429:
                        wait = 2.0 * attempt
                        logger.warning(f"    -> Rate limited (429). Retrying in {wait}s (Attempt {attempt}/3)...")
                        time.sleep(wait)
                    elif isinstance(res, dict) and "cannot be older than 5 years" in str(res):
                        logger.warning(f"    -> Reached broker's 5-year boundary: {res}")
                        success = True
                        break
                    else:
                        logger.warning(f"    -> Response: {res}")
                        time.sleep(1.0)
                except Exception as e:
                    logger.error(f"    -> Exception: {e}")
                    time.sleep(1.5)

            if not success:
                logger.warning(f"    -> Chunk {from_str} to {to_str} could not be retrieved.")

            curr_to = curr_from - timedelta(days=1)
            chunk_idx += 1

        if not all_raw_candles:
            raise RuntimeError("No 1-minute candles received from Kotak Neo API.")

        logger.info(f"Total raw 1-minute candles downloaded: {len(all_raw_candles):,}")
        raw_df = pd.DataFrame(
            all_raw_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        return raw_df

    @staticmethod
    def clean_and_normalize(df_raw: pd.DataFrame) -> pd.DataFrame:
        """
        Cleans and aligns 1-minute candles:
        - Asia/Kolkata timezone normalization and offset stripping.
        - Deduplication and chronological sorting.
        - Strict intraday filter: 09:15:00 to 15:29:00.
        - Mathematical OHLC boundary envelope sanitization.
        """
        logger.info("Cleaning and normalizing 1-minute dataset...")
        df = df_raw.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        if df["timestamp"].dt.tz is not None:
            df["timestamp"] = (
                df["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            )
        else:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)

        df = df.set_index("timestamp").sort_index()

        init_len = len(df)
        df = df[~df.index.duplicated(keep="first")]
        if len(df) < init_len:
            logger.info(f"Removed {init_len - len(df):,} duplicate candles.")

        # Strictly filter between 09:15:00 and 15:29:00 as requested
        df = df.between_time("09:15:00", "15:29:00")

        # Cast datatypes
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype("float64")
        df["volume"] = df["volume"].astype("int64")

        # Sanitize OHLC envelope
        df["high"] = df[["high", "open", "close"]].max(axis=1)
        df["low"] = df[["low", "open", "close"]].min(axis=1)

        logger.info(f"Cleaned 1-minute dataset: {len(df):,} candles ({df.index.min()} to {df.index.max()})")
        return df


def run_pipeline():
    pipeline = Kotak1MinPipeline()
    pipeline.authenticate()

    # 1. Fetch past 5 years in 20-day chunks
    raw_df = pipeline.fetch_1min_candles_5yr(
        chunk_days=20,
        delay_seconds=0.5,
        years=5,
    )

    # 2. Clean, deduplicate, filter 09:15-15:29, sort
    clean_df = pipeline.clean_and_normalize(raw_df)

    # 3. Save to data/nifty_1min_5yr.parquet and data/nifty_1min_5yr.csv
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)

    pq_path = os.path.join(data_dir, "nifty_1min_5yr.parquet")
    csv_path = os.path.join(data_dir, "nifty_1min_5yr.csv")

    clean_df.to_parquet(pq_path, engine="pyarrow", compression="snappy")
    pq_size_mb = os.path.getsize(pq_path) / (1024 * 1024)
    logger.info(f"Saved Parquet: {pq_path} ({pq_size_mb:.2f} MB)")

    clean_df.to_csv(csv_path, date_format="%Y-%m-%d %H:%M:%S", index=True)
    csv_size_mb = os.path.getsize(csv_path) / (1024 * 1024)
    logger.info(f"Saved CSV    : {csv_path} ({csv_size_mb:.2f} MB)")

    # 4. Audit & Verification Output
    total_candles = len(clean_df)
    unique_dates = pd.Series(clean_df.index.date).unique()
    total_trading_days = len(unique_dates)
    avg_bars_per_day = total_candles / total_trading_days if total_trading_days > 0 else 0
    null_counts = clean_df.isnull().sum()
    duplicate_count = clean_df.index.duplicated().sum()

    times = clean_df.index.time
    open_time = datetime.time(9, 15, 0)
    close_time = datetime.time(15, 29, 0)
    outside_candles = clean_df[(times < open_time) | (times > close_time)]

    print("\n" + "=" * 85)
    print("               QUANTITATIVE DATA AUDIT: NIFTY 1-MINUTE 5-YEAR DATASET")
    print("=" * 85)
    print(f"File Paths       : Parquet = {pq_path} ({pq_size_mb:.2f} MB)")
    print(f"                   CSV     = {csv_path} ({csv_size_mb:.2f} MB)")
    print(f"Total Rows       : {total_candles:,} candles")
    print(f"Date Range       : {clean_df.index.min()} to {clean_df.index.max()}")
    print(f"Trading Days     : {total_trading_days:,} days")
    print(f"Avg Candles/Day  : {avg_bars_per_day:.2f} bars/day (Theoretical max: 375 bars/day)")
    print(f"Intraday Window  : Earliest bar = {clean_df.index.time.min()} | Latest bar = {clean_df.index.time.max()}")
    print(f"Integrity Check  : {null_counts.sum()} Nulls | {duplicate_count} Duplicates | {len(outside_candles)} Outside [09:15-15:29]")
    print("-" * 85)
    print("FIRST 3 ROWS (head(3)):")
    print(clean_df.head(3).to_string())
    print("-" * 85)
    print("LAST 3 ROWS (tail(3)):")
    print(clean_df.tail(3).to_string())
    print("=" * 85 + "\n")


if __name__ == "__main__":
    run_pipeline()
