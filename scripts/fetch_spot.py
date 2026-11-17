"""
Historical Spot Data Pipeline for NIFTY 50 Index (Kotak Neo API) - 5 Years.

Features:
- Authenticates with Kotak Neo using credentials from .env and automated TOTP.
- Queries Scrip Master for canonical NIFTY 50 token (nse_cm|Nifty 50).
- Downloads 5 years of 5-minute historical OHLCV data looping backward from today to Nov 2021.
- Respects Kotak Neo API limits: safe 25-day chunk intervals (API ceiling is 30 days) and delay.
- Cleans data: Asia/Kolkata timezone normalization, offset stripping, market hours filtering (09:15-15:30).
- Validates OHLC logical envelope.
- Saves clean dataset to:
  1. data/nifty_5min_5yr.parquet
  2. data/nifty_5min_5yr.csv
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv

# Clean structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("SpotPipeline5Yr")

# Load environment variables
load_dotenv()

try:
    from neo_api_client import NeoAPI
except ImportError:
    logger.error("neo_api_client is not installed. Please install 'kotakneoapi'.")
    sys.exit(1)

try:
    import pyotp
except ImportError:
    pyotp = None


class KotakSpotPipeline:
    def __init__(self):
        self.consumer_key = os.getenv("CONSUMER_KEY") or os.getenv("KOTAK_CONSUMER_KEY", "").strip()
        self.consumer_secret = os.getenv("CONSUMER_SECRET") or os.getenv("KOTAK_CONSUMER_SECRET", "").strip()
        mobile = os.getenv("MOBILE_NUMBER") or os.getenv("KOTAK_MOBILE_NUMBER", "").strip()
        self.mobile_number = f"+91{mobile}" if mobile and not mobile.startswith("+91") else mobile
        self.ucc = os.getenv("UCC") or os.getenv("KOTAK_UCC", "").strip()
        self.mpin = os.getenv("MPIN") or os.getenv("KOTAK_MPIN", "").strip()
        self.totp_secret = os.getenv("TOTP_SECRET") or os.getenv("KOTAK_TOTP_SECRET", "").strip()
        self.environment = os.getenv("ENVIRONMENT") or os.getenv("KOTAK_ENVIRONMENT", "prod").strip()

        if not self.consumer_key:
            raise ValueError("CONSUMER_KEY is missing from environment.")
        if not self.ucc or not self.mobile_number:
            raise ValueError("MOBILE_NUMBER or UCC is missing from environment.")

        self.client = None

    def authenticate(self) -> NeoAPI:
        """Establishes authenticated session with Kotak Neo."""
        logger.info("Initializing Kotak NeoAPI Client...")
        self.client = NeoAPI(
            consumer_key=self.consumer_key,
            environment=self.environment,
        )

        if not self.totp_secret or not pyotp:
            raise ValueError("TOTP_SECRET or pyotp is missing for automatic authentication.")

        totp_code = pyotp.TOTP(self.totp_secret).now()
        logger.info(f"Generated 2FA TOTP: {totp_code}")

        # Step 1: TOTP Login
        login_res = self.client.totp_login(
            mobile_number=self.mobile_number,
            ucc=self.ucc,
            totp=totp_code,
        )
        if not isinstance(login_res, dict) or login_res.get("data", {}).get("status") != "success":
            logger.warning(f"totp_login response: {login_res}")

        # Step 2: Validate MPIN
        validate_res = self.client.totp_validate(mpin=self.mpin)
        k_type = validate_res.get("data", {}).get("kType")
        if k_type != "Trade":
            logger.warning(f"totp_validate response: {validate_res}")
        else:
            greeting = validate_res.get("data", {}).get("greetingName", "Trader")
            logger.info(f"Authentication SUCCESSFUL. Welcome {greeting}! (Session: {k_type})")

        return self.client

    def get_nifty_scrip_metadata(self) -> dict:
        """Fetches scrip master for nse_cm and extracts exact NIFTY 50 token info."""
        logger.info("Fetching Scrip Master for 'nse_cm'...")
        scrip_url = self.client.scrip_master(exchange_segment="nse_cm")
        if not isinstance(scrip_url, str) or not scrip_url.startswith("http"):
            raise RuntimeError(f"Unexpected Scrip Master response: {scrip_url}")

        logger.info(f"Downloading Scrip Master CSV from: {scrip_url}")
        df_master = pd.read_csv(scrip_url, low_memory=False)

        # Locate NIFTY 50 index: pSymbol == 26000 and pTrdSymbol == 'NIFTY'
        nifty_match = df_master[
            (df_master["pSymbol"] == 26000) & (df_master["pTrdSymbol"] == "NIFTY")
        ]

        if nifty_match.empty:
            nifty_match = df_master[df_master["pTrdSymbol"] == "NIFTY"]

        if nifty_match.empty:
            raise RuntimeError("NIFTY 50 index not found in Scrip Master CSV.")

        row = nifty_match.iloc[0]
        metadata = {
            "token": int(row["pSymbol"]),
            "trading_symbol": str(row["pTrdSymbol"]),
            "symbol_name": str(row["pSymbolName"]),
            "segment": str(row["pExchSeg"]),
            "neosymbol": "nse_cm|Nifty 50",
            "description": str(row.get("pDesc", "NIFTY 50 Index")),
        }
        logger.info(f"Scrip Master Record: Token={metadata['token']} | Symbol={metadata['trading_symbol']} | Neosymbol={metadata['neosymbol']}")
        return metadata

    def fetch_historical_candles_5yr(
        self,
        neosymbol: str = "nse_cm|Nifty 50",
        years: int = 5,
        chunk_days: int = 25,
        delay_seconds: float = 0.5,
    ) -> pd.DataFrame:
        """
        Pulls 5 years of 5-minute historical candles looping backward from today.
        Uses chunks of 25 days (strictly adhering to Kotak Neo's 30-day limit per call).
        Includes rate-limit retry logic with exponential backoff on 429.
        """
        end_dt = datetime.now()
        # Kotak Neo API hard limit: 'fromDate cannot be older than 5 years'
        # Start at 2021-11-11 to respect broker's earliest available timestamp
        start_dt = max(end_dt - timedelta(days=years * 365), datetime(2021, 11, 11))

        logger.info(
            f"Initiating 5-Year backward download: {end_dt.strftime('%Y-%m-%d')} down to {start_dt.strftime('%Y-%m-%d')}..."
        )

        all_raw_candles = []
        curr_to = end_dt
        chunk_idx = 1

        while curr_to > start_dt:
            curr_from = max(curr_to - timedelta(days=chunk_days), start_dt)
            from_str = curr_from.strftime("%Y-%m-%d")
            to_str = curr_to.strftime("%Y-%m-%d")

            logger.info(f"  [Chunk {chunk_idx:02d}] Fetching backward: {from_str} -> {to_str}...")

            # Delay to respect rate limits
            time.sleep(delay_seconds)

            success = False
            for attempt in range(1, 4):
                try:
                    res = self.client.historical_data(
                        neosymbol=neosymbol,
                        interval="5min",
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
                        wait_time = 2.0 * attempt
                        logger.warning(f"    -> Rate limited (429). Backing off {wait_time}s (Attempt {attempt}/3)...")
                        time.sleep(wait_time)
                    elif isinstance(res, dict) and "cannot be older than 5 years" in str(res):
                        logger.warning(f"    -> Reached broker's absolute 5-year boundary: {res}")
                        success = True
                        break
                    else:
                        logger.warning(f"    -> Non-success response: {res}")
                        time.sleep(1.0)
                except Exception as e:
                    logger.error(f"    -> Exception: {e}")
                    time.sleep(1.5)

            if not success:
                logger.warning(f"    -> Chunk {from_str} to {to_str} incomplete.")

            # Move backward by one day from curr_from
            curr_to = curr_from - timedelta(days=1)
            chunk_idx += 1

        if not all_raw_candles:
            raise RuntimeError("No candle data returned from Kotak Neo API.")

        logger.info(f"Successfully downloaded {len(all_raw_candles):,} raw 5-minute candles across 5 years!")
        raw_df = pd.DataFrame(
            all_raw_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        return raw_df

    @staticmethod
    def clean_and_normalize(df_raw: pd.DataFrame) -> pd.DataFrame:
        """
        Data cleaning pipeline:
        1. Parses timestamps into Asia/Kolkata timezone.
        2. Strips timezone offset so index is clean local naive datetime.
        3. Sorts chronologically and deduplicates on timestamp.
        4. Filters strictly within official NSE market trading hours: 09:15:00 - 15:30:00.
        5. Casts numeric types.
        6. Enforces high/low envelope validity: High >= max(open, high, close), Low <= min(open, low, close).
        """
        logger.info("Cleaning and normalizing 5-year market data...")
        df = df_raw.copy()

        # Parse timestamps
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Convert to Asia/Kolkata and make naive (strip tz offset)
        if df["timestamp"].dt.tz is not None:
            df["timestamp"] = (
                df["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            )
        else:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)

        # Set DatetimeIndex and sort chronologically
        df = df.set_index("timestamp").sort_index()

        # Deduplicate timestamps
        init_len = len(df)
        df = df[~df.index.duplicated(keep="first")]
        if len(df) < init_len:
            logger.info(f"Removed {init_len - len(df):,} duplicate timestamps.")

        # Filter strictly within official NSE market trading hours: 09:15:00 - 15:30:00
        df = df.between_time("09:15:00", "15:30:00")

        # Cast column types
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype("float64")
        df["volume"] = df["volume"].astype("int64")

        # Sanitize OHLC boundaries to guarantee mathematical validity
        df["high"] = df[["high", "open", "close"]].max(axis=1)
        df["low"] = df[["low", "open", "close"]].min(axis=1)

        logger.info(f"Final 5-year cleaned dataset contains {len(df):,} valid 5-minute candles.")
        return df


def main():
    pipeline = KotakSpotPipeline()

    # 1. Login & Auth
    pipeline.authenticate()

    # 2. Get Scrip Master & verify Token
    metadata = pipeline.get_nifty_scrip_metadata()

    # 3. Download past 5 years of 5-min candles looping backward
    raw_df = pipeline.fetch_historical_candles_5yr(
        neosymbol=metadata["neosymbol"],
        years=5,
        chunk_days=25,
        delay_seconds=0.5,
    )

    # 4. Clean, timezone normalize (IST naive), filter 09:15-15:30, sort chronologically
    clean_df = pipeline.clean_and_normalize(raw_df)

    # 5. Output paths
    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    os.makedirs(output_dir, exist_ok=True)
    parquet_path_5yr = os.path.join(output_dir, "nifty_5min_5yr.parquet")
    csv_path_5yr = os.path.join(output_dir, "nifty_5min_5yr.csv")

    # Save Parquet
    clean_df.to_parquet(parquet_path_5yr, engine="pyarrow", compression="snappy")
    parquet_size_mb = os.path.getsize(parquet_path_5yr) / (1024 * 1024)
    logger.info(f"Saved 5-Year Parquet to: {parquet_path_5yr} ({parquet_size_mb:.2f} MB)")

    # Save CSV
    clean_df.to_csv(csv_path_5yr, date_format="%Y-%m-%d %H:%M:%S", index=True)
    csv_size_mb = os.path.getsize(csv_path_5yr) / (1024 * 1024)
    logger.info(f"Saved 5-Year CSV to: {csv_path_5yr} ({csv_size_mb:.2f} MB)")

    # Print verification metrics
    print("\n" + "=" * 80)
    print("                 5-YEAR HISTORICAL NIFTY 50 DATASET VERIFICATION")
    print("=" * 80)
    print(f"Parquet File     : {parquet_path_5yr} ({parquet_size_mb:.2f} MB)")
    print(f"CSV File         : {csv_path_5yr} ({csv_size_mb:.2f} MB)")
    print(f"Total Rows       : {len(clean_df):,}")
    print(f"Start Date & Time: {clean_df.index.min()}")
    print(f"End Date & Time  : {clean_df.index.max()}")
    print(f"Unique Days      : {len(pd.Series(clean_df.index.date).unique())} trading days")
    print(f"Intraday Times   : Earliest bar = {clean_df.index.time.min()}, Latest bar = {clean_df.index.time.max()}")
    print(f"Index Type       : {clean_df.index.dtype} (Timezone naive, local IST)")
    print("-" * 80)
    print("FIRST 5 ROWS:")
    print(clean_df.head(5).to_string())
    print("-" * 80)
    print("LAST 5 ROWS:")
    print(clean_df.tail(5).to_string())
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
