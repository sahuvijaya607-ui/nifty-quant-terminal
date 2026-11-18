"""
Kotak Neo API Session Authenticator.
Loads credentials from .env, computes dynamic TOTP (if secret is configured),
and creates a verified session using the official NeoAPI client.
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Configure clean logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("KotakLogin")

# Load environment variables
load_dotenv()

try:
    from neo_api_client import NeoAPI
except ImportError:
    logger.error("neo_api_client is not installed in the active environment.")
    NeoAPI = None

try:
    import pyotp
except ImportError:
    pyotp = None


class KotakNeoAuth:
    def __init__(self):
        self.consumer_key = os.getenv("CONSUMER_KEY") or os.getenv("KOTAK_CONSUMER_KEY", "").strip()
        self.consumer_secret = os.getenv("CONSUMER_SECRET") or os.getenv("KOTAK_CONSUMER_SECRET", "").strip()
        self.mobile_number = os.getenv("MOBILE_NUMBER") or os.getenv("KOTAK_MOBILE_NUMBER", "").strip()
        self.password = os.getenv("PASSWORD") or os.getenv("KOTAK_PASSWORD", "").strip()
        self.mpin = os.getenv("MPIN") or os.getenv("KOTAK_MPIN", "").strip()
        self.totp_secret = os.getenv("TOTP_SECRET") or os.getenv("KOTAK_TOTP_SECRET", "").strip()
        self.environment = os.getenv("ENVIRONMENT") or os.getenv("KOTAK_ENVIRONMENT", "prod").strip()
        self.client = None

    def get_client(self):
        """Initializes and returns authenticated NeoAPI client."""
        if not self.consumer_key:
            raise ValueError("CONSUMER_KEY is missing in .env")

        self.client = NeoAPI(
            consumer_key=self.consumer_key,
            environment=self.environment,
        )
        logger.info("Kotak NeoAPI client initialized.")
        return self.client

    def login(self):
        """Executes full login and 2FA session flow."""
        client = self.get_client()

        logger.info(f"Logging in with registered mobile: {self.mobile_number[:6]}XXXX")
        login_res = client.login(mobilenumber=self.mobile_number, password=self.password)
        logger.info(f"Login Response: {login_res}")

        # Compute dynamic TOTP or use MPIN
        otp_to_send = None
        if self.totp_secret and pyotp:
            totp = pyotp.TOTP(self.totp_secret)
            otp_to_send = totp.now()
            logger.info(f"Generated TOTP automatically via pyotp: {otp_to_send}")
        elif self.mpin:
            otp_to_send = self.mpin
            logger.info("Using MPIN as 2FA code.")

        if otp_to_send:
            session_res = client.session_2fa(OTP=otp_to_send)
            logger.info(f"Session 2FA Response: {session_res}")
            return client, session_res
        else:
            logger.warning("No TOTP secret or MPIN available for automatic 2FA verification.")
            return client, login_res


if __name__ == "__main__":
    auth = KotakNeoAuth()
    print("Kotak Neo Authenticator loaded.")
    print(f"Configured Mobile: {auth.mobile_number[:6]}XXXX")
    print(f"Consumer Key: {auth.consumer_key[:8]}...")
    if auth.totp_secret and pyotp:
        totp = pyotp.TOTP(auth.totp_secret)
        print(f"Live TOTP Code Generated: {totp.now()}")
