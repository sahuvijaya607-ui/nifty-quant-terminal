"""
Multi-Timeframe Historical Data Pipeline for NIFTY 50 (Kotak Neo API) - 5 Years.

Downloads, cleans, and saves:
1. 15-Minute Data : data/nifty_15min_5yr.parquet & .csv (interval='15min')
2. 1-Hour Data    : data/nifty_1hr_5yr.parquet & .csv   (interval='60min', aligned to 09:15, 10:15...)
3. 1-Day Data     : data/nifty_1day_5yr.parquet & .csv  (interval='D', formatted as YYYY-MM-DD)

Strict IST timezone (Asia/Kolkata), offset stripping, deduplication, and OHLC validation.
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

# Clean logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("MultiTimeframePipeline")

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


class KotakMultiTimeframeDownloader:
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
        """Establishes authenticated session with Kotak Neo."""
        logger.info("Initializing Kotak NeoAPI Client...")
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

    def fetch_timeframe_candles(
        self,
        interval_code: str,
        chunk_days: int,
        label: str,
        delay_seconds: float = 0.5,
        years: int = 5,
    ) -> pd.DataFrame:
        """
        Pulls candles looping backward from today to 2021-11-11 (broker 5yr ceiling).
        Handles rate limits with exponential backoff.
        """
        end_dt = datetime.now()
        start_dt = max(end_dt - timedelta(days=years * 365), datetime(2021, 11, 11))

        logger.info(
            f"\n>>> Starting 5-Year download for [{label}] ({interval_code}): {end_dt.strftime('%Y-%m-%d')} down to {start_dt.strftime('%Y-%m-%d')} (Chunk: {chunk_days}d)..."
        )

        all_raw_candles = []
        curr_to = end_dt
        chunk_idx = 1

        while curr_to > start_dt:
            curr_from = max(curr_to - timedelta(days=chunk_days), start_dt)
            from_str = curr_from.strftime("%Y-%m-%d")
            to_str = curr_to.strftime("%Y-%m-%d")

            logger.info(f"  [{label} Chunk {chunk_idx:02d}] {from_str} -> {to_str}...")
            time.sleep(delay_seconds)

            success = False
            for attempt in range(1, 4):
                try:
                    res = self.client.historical_data(
                        neosymbol=self.neosymbol,
                        interval=interval_code,
                        from_date=from_str,
                        to_date=to_str,
                    )
                    if isinstance(res, dict) and res.get("status") == "SUCCESS":
                        candles = res.get("data", {}).get("candles", [])
                        logger.info(f"    -> Got {len(candles):,} candles (Accumulated: {len(all_raw_candles) + len(candles):,})")
                        all_raw_candles.extend(candles)
                        success = True
                        break
                    elif isinstance(res, dict) and res.get("code") == 429:
                        wait = 2.0 * attempt
                        logger.warning(f"    -> Rate limited (429). Retrying in {wait}s...")
                        time.sleep(wait)
                    elif isinstance(res, dict) and "cannot be older than 5 years" in str(res):
                        logger.warning(f"    -> Hit broker's absolute 5-year ceiling: {res}")
                        success = True
                        break
                    else:
                        logger.warning(f"    -> Non-success response: {res}")
                        time.sleep(1.0)
                except Exception as e:
                    logger.error(f"    -> Error: {e}")
                    time.sleep(1.5)

            if not success:
                logger.warning(f"    -> Chunk {from_str} to {to_str} incomplete.")

            curr_to = curr_from - timedelta(days=1)
            chunk_idx += 1

        if not all_raw_candles:
            raise RuntimeError(f"No candles returned for interval {interval_code}.")

        raw_df = pd.DataFrame(
            all_raw_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        return raw_df

    @staticmethod
    def clean_intraday_data(df_raw: pd.DataFrame, label: str) -> pd.DataFrame:
        """Cleans intraday data (15min, 1hr): time filter, IST naive, envelope check."""
        df = df_raw.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        if df["timestamp"].dt.tz is not None:
            df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        else:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)

        df = df.set_index("timestamp").sort_index()
        df = df[~df.index.duplicated(keep="first")]

        # Strictly filter between 09:15:00 and 15:30:00
        df = df.between_time("09:15:00", "15:30:00")

        # Cast column types
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype("float64")
        df["volume"] = df["volume"].astype("int64")

        # Sanitize OHLC envelope
        df["high"] = df[["high", "open", "close"]].max(axis=1)
        df["low"] = df[["low", "open", "close"]].min(axis=1)

        logger.info(f"[{label}] Cleaned: {len(df):,} candles ({df.index.min()} to {df.index.max()})")
        return df

    @staticmethod
    def clean_daily_data(df_raw: pd.DataFrame) -> pd.DataFrame:
        """Cleans Daily data (1day): date formatting as YYYY-MM-DD, envelope check."""
        df = df_raw.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        if df["timestamp"].dt.tz is not None:
            df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        else:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)

        # Normalize to date (midnight / YYYY-MM-DD)
        df["timestamp"] = pd.to_datetime(df["timestamp"].dt.date)
        df = df.set_index("timestamp").sort_index()
        df = df[~df.index.duplicated(keep="first")]

        # Cast column types
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype("float64")
        df["volume"] = df["volume"].astype("int64")

        # Sanitize OHLC envelope
        df["high"] = df[["high", "open", "close"]].max(axis=1)
        df["low"] = df[["low", "open", "close"]].min(axis=1)

        logger.info(f"[1-Day] Cleaned: {len(df):,} daily bars ({df.index.min().strftime('%Y-%m-%d')} to {df.index.max().strftime('%Y-%m-%d')})")
        return df


def run_pipeline():
    downloader = KotakMultiTimeframeDownloader()
    downloader.authenticate()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)

    # -------------------------------------------------------------
    # 1. 15-Minute Data (nifty_15min_5yr)
    # Kotak limit: <= 60 days per call -> safe 45-day chunks
    # -------------------------------------------------------------
    raw_15m = downloader.fetch_timeframe_candles(
        interval_code="15min",
        chunk_days=45,
        label="15-Minute",
        delay_seconds=0.5,
    )
    clean_15m = downloader.clean_intraday_data(raw_15m, "15-Minute")

    pq_15m = os.path.join(data_dir, "nifty_15min_5yr.parquet")
    csv_15m = os.path.join(data_dir, "nifty_15min_5yr.csv")
    clean_15m.to_parquet(pq_15m, engine="pyarrow", compression="snappy")
    clean_15m.to_csv(csv_15m, date_format="%Y-%m-%d %H:%M:%S", index=True)
    logger.info(f"Saved: {pq_15m} & {csv_15m}")

    # -------------------------------------------------------------
    # 2. 1-Hour Data (nifty_1hr_5yr)
    # Kotak limit: <= 90 days per call -> safe 60-day chunks
    # -------------------------------------------------------------
    raw_1h = downloader.fetch_timeframe_candles(
        interval_code="60min",
        chunk_days=60,
        label="1-Hour",
        delay_seconds=0.5,
    )
    clean_1h = downloader.clean_intraday_data(raw_1h, "1-Hour")

    # Verify hourly alignment (09:15, 10:15, etc.)
    unique_times = sorted(clean_1h.index.time)
    unique_time_strs = sorted(list(set([t.strftime("%H:%M") for t in unique_times])))
    logger.info(f"1-Hour Candle Time Slots: {unique_time_strs}")

    pq_1h = os.path.join(data_dir, "nifty_1hr_5yr.parquet")
    csv_1h = os.path.join(data_dir, "nifty_1hr_5yr.csv")
    clean_1h.to_parquet(pq_1h, engine="pyarrow", compression="snappy")
    clean_1h.to_csv(csv_1h, date_format="%Y-%m-%d %H:%M:%S", index=True)
    logger.info(f"Saved: {pq_1h} & {csv_1h}")

    # -------------------------------------------------------------
    # 3. 1-Day Data (nifty_1day_5yr)
    # Kotak limit: <= 180 days per call -> safe 120-day chunks
    # -------------------------------------------------------------
    raw_1d = downloader.fetch_timeframe_candles(
        interval_code="D",
        chunk_days=120,
        label="1-Day",
        delay_seconds=0.5,
    )
    clean_1d = downloader.clean_daily_data(raw_1d)

    pq_1d = os.path.join(data_dir, "nifty_1day_5yr.parquet")
    csv_1d = os.path.join(data_dir, "nifty_1day_5yr.csv")
    clean_1d.to_parquet(pq_1d, engine="pyarrow", compression="snappy")
    clean_1d.to_csv(csv_1d, date_format="%Y-%m-%d", index=True)
    logger.info(f"Saved: {pq_1d} & {csv_1d}")

    print("\n" + "=" * 80)
    print("        ALL 3 TIMEFRAMES DOWNLOADED & PERSISTED SUCCESSFULLY!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    run_pipeline()
