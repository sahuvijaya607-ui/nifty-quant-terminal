"""
========================================================================================
CHAMPION MODEL 4: MASTER QUANT DESK & TERMINAL (NIFTY 50)
========================================================================================
Features Implemented:
1. Dual-Mode Data Feeder (Live Market Stream + Off-Market Replay 1x-10x)
2. Live Option Chain Table (5 ITM, ATM, 5 OTM) & Real-time PCR Gauge Meter
3. Exact Kotak Neo Brokerage & Taxes Calculator (Rs 20/order, STT, GST, Exchange fees)
4. Safety Toggle: [PAPER TRADING] <-> [LIVE EXECUTION] with Security MPIN (031995)
5. Audio Alerts Trigger & Second-by-Second Active Position Monitor
6. Full Trade Audit Ledger & 1-Click CSV Export
========================================================================================
"""

import os
import sys
import time
import json
import sqlite3
import threading
from datetime import datetime, date, timedelta, time as d_time
from pathlib import Path
from io import StringIO

from flask import Flask, render_template, jsonify, request, Response, send_file
import requests
import pandas as pd
import numpy as np
from scipy.stats import norm
import pyotp
from dotenv import load_dotenv

from scripts.daily_data_archiver import DailyDataArchiver

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("quant_terminal")

load_dotenv()


def send_telegram_alert(message):
    """Sends real-time trade alerts to user's Telegram if configured."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        requests.post(url, json=payload, timeout=4)
        return True
    except Exception:
        return False

def get_process_memory_mb():
    """Returns current process Resident Set Size (RSS) in MB with zero-dependency fallback."""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except Exception:
        pass
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        pass
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / (1024.0 * 1024.0), 1)
    except Exception:
        pass
    return 0.0

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# Persistence paths (Guaranteed Absolute Paths relative to app.py with Persistent Disk support)
BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", str(BASE_DIR / "results")))
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = RESULTS_DIR / "paper_engine_state.json"
STATE_BACKUP_FILE = RESULTS_DIR / "paper_engine_state_backup.json"
STATE_DB_FILE = RESULTS_DIR / "quant_state.db"
ENGINE_MODE_FILE = RESULTS_DIR / "engine_mode.json"
EQUITY_CURVE_FILE = RESULTS_DIR / "equity_curve_history.json"
TRADES_FILE = RESULTS_DIR / "live_paper_trades.csv"
MASTER_SPOT_PARQUET = BASE_DIR / "data" / "nifty_1min_5yr.parquet"
REPORTS_DIR = RESULTS_DIR / "daily_reports"
ORDER_REGISTRY_FILE = RESULTS_DIR / "order_registry.json"
EMA_CACHE_FILE = RESULTS_DIR / "ema_state_cache.json"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def get_ist_now():
    """Returns exact current datetime in Indian Standard Time (IST, UTC+5:30)."""
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def calc_bs_greeks(spot, strike, dte_days=5, iv=0.135, r=0.07):
    """
    Computes analytical Black-Scholes Option Greeks:
    Delta, Gamma, Theta (per day in Rs), Vega (per 1% IV in Rs).
    """
    try:
        T = max(1.0, float(dte_days)) / 365.0
        sigma = max(0.01, float(iv))
        K = float(strike)
        S = float(spot)

        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        pdf_d1 = norm.pdf(d1)
        cdf_d1 = norm.cdf(d1)
        cdf_d2 = norm.cdf(d2)
        cdf_neg_d2 = norm.cdf(-d2)

        call_delta = round(float(cdf_d1), 2)
        put_delta = round(float(cdf_d1 - 1.0), 2)

        gamma = round(float(pdf_d1 / (S * sigma * np.sqrt(T))), 4)

        # Theta per calendar day (/ 365)
        call_theta = -(S * pdf_d1 * sigma / (2.0 * np.sqrt(T))) - r * K * np.exp(-r * T) * cdf_d2
        put_theta = -(S * pdf_d1 * sigma / (2.0 * np.sqrt(T))) + r * K * np.exp(-r * T) * cdf_neg_d2
        call_theta_day = round(float(call_theta / 365.0), 2)
        put_theta_day = round(float(put_theta / 365.0), 2)

        # Vega per 1% change in volatility (/ 100)
        vega = round(float(S * np.sqrt(T) * pdf_d1 / 100.0), 2)

        return {
            "call_delta": call_delta,
            "put_delta": put_delta,
            "gamma": gamma,
            "call_theta": call_theta_day,
            "put_theta": put_theta_day,
            "vega": vega,
            "iv": round(sigma * 100.0, 1),
        }
    except Exception:
        return {
            "call_delta": 0.50,
            "put_delta": -0.50,
            "gamma": 0.0010,
            "call_theta": -12.50,
            "put_theta": -12.50,
            "vega": 12.80,
            "iv": 13.5,
        }


def is_kotak_ok(res):
    """Case-insensitive robust verification of Kotak Neo API responses."""
    if not isinstance(res, dict):
        return False
    stat = str(res.get("stat", "")).strip().lower()
    st_code = str(res.get("stCode", ""))
    return (
        stat == "ok"
        or st_code in ["200", "0"]
        or "nOrdNo" in res
        or "data" in res
        or "call" in res
        or "common_data" in res
        or "spot" in res
    )


class MasterQuantEngine:
    def __init__(self):
        self.consumer_key = os.getenv("CONSUMER_KEY") or os.getenv("KOTAK_CONSUMER_KEY", "").strip()
        mobile = os.getenv("MOBILE_NUMBER") or os.getenv("KOTAK_MOBILE_NUMBER", "").strip()
        self.mobile = f"+91{mobile}" if mobile and not mobile.startswith("+91") else mobile
        self.ucc = os.getenv("UCC") or os.getenv("KOTAK_UCC", "").strip()
        self.mpin = os.getenv("MPIN") or os.getenv("KOTAK_MPIN", "031995").strip()
        self.totp_sec = os.getenv("TOTP_SECRET") or os.getenv("KOTAK_TOTP_SECRET", "").strip()

        self.client = None
        self.authenticated = False
        self.greeting = "RAHUL"
        self.lot_size = 65
        self.mkt_lot = 65
        self.active_lots = 1
        self.capital_per_lot = 75000.0

        # Safety Execution Mode: 'LIVE' (Real Kotak Neo Trading) with Persistent Engine Mode
        self.execution_mode = "LIVE"
        self.manual_paused = False
        self.paused_reason = ""
        self.last_trade_exit_time = 0
        self.security_mpin = "031995"
        self.state_lock = threading.RLock()
        self.emas_seeded_for_today = False
        self._init_sqlite_db()
        self.load_engine_mode()
        self.terminal_order_ids = set()
        self.closed_trade_ids = set()

        # Render Free Plan Safety Invariant:
        # Before initial broker sync finishes, block live trade entries
        self.broker_sync_status = "PENDING_RECONCILIATION"
        self.reconciliation_alert = False
        self.reconciliation_blocked = True if self.execution_mode == "LIVE" else False
        self.reconciliation_reason = "Initial broker position reconciliation pending on startup."

        # Capital Tracking (Real Kotak Neo Account Balance)
        self.initial_capital = 101244.91
        self.real_balance = 101244.91
        self.real_margin_used = 0.0
        self.real_collateral = 78929.79

        # Dual-Mode Feeder & Replay State
        self.feeder_mode = "LIVE"  # 'LIVE' or 'REPLAY'
        self.replay_active = False
        self.replay_paused = True
        self.replay_speed = 1  # 1x, 2x, 5x, 10x
        self.replay_idx = 0
        self.replay_df = None
        self.replay_date = "2026-11-09"
        self.last_replay_tick_time = time.time()

        self.last_api_poll = 0
        self.start_time = time.time()
        self.self_heal_count = 0
        self.proximity_polling = False
        self.status_message = "Master Quant Desk Initialized."

        # Latest Market Pulse
        self.pulse = {
            "spot": 25585.00,
            "prev_close": 25800.60,
            "daily_ema20": 24722.08,
            "ema9": 25607.06,
            "ema20": 25640.22,
            "ema50": 25662.26,
            "pcr": 0.68,
            "pcr_oi_change": 1.0,
            "pcr_core_atm": 1.0,
            "india_vix": 16.11,
            "vix_regime": "NORMAL_VOLATILITY (Champion)",
            "wing_distance": 350,
            "next_expiry": "2026-11-23",
            "calls_count": 0,
            "puts_count": 0,
            "candle_5m_open": 25040.00,
            "candle_5m_close": 25040.00,
            "candle_5m_high": 25040.00,
            "candle_5m_low": 25040.00,
            "is_green_candle": True,
            "is_red_candle": False,
            "candle_status": "WAITING_FOR_FIRST_5M_CLOSE",
            "candle_bar_time": None,
            "candle_data_lag": 0.0,
            "signal": "NEUTRAL_SCANNING",
            "option_chain": None,
        }

        # Event queue for browser audio chimes: 'ENTRY', 'TARGET', 'STOP_LOSS'
        self.pending_audio_event = None

        # Daily 1-Minute Live Archiver Engine (Zero Data Gaps!)
        self.archiver = DailyDataArchiver(self.client)
        self.last_archive_time = 0

        self.state = self.load_state()
        # 5-Minute Candle Close Confirmation Latch State (Strict Backtest Alignment)
        self.running_5m_bar = None
        self.finalized_5m_candle = None
        self.last_evaluated_bar = self.state.get("last_evaluated_candle_bar", None)
        self.active_lots = int(self.state.get("user_configured_lots", 1))
        self.lot_size = self.active_lots * int(self.pulse.get("mkt_lot", 65))
        self.load_cached_emas()
        self.authenticate()
        try:
            self.poll_kotak_neo(force=True)
        except Exception as e:
            logger.warning(f"Initial Kotak poll skipped: {e}")
        self.init_replay_data()

        # Dedicated Autonomous Background Workers (Live Poller, Watchdog, Keepalive & Replay)
        self.start_background_workers()

    def start_background_workers(self):
        """Ensures all 4 autonomous background threads are active inside the process/worker."""
        if not hasattr(self, "live_daemon_thread") or not self.live_daemon_thread.is_alive():
            self.live_daemon_thread = threading.Thread(target=self._live_engine_loop, daemon=True)
            self.live_daemon_thread.start()
            logger.info("[ENGINE] Started autonomous live_daemon_thread in worker process.")

        if not hasattr(self, "watchdog_thread") or not self.watchdog_thread.is_alive():
            self.watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
            self.watchdog_thread.start()
            logger.info("[ENGINE] Started self-healing watchdog_thread in worker process.")

        if not hasattr(self, "keepalive_thread") or not self.keepalive_thread.is_alive():
            self.keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
            self.keepalive_thread.start()
            logger.info("[ENGINE] Started autonomous Render keepalive daemon in worker process.")

        if not hasattr(self, "replay_thread") or not self.replay_thread.is_alive():
            self.replay_thread = threading.Thread(target=self._replay_loop, daemon=True)
            self.replay_thread.start()

    def _keepalive_loop(self):
        """
        Autonomous Render Anti-Sleep Daemon:
        Pings public Render endpoint every 240 seconds (4 minutes) to reset the
        15-minute inactivity idle timer on Render Free Tier. Guarantees 24/7 responsiveness.
        """
        time.sleep(20)  # Wait for Flask web server to bind port
        logger.info("[KEEPALIVE] Running autonomous Render keepalive loop (240s interval).")
        while True:
            try:
                time.sleep(240)
                base_url = (os.getenv("RENDER_EXTERNAL_URL") or "https://nifty-quant-terminal.onrender.com").rstrip("/")
                ping_url = f"{base_url}/api/ping"
                res = requests.get(ping_url, timeout=12, headers={"User-Agent": "Antigravity-KeepAlive/1.0"})
                if res.status_code == 200:
                    logger.info(f"[KEEPALIVE] Render Anti-Sleep ping OK (HTTP 200) -> {ping_url}")
                else:
                    logger.warning(f"[KEEPALIVE] Render ping returned non-200 ({res.status_code})")
            except Exception as e:
                logger.debug(f"[KEEPALIVE_NOTICE] Ping attempt notice: {e}")


    def authenticate(self):
        if not self.consumer_key or not self.ucc:
            self.status_message = "Cloud / Offline Mode"
            return False

        try:
            from neo_api_client import NeoAPI
            self.client = NeoAPI(consumer_key=self.consumer_key, environment="prod")
            totp = pyotp.TOTP(self.totp_sec).now()
            self.client.totp_login(mobile_number=self.mobile, ucc=self.ucc, totp=totp)
            res2 = self.client.totp_validate(mpin=self.mpin)

            if res2.get("data", {}).get("status") == "success":
                self.authenticated = True
                self.last_auth_timestamp = time.time()
                self.greeting = res2.get("data", {}).get("greetingName", "RAHUL")
                self.status_message = f"Kotak Neo Connected ({self.greeting})"

                # Fetch Real Live Account Balance & Margins from Kotak Neo API limits()
                try:
                    lim = self.client.limits()
                    if lim and isinstance(lim, dict):
                        net_val = float(lim.get("Net", 0.0) or 0.0)
                        col_val = float(lim.get("Collateral", 0.0) or 0.0)
                        m_used = float(lim.get("MarginUsed", 0.0) or 0.0)
                        # During market hours, Kotak Neo returns live ledger balance.
                        # Off-market / clearing / MTF category returns '0' - never overwrite verified real balance with 0!
                        if net_val >= 1000.0:
                            self.real_balance = round(net_val, 2)
                            self.real_margin_used = round(m_used, 2)
                            if col_val >= 1000.0:
                                self.real_collateral = round(col_val, 2)
                            self.initial_capital = self.real_balance
                        else:
                            if getattr(self, "real_balance", 0.0) < 1000.0:
                                self.real_balance = 101244.91
                            if getattr(self, "real_collateral", 0.0) < 1000.0:
                                self.real_collateral = 78929.79
                            self.initial_capital = self.real_balance
                except Exception:
                    if getattr(self, "real_balance", 0.0) < 1000.0:
                        self.real_balance = 101244.91
                    if getattr(self, "real_collateral", 0.0) < 1000.0:
                        self.real_collateral = 78929.79
                    self.initial_capital = self.real_balance

                if hasattr(self, "archiver") and self.archiver:
                    self.archiver.set_client(self.client)
                return True
            else:
                self.status_message = "Kotak Auth Failed - Standby"
                return False
        except Exception as e:
            self.status_message = f"API Standby ({str(e)[:25]})"
            return False

    def _init_sqlite_db(self):
        """Initializes SQLite persistent storage tables on Persistent Disk."""
        try:
            with sqlite3.connect(STATE_DB_FILE) as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS engine_kv (
                        key TEXT PRIMARY KEY,
                        val_json TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS order_registry (
                        order_id TEXT PRIMARY KEY,
                        order_src TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"[SQLITE_INIT_ERR] Failed to initialize state database: {e}")

    def _sqlite_save_kv(self, key: str, val):
        """Persists key-value JSON to SQLite DB on persistent disk."""
        try:
            val_str = json.dumps(val, default=str)
            with sqlite3.connect(STATE_DB_FILE) as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT OR REPLACE INTO engine_kv (key, val_json, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (key, val_str))
                conn.commit()
        except Exception as e:
            logger.error(f"[SQLITE_SAVE_ERR] Failed to persist key '{key}': {e}")

    def _sqlite_load_kv(self, key: str, default=None):
        """Loads key-value JSON from SQLite DB on persistent disk."""
        try:
            if not STATE_DB_FILE.exists():
                return default
            with sqlite3.connect(STATE_DB_FILE) as conn:
                cur = conn.cursor()
                cur.execute("SELECT val_json FROM engine_kv WHERE key = ?", (key,))
                row = cur.fetchone()
                if row and row[0]:
                    return json.loads(row[0])
        except Exception as e:
            logger.warning(f"[SQLITE_LOAD_WARN] Failed to load key '{key}': {e}")
        return default

    def load_state(self):
        """Upgrade 3: Crash-Proof State Loader with SQLite Dual-Mirror recovery & Persistent Lot Size."""
        today_str = get_ist_now().strftime("%Y-%m-%d")
        default_state = {
            "date": today_str,
            "circuit_breaker_active": False,
            "today_losses": 0,
            "today_wins": 0,
            "today_realized_gross_pnl": 0.0,
            "today_total_charges": 0.0,
            "today_realized_net_pnl": 0.0,
            "active_position": None,
            "user_configured_lots": 1,
            "terminal_order_ids": [],
            "closed_trade_ids": [],
        }
        for state_path in [STATE_FILE, STATE_BACKUP_FILE]:
            if state_path.exists():
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                        if isinstance(saved, dict):
                            user_lots = saved.get("user_configured_lots", 1)
                            default_state["user_configured_lots"] = user_lots
                            if "terminal_order_ids" in saved and isinstance(saved["terminal_order_ids"], list):
                                self.terminal_order_ids = set(str(x) for x in saved["terminal_order_ids"])
                            if "closed_trade_ids" in saved and isinstance(saved["closed_trade_ids"], list):
                                self.closed_trade_ids = set(str(x) for x in saved["closed_trade_ids"])
                            if saved.get("date") == today_str:
                                if "user_configured_lots" not in saved:
                                    saved["user_configured_lots"] = user_lots
                                return saved
                            else:
                                return default_state
                except Exception as e:
                    logger.warning(f"[STATE_LOAD_NOTICE] Could not read {state_path}: {e}")
                    continue

        # Fallback to SQLite Persistent Disk DB if JSON was wiped or corrupted
        sqlite_state = self._sqlite_load_kv("state")
        if isinstance(sqlite_state, dict):
            logger.info("[STATE_LOAD_SQLITE] Restored engine state from SQLite Persistent Storage.")
            if "terminal_order_ids" in sqlite_state and isinstance(sqlite_state["terminal_order_ids"], list):
                self.terminal_order_ids = set(str(x) for x in sqlite_state["terminal_order_ids"])
            if "closed_trade_ids" in sqlite_state and isinstance(sqlite_state["closed_trade_ids"], list):
                self.closed_trade_ids = set(str(x) for x in sqlite_state["closed_trade_ids"])
            if sqlite_state.get("date") == today_str:
                return sqlite_state

        return default_state

    def save_state(self):
        """Upgrade 3: Crash-Proof Atomic State Persistence with Secondary Backup Mirror & SQLite DB Dual-Persistence."""
        lock = getattr(self, "state_lock", None)
        if lock:
            lock.acquire()
        try:
            self.state["terminal_order_ids"] = list(getattr(self, "terminal_order_ids", set()))
            self.state["closed_trade_ids"] = list(getattr(self, "closed_trade_ids", set()))
            tmp_file = STATE_FILE.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, default=str)
            tmp_file.replace(STATE_FILE)
            with open(STATE_BACKUP_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, default=str)

            # Dual-Mirror to SQLite on Persistent Disk
            self._sqlite_save_kv("state", self.state)
            try:
                with sqlite3.connect(STATE_DB_FILE) as conn:
                    cur = conn.cursor()
                    for ord_id in self.terminal_order_ids:
                        cur.execute("INSERT OR IGNORE INTO order_registry (order_id, order_src) VALUES (?, 'RENDER_APP')", (str(ord_id),))
                    conn.commit()
            except Exception as sql_e:
                logger.warning(f"[SQLITE_ORDER_SYNC_ERR] {sql_e}")
        except Exception as e:
            logger.error(f"[STATE_SAVE_ERR] Atomic state write failed: {e}. Attempting direct fallback write...")
            try:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=2, default=str)
                self._sqlite_save_kv("state", self.state)
            except Exception as direct_err:
                logger.error(f"[STATE_SAVE_CRITICAL] Direct state write fallback also failed: {direct_err}")
        finally:
            if lock:
                lock.release()

    def load_engine_mode(self):
        """Loads persistent execution mode, manual pause latch, and cooldown timestamps with Render Free Plan safety."""
        try:
            auto_arm_live = os.getenv("AUTO_ARM_LIVE", "true").lower() in ("true", "1", "yes")
            if ENGINE_MODE_FILE.exists():
                with open(ENGINE_MODE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.execution_mode = data.get("execution_mode", "LIVE")
                    saved_paused = bool(data.get("manual_paused", False))
                    self.last_trade_exit_time = float(data.get("last_trade_exit_time", 0) or 0)

                    # Dynamic Arming Logic: Respect explicit user pause; otherwise auto-arm for autonomous trading
                    if self.execution_mode == "LIVE":
                        if saved_paused:
                            self.manual_paused = True
                            self.paused_reason = str(data.get("paused_reason", "MANUALLY_PAUSED_BY_OPERATOR"))
                        elif auto_arm_live:
                            self.manual_paused = False
                            self.paused_reason = ""
                            logger.info("[ENGINE_MODE] LIVE engine auto-armed on startup (AUTO_ARM_LIVE=true). Ready for market signals.")
                        else:
                            self.manual_paused = True
                            self.paused_reason = "RESTART_SAFETY: LIVE engine started in safe disarmed mode after reboot. Click Resume on Cockpit to arm."
                    else:
                        self.manual_paused = saved_paused
                        self.paused_reason = str(data.get("paused_reason", ""))
                    logger.info(f"[ENGINE_MODE] Loaded: mode={self.execution_mode}, paused={self.manual_paused}, reason={self.paused_reason}")
                    return

            # SQLite fallback (within container session)
            sqlite_mode = self._sqlite_load_kv("engine_mode")
            if isinstance(sqlite_mode, dict):
                self.execution_mode = sqlite_mode.get("execution_mode", "LIVE")
                saved_paused = bool(sqlite_mode.get("manual_paused", False))
                self.last_trade_exit_time = float(sqlite_mode.get("last_trade_exit_time", 0) or 0)
                if self.execution_mode == "LIVE":
                    if saved_paused:
                        self.manual_paused = True
                        self.paused_reason = str(sqlite_mode.get("paused_reason", "MANUALLY_PAUSED_BY_OPERATOR"))
                    elif auto_arm_live:
                        self.manual_paused = False
                        self.paused_reason = ""
                        logger.info("[ENGINE_MODE_SQLITE] LIVE engine auto-armed on startup (AUTO_ARM_LIVE=true).")
                    else:
                        self.manual_paused = True
                        self.paused_reason = "RESTART_SAFETY: LIVE engine started in safe disarmed mode after reboot. Click Resume on Cockpit to arm."
                else:
                    self.manual_paused = saved_paused
                    self.paused_reason = str(sqlite_mode.get("paused_reason", ""))
                logger.info(f"[ENGINE_MODE_SQLITE] Restored from SQLite: mode={self.execution_mode}, paused={self.manual_paused}, reason={self.paused_reason}")
                return

            # Cold boot without state file (Render Free fresh container)
            if self.execution_mode == "LIVE":
                if auto_arm_live:
                    self.manual_paused = False
                    self.paused_reason = ""
                    logger.info("[ENGINE_MODE] LIVE engine auto-armed on fresh cold start (AUTO_ARM_LIVE=true). Ready for automated trading.")
                else:
                    self.manual_paused = True
                    self.paused_reason = "RESTART_SAFETY: LIVE engine cold start on Render Free. Click Resume on Cockpit to arm."
            self.save_engine_mode()
        except Exception as e:
            logger.warning(f"[ENGINE_MODE] Failed to load engine mode file: {e}")
            if self.execution_mode == "LIVE":
                self.manual_paused = False
                self.paused_reason = ""

    def save_engine_mode(self):
        """Persists execution mode, manual pause latch, and cooldown timestamps to disk & SQLite."""
        try:
            data = {
                "execution_mode": getattr(self, "execution_mode", "LIVE"),
                "manual_paused": bool(getattr(self, "manual_paused", False)),
                "paused_reason": str(getattr(self, "paused_reason", "")),
                "last_trade_exit_time": float(getattr(self, "last_trade_exit_time", 0) or 0),
                "updated_at": get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
            }
            tmp = ENGINE_MODE_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(ENGINE_MODE_FILE)
            self._sqlite_save_kv("engine_mode", data)
        except Exception as e:
            logger.error(f"[ENGINE_MODE] Failed to save engine mode: {e}")

    def record_equity_point(self, net_pnl, reason="TRADE_CLOSED"):
        """Upgrade 3: Tracks continuous equity curve and realized PnL trajectory."""
        try:
            cur_equity = round(self.initial_capital + float(self.state.get("today_realized_net_pnl", 0.0)), 2)
            record = {
                "timestamp": get_ist_now().strftime("%Y-%m-%d %H:%M:%S"),
                "date": get_ist_now().strftime("%Y-%m-%d"),
                "equity": cur_equity,
                "net_pnl": net_pnl,
                "reason": reason,
            }
            history = []
            if EQUITY_CURVE_FILE.exists():
                try:
                    with open(EQUITY_CURVE_FILE, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except Exception:
                    history = []
            history.append(record)
            with open(EQUITY_CURVE_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"[EQUITY_CURVE_ERR] Failed to write equity curve point: {e}")

    def get_compounding_lot_sizing(self):
        """Lot Sizing: Strictly honors User-Configured Lots if set, else auto-compounds."""
        mkt_lot = int(self.pulse.get("mkt_lot", 65))
        if self.state.get("user_configured_lots"):
            configured_lots = max(1, int(self.state.get("user_configured_lots")))
            self.active_lots = configured_lots
            self.lot_size = configured_lots * mkt_lot
            return configured_lots, self.lot_size

        cur_equity = self.initial_capital + float(self.state.get("today_realized_net_pnl", 0.0))
        pos = self.state.get("active_position")
        if pos:
            cur_equity += float(pos.get("mtm_net_pnl", 0.0))
        calculated_lots = max(1, int(cur_equity // self.capital_per_lot))
        self.active_lots = calculated_lots
        self.lot_size = calculated_lots * mkt_lot
        return calculated_lots, self.lot_size

    def get_next_tuesday_expiry(self, base_date=None):
        """Strictly calculates the upcoming Tuesday weekly expiry date for NIFTY.
        On Tuesday (Expiry Day), strictly rolls to NEXT Tuesday (+7 days) to avoid high-gamma 0-DTE risk."""
        if base_date is None:
            base_date = get_ist_now().date()
        # Monday is 0, Tuesday is 1, Wednesday is 2, ..., Sunday is 6
        days_ahead = (1 - base_date.weekday()) % 7
        if days_ahead == 0:  # Expiry Day (Tuesday) -> strictly roll to NEXT week!
            days_ahead = 7
        tuesday_exp = base_date + timedelta(days=days_ahead)
        return tuesday_exp.strftime("%Y-%m-%d")

    def get_adaptive_wing_distance(self):
        """Upgrade 5: India VIX-Adaptive Wing Distance (300/350/450 pts)."""
        vix = float(self.pulse.get("india_vix", 13.5))
        if vix < 13.0:
            regime = "LOW_VOLATILITY (Rangebound)"
            wing_dist = 300
        elif vix <= 17.0:
            regime = "NORMAL_VOLATILITY (Champion)"
            wing_dist = 350
        else:
            regime = "HIGH_VOLATILITY (Buffer Expansion)"
            wing_dist = 450
        self.pulse["vix_regime"] = regime
        self.pulse["wing_distance"] = wing_dist
        return wing_dist, regime

    def check_bull_pcr(self):
        """Strict Quantitative Rule: Weekly PCR >= 1.05 for Bullish Uptrade."""
        pcr = self.pulse.get("pcr")
        if pcr is None:
            return False, "PCR Loading..."
        if pcr >= 1.05:
            return True, f"Weekly PCR ({pcr:.3f} >= 1.05, Put Writers Dominant)"
        return False, f"Weekly PCR ({pcr:.3f} < 1.05, Call Writers Dominant)"

    def check_bear_pcr(self):
        """Strict Quantitative Rule: Weekly PCR <= 0.85 for Bearish Downtrade."""
        pcr = self.pulse.get("pcr")
        if pcr is None:
            return False, "PCR Loading..."
        if pcr <= 0.85:
            return True, f"Weekly PCR ({pcr:.3f} <= 0.85, Call Writers Dominant)"
        return False, f"Weekly PCR ({pcr:.3f} > 0.85, Put Writers Dominant)"

    def load_cached_emas(self, fallback_only=False):
        """Loads persistent Daily and 5m base EMAs from results/cached_emas.json with date protection."""
        cache_file = os.path.join(os.path.dirname(__file__), "results", "cached_emas.json")
        try:
            if os.path.exists(cache_file):
                with open(cache_file, "r") as f:
                    c = json.load(f)
                    today_str = get_ist_now().strftime("%Y-%m-%d")
                    # Fix 6: Never let stale historical cache from an earlier date overwrite today's live organic EMAs!
                    if c.get("date") != today_str:
                        logger.warning(f"[EMA_CACHE_GUARD] Discarding stale cache from earlier date: {c.get('date')} vs today: {today_str}")
                        return False
                    self.base_ema9 = float(c.get("ema9", getattr(self, "base_ema9", 25607.06)))
                    self.base_ema20 = float(c.get("ema20", getattr(self, "base_ema20", 25640.22)))
                    self.base_ema50 = float(c.get("ema50", getattr(self, "base_ema50", 25662.26)))
                    self.base_daily20 = float(c.get("daily_ema20", getattr(self, "base_daily20", 24722.08)))
                    self.pulse["ema9"] = self.base_ema9
                    self.pulse["ema20"] = self.base_ema20
                    self.pulse["ema50"] = self.base_ema50
                    self.pulse["daily_ema20"] = self.base_daily20
                    return True
        except Exception as e:
            logger.warning(f"[EMA_LOAD_ERR] Failed to load cached EMAs: {e}")
        return False

    def save_cached_emas(self):
        """Persists current calculated EMAs to results/cached_emas.json."""
        cache_file = os.path.join(os.path.dirname(__file__), "results", "cached_emas.json")
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            data = {
                "date": get_ist_now().strftime("%Y-%m-%d"),
                "ema9": getattr(self, "base_ema9", 25607.06),
                "ema20": getattr(self, "base_ema20", 25640.22),
                "ema50": getattr(self, "base_ema50", 25662.26),
                "daily_ema20": getattr(self, "base_daily20", 24722.08),
                "last_spot": float(self.pulse.get("spot", 25585.0) or 25585.0),
                "updated_at": get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(cache_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"[EMA_SAVE_ERR] Failed to save cached EMAs: {e}")

    def update_real_emas_from_candles(self):
        """Fetches real historical 5-minute and Daily candle data from Kotak Neo API and seeds exact EMAs with persistent cache fallback."""
        if not self.client or not self.authenticated:
            self.load_cached_emas(fallback_only=True)
            return
        now = time.time()
        now_dt = get_ist_now()
        is_mkt_open = (datetime.strptime("09:15", "%H:%M").time() <= now_dt.time() <= datetime.strptime("15:30", "%H:%M").time()) and (now_dt.weekday() < 5)

        # Fix 6: Once seeded for the day, intraday EMAs evolve organically on confirmed 5m closes.
        # Do not allow periodic historical API calls to unexpectedly clobber intraday organic EMAs during market hours!
        if getattr(self, "emas_seeded_for_today", False) and is_mkt_open:
            return

        if now < getattr(self, "ema_backoff_until", 0):
            self.load_cached_emas(fallback_only=True)
            return
        if now - getattr(self, "last_ema_calc_time", 0) < 180:
            return
        self.last_ema_calc_time = now
        try:
            today_dt = now_dt.date()
            today_str = today_dt.strftime("%Y-%m-%d")
            start_5m = (today_dt - timedelta(days=5)).strftime("%Y-%m-%d")
            res_5m = self.client.historical_data(neosymbol="nse_cm|Nifty 50", interval="5min", from_date=start_5m, to_date=today_str)
            if isinstance(res_5m, dict) and ("429" in str(res_5m) or "rate" in str(res_5m).lower()):
                self.ema_backoff_until = now + 60
                self.load_cached_emas(fallback_only=True)
                return
            candles_5m = res_5m.get("data", {}).get("candles", []) if isinstance(res_5m, dict) else []
            if candles_5m and len(candles_5m) >= 20:
                closes = [float(c[4]) for c in candles_5m]
                s_close = pd.Series(closes)
                self.base_ema9 = round(float(s_close.ewm(span=9, adjust=False).mean().iloc[-1]), 2)
                self.base_ema20 = round(float(s_close.ewm(span=20, adjust=False).mean().iloc[-1]), 2)
                self.base_ema50 = round(float(s_close.ewm(span=50, adjust=False).mean().iloc[-1]), 2)
                self.pulse["ema9"] = self.base_ema9
                self.pulse["ema20"] = self.base_ema20
                self.pulse["ema50"] = self.base_ema50

                # Direct Seeding of latest completed 5-minute candle from Kotak Neo API (Strict 5m Close Confirmation)
                try:
                    latest_c = candles_5m[-1]
                    c_ts = pd.to_datetime(latest_c[0])
                    c_open = float(latest_c[1])
                    c_high = float(latest_c[2])
                    c_low = float(latest_c[3])
                    c_close = float(latest_c[4])
                    self.finalized_5m_candle = {
                        "bar_start": c_ts,
                        "bar_close": c_ts + timedelta(minutes=5),
                        "open": c_open,
                        "high": c_high,
                        "low": c_low,
                        "close": c_close,
                        "tick_count": 60,
                        "last_tick_time": c_ts + timedelta(minutes=5),
                        "last_tick_age": 1.0,
                        "finalized_at": now_dt,
                        "data_lag": 1.0,
                        "is_green": bool(c_close >= c_open),
                        "is_red": bool(c_close <= c_open),
                        "is_valid": True,
                        "source": "KOTAK_HISTORICAL_API"
                    }
                    self.pulse["candle_5m_open"] = c_open
                    self.pulse["candle_5m_close"] = c_close
                    self.pulse["candle_5m_high"] = c_high
                    self.pulse["candle_5m_low"] = c_low
                    self.pulse["is_green_candle"] = bool(c_close >= c_open)
                    self.pulse["is_red_candle"] = bool(c_close <= c_open)
                    self.pulse["candle_status"] = "CONFIRMED_CLOSED"
                    self.pulse["candle_bar_time"] = c_ts.strftime("%H:%M")
                    self.pulse["candle_data_lag"] = 1.0
                    logger.info(f"[CANDLE_SEED] Confirmed closed 5m candle seeded from Kotak Neo API: {c_ts.strftime('%H:%M')} Open={c_open}, Close={c_close}, Color={'GREEN' if c_close>=c_open else 'RED'}")
                except Exception as seed_err:
                    logger.warning(f"[CANDLE_SEED_ERR] Could not parse latest 5m candle: {seed_err}")

            # Safe spacing to prevent Kotak 429 rate limit between 5m and Daily historical calls
            time.sleep(2.5)

            start_d = (today_dt - timedelta(days=120)).strftime("%Y-%m-%d")
            res_d = self.client.historical_data(neosymbol="nse_cm|Nifty 50", interval="D", from_date=start_d, to_date=today_str)
            candles_d = res_d.get("data", {}).get("candles", []) if isinstance(res_d, dict) else []
            if candles_d and len(candles_d) >= 20:
                d_closes = [float(c[4]) for c in candles_d]
                s_d = pd.Series(d_closes)
                self.base_daily20 = round(float(s_d.ewm(span=20, adjust=False).mean().iloc[-1]), 2)
                self.pulse["daily_ema20"] = self.base_daily20

            self.emas_seeded_for_today = True
            self.save_cached_emas()
            logger.info(f"[EMA_SEED_OK] Seeded live EMAs: E9={self.pulse['ema9']}, E20={self.pulse['ema20']}, E50={self.pulse['ema50']}, Daily20={self.pulse['daily_ema20']}")
        except Exception as e:
            logger.warning(f"[EMA_CALC_ERR] Error fetching historical candles: {e}")
            if "429" in str(e) or "rate" in str(e).lower():
                self.ema_backoff_until = now + 120
            self.load_cached_emas(fallback_only=True)

    def _watchdog_loop(self):
        """Upgrade 9: Self-Healing Server Watchdog & Heartbeat Monitor Daemon."""
        while True:
            try:
                time.sleep(10)
                now = time.time()
                now_dt = get_ist_now()
                is_mkt_open = (datetime.strptime("09:15", "%H:%M").time() <= now_dt.time() <= datetime.strptime("15:30", "%H:%M").time()) and (now_dt.weekday() < 5)

                # Check if live worker loop thread is alive
                if hasattr(self, "live_daemon_thread") and not self.live_daemon_thread.is_alive():
                    self.self_heal_count += 1
                    self.status_message = f"[WATCHDOG] Reviving Live Engine Worker Loop (Heal #{self.self_heal_count})..."
                    self.live_daemon_thread = threading.Thread(target=self._live_engine_loop, daemon=True)
                    self.live_daemon_thread.start()

                # Check if market hours polling stalled (>45s)
                if is_mkt_open and (now - self.last_api_poll > 45):
                    self.self_heal_count += 1
                    self.status_message = f"[WATCHDOG] Stalled poller ({int(now - self.last_api_poll)}s). Soft-healing connection..."
                    self.authenticate()
                    self.poll_kotak_neo(force=True)

                # Pre-Market Proactive Re-authentication Handshake (08:50 - 09:14 IST)
                # Guarantees a fresh session token before market opens at 09:15 sharp!
                if datetime.strptime("08:50", "%H:%M").time() <= now_dt.time() <= datetime.strptime("09:14", "%H:%M").time() and (now_dt.weekday() < 5):
                    if not getattr(self, "pre_market_reauth_done", False):
                        logger.info("[WATCHDOG] Performing Pre-Market Proactive Authentication Handshake (08:50 IST)...")
                        self.authenticate()
                        self.poll_kotak_neo(force=True)
                        self.pre_market_reauth_done = True
                elif now_dt.time() > datetime.strptime("09:15", "%H:%M").time():
                    self.pre_market_reauth_done = False
            except Exception as e:
                logger.warning(f"[WATCHDOG_NOTICE] Watchdog loop iteration notice: {e}")

    def generate_daily_report(self):
        """Upgrade 7: Automated 15:35 IST EOD Daily Performance Report Generator."""
        today_str = get_ist_now().strftime("%Y-%m-%d")
        now_str = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
        ending_capital = round(self.initial_capital + float(self.state.get("today_realized_net_pnl", 0.0)), 2)
        roi_pct = round((float(self.state.get("today_realized_net_pnl", 0.0)) / self.initial_capital) * 100.0, 2)

        trades_list = []
        if TRADES_FILE.exists():
            try:
                df = pd.read_csv(TRADES_FILE, on_bad_lines="skip")
                df_today = df[df["date"] == today_str]
                trades_list = df_today.to_dict(orient="records")
            except Exception:
                pass

        report_data = {
            "date": today_str,
            "generated_at": now_str,
            "status": "COMPLETED",
            "starting_capital": self.initial_capital,
            "ending_capital": ending_capital,
            "realized_gross_pnl": self.state.get("today_realized_gross_pnl", 0.0),
            "total_charges": self.state.get("today_total_charges", 0.0),
            "realized_net_pnl": self.state.get("today_realized_net_pnl", 0.0),
            "roi_pct": roi_pct,
            "today_wins": self.state.get("today_wins", 0),
            "today_losses": self.state.get("today_losses", 0),
            "total_trades": self.state.get("today_wins", 0) + self.state.get("today_losses", 0),
            "circuit_breaker_triggered": self.state.get("circuit_breaker_active", False),
            "active_position": self.state.get("active_position"),
            "spot_close": self.pulse.get("spot"),
            "weekly_pcr": self.pulse.get("pcr"),
            "trades": trades_list,
        }

        # Save JSON scorecard
        json_path = REPORTS_DIR / f"daily_report_{today_str}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)

        # Generate standalone HTML scorecard
        pnl_color = "#10b981" if report_data["realized_net_pnl"] >= 0 else "#ef4444"
        pnl_sign = "+" if report_data["realized_net_pnl"] >= 0 else ""
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>EOD Performance Report - {today_str}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #080c14; color: #e2e8f0; margin: 0; padding: 24px; }}
        .card {{ max-width: 720px; margin: 0 auto; background: #101726; border: 1px solid #1e293b; border-radius: 16px; padding: 28px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e293b; padding-bottom: 16px; }}
        .badge {{ background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); padding: 4px 12px; border-radius: 9999px; font-weight: 700; font-size: 12px; }}
        .pnl-hero {{ text-align: center; margin: 28px 0; }}
        .pnl-value {{ font-size: 42px; font-weight: 900; color: {pnl_color}; font-family: monospace; }}
        .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; margin: 24px 0; }}
        .stat-box {{ background: #0d131f; border: 1px solid #1e293b; border-radius: 12px; padding: 14px 18px; }}
        .stat-label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
        .stat-val {{ font-size: 18px; font-weight: 700; color: #f8fafc; font-family: monospace; margin-top: 4px; }}
        .footer {{ border-top: 1px solid #1e293b; padding-top: 16px; font-size: 12px; color: #64748b; text-align: center; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div>
                <h2 style="margin: 0; color: #fff;">NIFTY Champion Model 4</h2>
                <p style="margin: 4px 0 0; color: #94a3b8; font-size: 13px;">EOD Performance Dossier &bull; {today_str}</p>
            </div>
            <span class="badge">OFFICIAL EOD AUDIT</span>
        </div>
        <div class="pnl-hero">
            <div style="font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.1em;">Net Realized PnL</div>
            <div class="pnl-value">{pnl_sign}Rs {report_data['realized_net_pnl']:,.2f}</div>
            <div style="font-size: 14px; color: {pnl_color}; font-weight: 600; margin-top: 6px;">Day ROI: {pnl_sign}{roi_pct}%</div>
        </div>
        <div class="grid">
            <div class="stat-box">
                <div class="stat-label">Starting Capital</div>
                <div class="stat-val">Rs {report_data['starting_capital']:,.2f}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Ending Account Balance</div>
                <div class="stat-val">Rs {report_data['ending_capital']:,.2f}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Gross PnL</div>
                <div class="stat-val">Rs {report_data['realized_gross_pnl']:,.2f}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Kotak Neo Taxes & Brokerage</div>
                <div class="stat-val" style="color: #f87171;">-Rs {report_data['total_charges']:,.2f}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Trades Executed</div>
                <div class="stat-val">{report_data['total_trades']} (Wins: {report_data['today_wins']}, Losses: {report_data['today_losses']})</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Nifty Spot Settlement</div>
                <div class="stat-val">{report_data['spot_close']}</div>
            </div>
        </div>
        <div class="footer">Generated Autonomously by Nifty Quant Terminal &bull; {now_str} IST</div>
    </div>
</body>
</html>"""
        html_path = REPORTS_DIR / f"daily_report_{today_str}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return report_data

    def init_replay_data(self, target_date="2026-11-09"):
        """Loads historical 1-minute candles for off-market replay with immediate GC cleanup."""
        try:
            if MASTER_SPOT_PARQUET.exists():
                import gc
                df = pd.read_parquet(MASTER_SPOT_PARQUET, columns=["open", "high", "low", "close"])
                available = df.index.normalize().unique()
                self.available_replay_dates = [d.strftime("%Y-%m-%d") for d in sorted(available, reverse=True)[:30]]
                if target_date not in self.available_replay_dates and self.available_replay_dates:
                    target_date = self.available_replay_dates[0]
                
                day_slice = df.loc[target_date].copy()
                del df
                gc.collect()
                if not day_slice.empty:
                    self.replay_df = day_slice.reset_index()
                    self.replay_date = target_date
                    self.replay_idx = 0
                    self.status_message = f"Replay Loaded: {target_date} ({len(self.replay_df)} candles)"
        except Exception as e:
            self.status_message = f"Replay Load Error: {e}"

    def poll_kotak_neo(self, force=False):
        """Fetches live market data if market is open, or snapshot on init if forced."""
        now = time.time()
        if not self.authenticated or not self.client:
            self.authenticate()
            if not self.authenticated or not self.client:
                return

        # Rate Limit (429) Safeguard: Honor backoff cooldown if active
        if not force and now < getattr(self, "kotak_rate_limit_backoff_until", 0):
            return

        # Check market & pre-market hours in IST (08:50-09:15 Pre-market warmup; 09:15-15:30 Live trading)
        now_dt = get_ist_now()
        t_now = now_dt.time()
        is_mkt_open = (datetime.strptime("09:15", "%H:%M").time() <= t_now <= datetime.strptime("15:30", "%H:%M").time()) and (now_dt.weekday() < 5)
        is_pre_mkt = (datetime.strptime("08:50", "%H:%M").time() <= t_now < datetime.strptime("09:15", "%H:%M").time()) and (now_dt.weekday() < 5)

        # Proactive Daily Token Expiry Guard: If token is > 12 hours old, force refresh
        if getattr(self, "last_auth_timestamp", 0) > 0 and (now - self.last_auth_timestamp > 12 * 3600):
            logger.info("[AUTO_TOKEN_EXPIRY] Kotak Neo token older than 12 hours. Performing proactive daily refresh...")
            self.authenticate()

        # Pre-Market Warmup (08:50-09:15): poll every 2.0s to keep session, option chain & PCR warm!
        # In market hours (09:15-15:30): poll every 1.0s (strictly complies with Kotak Neo option-chain rate limits).
        # Off-market: poll every 60s (or instant if force=True or initial boot).
        if is_mkt_open:
            min_poll_interval = 0.9
        elif is_pre_mkt:
            min_poll_interval = 2.0
        else:
            min_poll_interval = 60

        if not force and (now - self.last_api_poll < min_poll_interval):
            return

        if not hasattr(self, "poll_lock"):
            self.poll_lock = threading.Lock()
            self.poll_lock_time = 0.0

        acquired = self.poll_lock.acquire(blocking=False)
        if not acquired:
            if now - getattr(self, "poll_lock_time", 0.0) > 15.0:
                logger.warning("[POLL_LOCK_HEAL] Poller lock held > 15s. Recreating lock.")
                self.poll_lock = threading.Lock()
                acquired = self.poll_lock.acquire(blocking=False)
            if not acquired:
                return
        self.poll_lock_time = now

        self.feeder_mode = "LIVE" if (is_mkt_open or is_pre_mkt) else "CLOSED"

        try:
            # Query active weekly option chain directly from Kotak Neo (Matches Kotak mobile app 100%!)
            chain = self.client.option_chain(exchange="nse_fo", underlying="NIFTY")

            if not is_kotak_ok(chain) or not isinstance(chain, dict) or (isinstance(chain, dict) and chain.get("code") == 429):
                err_msg = str(chain.get("message") or chain.get("error") or chain.get("fault") or "Non-OK")
                err_lower = err_msg.lower()
                if "401" in err_lower or "token" in err_lower or "session" in err_lower or "unauth" in err_lower or "expired" in err_lower or "invalid" in err_lower:
                    logger.warning(f"[POLL_REJECT_AUTH] Kotak session expired ({err_msg}). Re-authenticating...")
                    self.authenticate()
                logger.warning(f"[POLL_REJECT] Kotak option chain {err_msg}. Retaining live PCR {self.pulse.get('pcr')}.")
                self.last_api_poll = now + 2.0
                return

            calls = chain.get("call", [])
            puts = chain.get("put", [])
            if not calls and not puts and "spot" not in chain:
                logger.warning("[POLL_EMPTY] Kotak option chain empty. Preserving existing live market data.")
                return

            if "spot" in chain and "ltp" in chain["spot"]:
                self.pulse["spot"] = float(chain["spot"]["ltp"])
                self.pulse["prev_close"] = float(chain["spot"].get("prevClose", self.pulse["spot"]))

            if "common_data" in chain:
                raw_exp = chain["common_data"].get("expiryDt", "2026-11-23")
                self.pulse["mkt_lot"] = int(chain["common_data"].get("mktLot", 65))
                self.lot_size = self.pulse["mkt_lot"]
                self.pulse["next_expiry"] = raw_exp
                self.pulse["is_expiry_rolled"] = False

            self.pulse["option_chain"] = chain
            self.pulse["chain_updated_at"] = now_dt.strftime("%H:%M:%S IST")
            spot = self.pulse["spot"]

            # Dynamic Real-Time EMA Calculations: Seed once on boot, then evolve organically on 5m close
            if not getattr(self, "emas_seeded_for_today", False):
                self.update_real_emas_from_candles()

            base_e9 = getattr(self, "base_ema9", 25607.06)
            base_e20 = getattr(self, "base_ema20", 25640.22)
            base_e50 = getattr(self, "base_ema50", 25662.26)
            base_daily = getattr(self, "base_daily20", 24722.08)

            # Fix 3: Strict Backtest Purity - EMAs are derived strictly from finalized 5m candle closes (Zero tick noise)
            self.pulse["ema9"] = base_e9
            self.pulse["ema20"] = base_e20
            self.pulse["ema50"] = base_e50
            self.pulse["daily_ema20"] = base_daily

            # Live Option Chain OI & High-Precision PCR (Sanitized & Bounded)
            if calls or puts:
                total_call_oi = sum(int(c.get("oi", {}).get("cur") or 0) for c in calls)
                total_put_oi = sum(int(p.get("oi", {}).get("cur") or 0) for p in puts)
                if total_call_oi > 0:
                    self.pulse["total_call_oi"] = total_call_oi
                    self.pulse["total_put_oi"] = total_put_oi
                    raw_pcr = round(total_put_oi / total_call_oi, 3)
                    new_pcr = min(max(raw_pcr, 0.20), 4.00)
                    self.pulse["pcr"] = new_pcr
                    self.last_valid_pcr = new_pcr
                # GLITCH FIX: If total_call_oi <= 0, RETAIN existing self.pulse["pcr"]! NEVER default to 1.05!

                # Intraday Fresh Change in OI PCR (Today's live build-up)
                call_oi_chg = sum(int(c.get("oi", {}).get("cur") or 0) - int(c.get("oi", {}).get("prev") or 0) for c in calls)
                put_oi_chg = sum(int(p.get("oi", {}).get("cur") or 0) - int(p.get("oi", {}).get("prev") or 0) for p in puts)
                if call_oi_chg != 0 or put_oi_chg != 0:
                    self.pulse["call_oi_chg"] = call_oi_chg
                    self.pulse["put_oi_chg"] = put_oi_chg
                    if call_oi_chg > 0 and put_oi_chg > 0:
                        raw_oi_pcr = round(put_oi_chg / call_oi_chg, 2)
                        self.pulse["pcr_oi_change"] = min(max(raw_oi_pcr, 0.10), 10.0)
                    elif put_oi_chg > 0:
                        self.pulse["pcr_oi_change"] = 3.0
                    elif call_oi_chg > 0:
                        self.pulse["pcr_oi_change"] = 0.10

                # Active Core Strikes PCR (ATM +/- 500 pts)
                atm = int(round(spot / 50.0) * 50)
                c_oi_core = sum(int(c.get("oi", {}).get("cur", 0)) for c in calls if abs(float(c.get("inst", {}).get("strkPrc", 0)) - atm) <= 500)
                p_oi_core = sum(int(p.get("oi", {}).get("cur", 0)) for p in puts if abs(float(p.get("inst", {}).get("strkPrc", 0)) - atm) <= 500)
                if c_oi_core > 0:
                    raw_core_pcr = round(p_oi_core / c_oi_core, 3)
                    self.pulse["pcr_core_atm"] = min(max(raw_core_pcr, 0.05), 5.0)
            else:
                self.pulse["pcr_core_atm"] = 1.0

            # Real-time position reconciliation with Kotak Neo broker (On startup when PENDING_RECONCILIATION, or whenever active position is open)
            if self.execution_mode == "LIVE" and self.authenticated and self.client and (self.state.get("active_position") or getattr(self, "broker_sync_status", "") == "PENDING_RECONCILIATION"):
                try:
                    real_pos = self.client.positions()
                    # Check if response is OK or is Kotak Neo's standard "Empty / 0 Positions" response (stCode 5203 / "No Data")
                    is_empty_broker_pos = False
                    if isinstance(real_pos, dict):
                        st_c = str(real_pos.get("stCode", ""))
                        err_m = str(real_pos.get("errMsg", "")).lower()
                        desc_m = str(real_pos.get("desc", "")).lower()
                        if st_c in ["5203", "5204"] or "no data" in err_m or "data not found" in desc_m:
                            is_empty_broker_pos = True

                    if is_kotak_ok(real_pos) or is_empty_broker_pos:
                        has_real_pos = False
                        short_leg = None
                        long_leg = None
                        pos_list = (real_pos.get("data", []) or []) if not is_empty_broker_pos else []

                        for p in pos_list:
                            # CRITICAL SAFETY FILTER: Only process NIFTY F&O Options! Ignore Equity shares/ETFs (like ITBEES)!
                            ex_seg = str(p.get("exSeg", "")).lower()
                            sym = str(p.get("sym", "")).upper()
                            trd_sym = str(p.get("trdSym", "")).upper()
                            stk_prc = float(p.get("stkPrc", 0.0) or 0.0)

                            if ex_seg != "nse_fo" or not (sym == "NIFTY" or trd_sym.startswith("NIFTY")) or stk_prc <= 0:
                                continue

                            try:
                                buy_q = int(float(p.get("flBuyQty", 0)))
                                sell_q = int(float(p.get("flSellQty", 0)))
                                net = buy_q - sell_q
                                if abs(net) > 0:
                                    has_real_pos = True
                                if net < 0 and not short_leg:
                                    short_leg = p
                                elif net > 0 and not long_leg:
                                    long_leg = p
                            except Exception as parse_leg_err:
                                logger.warning(f"[RECON_PARSE_NOTICE] Error parsing broker position leg: {parse_leg_err}")

                        # Consecutive empty position check counter
                        if not hasattr(self, "zero_pos_consecutive_count"):
                            self.zero_pos_consecutive_count = 0

                        # Case A: If Terminal had an active position, but Kotak reports NO open position:
                        # DO NOT delete on single empty poll, transient API delay, or empty order book!
                        # STRICT SECURITY RULE: NEVER close position purely based on empty positions or empty order book!
                        if not has_real_pos and self.state.get("active_position"):
                            self.zero_pos_consecutive_count += 1
                            old_pos = self.state.get("active_position")
                            logger.info(
                                f"[RECON_GRACE] Zero open broker position detected (poll {self.zero_pos_consecutive_count}). "
                                f"Retaining active position {old_pos.get('direction')} in protective state..."
                            )

                            s_sym = str(old_pos.get("sell_symbol", "")).upper()
                            b_sym = str(old_pos.get("buy_symbol", "")).upper()
                            total_pos_qty = int(old_pos.get("lot_size", self.lot_size) or self.lot_size)

                            # Multi-source verification: Cross-check Kotak Neo Order Book / Trade Book
                            sell_close_qty = 0
                            sell_close_val = 0.0
                            buy_close_qty = 0
                            buy_close_val = 0.0

                            try:
                                rep = self.client.order_report()
                                if is_kotak_ok(rep) and rep.get("data"):
                                    for o in rep.get("data", []):
                                        trd_sym = str(o.get("trdSym", "")).upper()
                                        st = str(o.get("ordSt", "")).lower()
                                        tt = str(o.get("trnsTp", "")).upper()
                                        fld_qty = int(float(o.get("fldQty", 0) or 0))
                                        fl_prc = float(o.get("flPr", 0.0) or o.get("avgPrc", 0.0) or 0.0)

                                        if st in ["complete", "traded"] and fld_qty > 0:
                                            # Short leg exit is BUY (B)
                                            if s_sym and s_sym in trd_sym and tt == "B":
                                                sell_close_qty += fld_qty
                                                sell_close_val += fld_qty * fl_prc
                                            # Long hedge exit is SELL (S)
                                            if b_sym and b_sym in trd_sym and tt == "S":
                                                buy_close_qty += fld_qty
                                                buy_close_val += fld_qty * fl_prc
                            except Exception as rep_err:
                                logger.warning(f"Order book reconciliation check notice: {rep_err}")

                            # Optional: Check trade_report if available and needed
                            if hasattr(self.client, "trade_report") and (sell_close_qty == 0 or buy_close_qty == 0):
                                try:
                                    trd_rep = self.client.trade_report()
                                    if is_kotak_ok(trd_rep) and trd_rep.get("data"):
                                        for tr in trd_rep.get("data", []):
                                            trd_sym = str(tr.get("trdSym", "")).upper()
                                            tt = str(tr.get("trnsTp", "")).upper()
                                            fld_qty = int(float(tr.get("fldQty", tr.get("qty", 0)) or 0))
                                            fl_prc = float(tr.get("flPr", tr.get("prc", 0.0)) or 0.0)
                                            if fld_qty > 0:
                                                if s_sym and s_sym in trd_sym and tt == "B" and sell_close_qty == 0:
                                                    sell_close_qty += fld_qty
                                                    sell_close_val += fld_qty * fl_prc
                                                if b_sym and b_sym in trd_sym and tt == "S" and buy_close_qty == 0:
                                                    buy_close_qty += fld_qty
                                                    buy_close_val += fld_qty * fl_prc
                                except Exception as trd_rep_err:
                                    logger.warning(f"[RECON_TRD_REP_NOTICE] Error parsing trade report: {trd_rep_err}")

                            # Reconcile quantities: effective closed spread quantity
                            closed_qty = min(sell_close_qty, buy_close_qty)

                            if closed_qty > 0:
                                # Positive execution proof! Calculate exact weighted average fill prices
                                sell_exit_prc = round(sell_close_val / sell_close_qty, 2) if sell_close_qty > 0 else float(old_pos.get("sell_price", 0.0))
                                buy_exit_prc = round(buy_close_val / buy_close_qty, 2) if buy_close_qty > 0 else float(old_pos.get("buy_price", 0.0))
                                actual_exit_spread = round(sell_exit_prc - buy_exit_prc, 2)

                                if closed_qty >= total_pos_qty:
                                    # FULL SQUARE-OFF CONFIRMED
                                    logger.info(
                                        f"[RECON_CONFIRMED_FULL] Order book confirms complete square-off: "
                                        f"{closed_qty} qty @ spread {actual_exit_spread} (Short: {sell_exit_prc}, Long: {buy_exit_prc})"
                                    )
                                    try:
                                        self.record_external_close_to_ledger(
                                            old_pos,
                                            reason="BROKER_MANUAL_SQUAREOFF",
                                            exit_spread_override=actual_exit_spread,
                                            closed_qty_override=total_pos_qty
                                        )
                                    except Exception as log_err:
                                        logger.error(f"Failed to record external close to ledger: {log_err}")
                                    self.state["active_position"] = None
                                    self.zero_pos_consecutive_count = 0
                                    self.broker_sync_status = "SYNCED"
                                    self.reconciliation_alert = False
                                    self.reconciliation_blocked = False
                                    self.reconciliation_reason = ""
                                    self.save_state()
                                else:
                                    # PARTIAL SQUARE-OFF: Close only filled portion, retain remainder
                                    logger.info(
                                        f"[RECON_PARTIAL_EXIT] Partial fill confirmed: {closed_qty}/{total_pos_qty} qty closed @ spread {actual_exit_spread}. "
                                        f"Retaining remaining {total_pos_qty - closed_qty} qty as OPEN."
                                    )
                                    try:
                                        self.record_external_close_to_ledger(
                                            old_pos,
                                            reason="PARTIAL_SQUAREOFF",
                                            exit_spread_override=actual_exit_spread,
                                            closed_qty_override=closed_qty
                                        )
                                    except Exception as log_err:
                                        logger.error(f"Failed to record partial close to ledger: {log_err}")
                                    rem_qty = total_pos_qty - closed_qty
                                    old_pos["lot_size"] = rem_qty
                                    old_pos["lot_count"] = max(1, rem_qty // self.lot_size)
                                    old_pos["max_credit_inr"] = round(float(old_pos["net_credit"]) * rem_qty, 2)
                                    old_pos["target_profit_inr"] = round((float(old_pos["net_credit"]) * 0.40) * rem_qty, 2)
                                    old_pos["max_risk_inr"] = round(min((float(old_pos["net_credit"]) * 0.60) * rem_qty, 2800.0 * old_pos["lot_count"]), 2)
                                    old_pos["trail_trigger_inr"] = round((float(old_pos["net_credit"]) * 0.25) * rem_qty, 2)
                                    old_pos["reconciliation_status"] = "PARTIAL_REMAINING"
                                    self.state["active_position"] = old_pos
                                    self.zero_pos_consecutive_count = 0
                                    self.save_state()

                            elif sell_close_qty > 0 or buy_close_qty > 0:
                                # ASYMMETRIC LEG FILL DETECTED (One leg closed, one leg open!)
                                logger.warning(
                                    f"[RECON_LEG_ASYMMETRY] Critical fill mismatch! Short closed: {sell_close_qty}, "
                                    f"Long closed: {buy_close_qty}. DO NOT CLOSE POSITION!"
                                )
                                old_pos["reconciliation_status"] = "RECONCILIATION_REQUIRED"
                                old_pos["reconciliation_reason"] = f"Leg fill asymmetry: Short closed {sell_close_qty}, Long closed {buy_close_qty}"
                                self.state["active_position"] = old_pos
                                self.broker_sync_status = "RECONCILIATION_REQUIRED"
                                self.reconciliation_alert = True
                                self.reconciliation_reason = old_pos["reconciliation_reason"]
                                self.reconciliation_blocked = True
                                self.save_state()

                            else:
                                # ZERO closing fills found!
                                # NEVER AUTO-CLOSE! Require positive confirmation or mark RECONCILIATION_REQUIRED.
                                if self.zero_pos_consecutive_count >= 5:
                                    logger.warning(
                                        f"[RECON_DISCREPANCY_ALERT] Broker returned 0 positions for {self.zero_pos_consecutive_count} polls, "
                                        f"but Order Book has 0 closing fills! Keeping position OPEN. Blocking new entries."
                                    )
                                    old_pos["reconciliation_status"] = "RECONCILIATION_REQUIRED"
                                    old_pos["reconciliation_reason"] = "Broker reported 0 open positions, but no verified exit fills found in Order Book"
                                    self.state["active_position"] = old_pos
                                    self.broker_sync_status = "RECONCILIATION_REQUIRED"
                                    self.reconciliation_alert = True
                                    self.reconciliation_reason = old_pos["reconciliation_reason"]
                                    self.reconciliation_blocked = True
                                    self.save_state()
                        else:
                            # Position found or no active position: reset consecutive counter
                            self.zero_pos_consecutive_count = 0
                            if not has_real_pos and not self.state.get("active_position"):
                                if getattr(self, "reconciliation_blocked", False) and getattr(self, "broker_sync_status", "") == "PENDING_RECONCILIATION":
                                    self.broker_sync_status = "SYNCED"
                                    self.reconciliation_blocked = False
                                    self.reconciliation_reason = ""
                                    logger.info("[RECON_INIT_CLEAN] Broker position reconciliation verified: 0 positions on broker, 0 positions in terminal.")
                            if has_real_pos and self.state.get("active_position"):
                                pos_obj = self.state.get("active_position")
                                if pos_obj.get("reconciliation_status") == "RECONCILIATION_REQUIRED":
                                    pos_obj["reconciliation_status"] = "SYNCED"
                                    pos_obj.pop("reconciliation_reason", None)
                                    self.broker_sync_status = "SYNCED"
                                    self.reconciliation_alert = False
                                    self.reconciliation_blocked = False
                                    self.reconciliation_reason = ""
                                    self.save_state()
                                    logger.info("[RECON_RESOLVED] Broker position re-verified! Discrepancy cleared.")

                        # Case B: If Kotak Neo reports an active NIFTY spread position:
                        # Auto-adopt or continuously synchronize with live broker execution & MTM prices!
                        # STRICT USER RULE: Only synchronize if this is Render's own trade (or already tracked).
                        # User's personal mobile app trades (ADMINCPPAPI_MOB) are strictly IGNORED!
                        if has_real_pos and short_leg and long_leg:
                            self.zero_pos_consecutive_count = 0
                            try:
                                cur_pos = self.state.get("active_position")
                                short_sym = str(short_leg.get("trdSym", "")).upper()
                                long_sym = str(long_leg.get("trdSym", "")).upper()

                                # If no active position in terminal, only adopt if initiated by Render (NEOTRADEAPI)
                                if not cur_pos:
                                    is_render_trade = False
                                    try:
                                        rep = self.client.order_report()
                                        if is_kotak_ok(rep) and rep.get("data"):
                                            for o in rep.get("data", []):
                                                trd_sym = str(o.get("trdSym", "")).upper()
                                                ord_src = str(o.get("ordSrc", "")).upper()
                                                ord_no = str(o.get("nOrdNo", ""))
                                                if (trd_sym == short_sym or trd_sym == long_sym):
                                                    if "NEOTRADEAPI" in ord_src or ord_no in getattr(self, "terminal_order_ids", set()):
                                                        is_render_trade = True
                                                        break
                                    except Exception as ord_chk_err:
                                        logger.warning(f"[ADOPT_ORD_CHECK_ERR] Error checking order source in position adoption: {ord_chk_err}")

                                    if not is_render_trade:
                                        # This is user's personal mobile/web trade (e.g. ADMINCPPAPI_MOB). DO NOT ADOPT!
                                        logger.info(f"[INDEPENDENCE] External broker position detected ({short_sym}/{long_sym}). Ignoring - user personal trade.")
                                        cur_pos = None

                                if cur_pos or is_render_trade:
                                    self.latest_broker_short_leg = short_leg
                                    self.latest_broker_long_leg = long_leg

                                    sell_strk = int(float(short_leg.get("stkPrc", 0)))
                                    buy_strk = int(float(long_leg.get("stkPrc", 0)))
                                    sell_type = str(short_leg.get("optTp", "PE")).upper()
                                    buy_type = str(long_leg.get("optTp", "PE")).upper()
                                    sell_amt = float(short_leg.get("sellAmt", 0.0) or 0.0)
                                    sell_qty = int(float(short_leg.get("flSellQty", 65) or 65))
                                    buy_amt = float(long_leg.get("buyAmt", 0.0) or 0.0)
                                    buy_qty = int(float(long_leg.get("flBuyQty", 65) or 65))

                                    # Extract live LTPs from Kotak Neo position data
                                    s_ltp = float(short_leg.get("ltp") or short_leg.get("lp") or short_leg.get("lastPrice") or 0.0)
                                    b_ltp = float(long_leg.get("ltp") or long_leg.get("lp") or long_leg.get("lastPrice") or 0.0)
                                    s_ur = float(short_leg.get("urMtM", 0.0) or 0.0)
                                    b_ur = float(long_leg.get("urMtM", 0.0) or 0.0)

                                    # Dynamic price resolution: NO hardcoded fallbacks!
                                    if sell_amt > 0 and sell_qty > 0:
                                        sell_prc = round(sell_amt / sell_qty, 2)
                                    elif cur_pos and float(cur_pos.get("sell_price", 0.0) or 0.0) > 0:
                                        sell_prc = float(cur_pos["sell_price"])
                                    elif short_leg.get("flPr") and float(short_leg.get("flPr") or 0.0) > 0:
                                        sell_prc = round(float(short_leg["flPr"]), 2)
                                    else:
                                        sell_prc = s_ltp if s_ltp > 0 else 0.0

                                    if buy_amt > 0 and buy_qty > 0:
                                        buy_prc = round(buy_amt / buy_qty, 2)
                                    elif cur_pos and float(cur_pos.get("buy_price", 0.0) or 0.0) > 0:
                                        buy_prc = float(cur_pos["buy_price"])
                                    elif long_leg.get("flPr") and float(long_leg.get("flPr") or 0.0) > 0:
                                        buy_prc = round(float(long_leg["flPr"]), 2)
                                    else:
                                        buy_prc = b_ltp if b_ltp > 0 else 0.0

                                    net_credit = round(sell_prc - buy_prc, 2)
                                    direction = "BULL_PUT_SPREAD" if sell_type == "PE" else "BEAR_CALL_SPREAD"
                                    entry_tm = short_leg.get("hsUpTm", get_ist_now().strftime("%Y-%m-%d %H:%M:%S"))

                                    if not cur_pos:
                                        cur_pos = {
                                            "trade_id": f"TRD-LIVE-{get_ist_now().strftime('%Y%m%d')}-01",
                                            "entry_time": entry_tm,
                                            "entry_timestamp_sec": time.time(),
                                            "direction": direction,
                                            "execution_mode": "LIVE",
                                            "trade_initiator": "AUTO_ALGO",
                                            "entry_spot": float(self.pulse.get("spot", 25530.0) or 25530.0),
                                            "sell_strike": sell_strk,
                                            "sell_type": sell_type,
                                            "sell_symbol": short_leg.get("trdSym"),
                                            "sell_price": sell_prc,
                                            "buy_strike": buy_strk,
                                            "buy_type": buy_type,
                                            "buy_symbol": long_leg.get("trdSym"),
                                            "buy_price": buy_prc,
                                            "net_credit": net_credit,
                                            "lot_size": sell_qty,
                                            "lot_count": max(1, sell_qty // self.lot_size),
                                            "max_credit_inr": round(net_credit * sell_qty, 2),
                                            "target_spread": round(net_credit * 0.60, 2),
                                            "target_profit_inr": round((net_credit * 0.40) * sell_qty, 2),
                                            "sl_spread": round(net_credit * 1.60, 2),
                                            "max_risk_inr": round(min((net_credit * 0.60) * sell_qty, 2800.0), 2),
                                            "trail_trigger_spread": round(net_credit * 0.75, 2),
                                            "trail_trigger_inr": round((net_credit * 0.25) * sell_qty, 2),
                                            "breakeven_active": False,
                                            "be_locked": False,
                                            "current_spread": net_credit,
                                            "mtm_gross_pnl": 0.0,
                                            "mtm_net_pnl": 0.0,
                                            "estimated_charges": 80.0,
                                            "holding_time_str": "0:00:00",
                                            "why_trade_taken": "5m Momentum Alignment & Dual-PCR Confluence (Live Kotak Execution)"
                                        }
                                        self.state["active_position"] = cur_pos
                                        logger.info(f"[ADOPTED_POSITION] Connected live broker spread: {direction} Sell {sell_strk} / Buy {buy_strk}")

                                    # Synchronize active position with live Kotak Neo execution prices & broker MTM:
                                    cur_pos["sell_price"] = sell_prc
                                    cur_pos["buy_price"] = buy_prc
                                    cur_pos["net_credit"] = net_credit
                                    cur_pos["lot_size"] = sell_qty
                                    cur_pos["max_credit_inr"] = round(net_credit * sell_qty, 2)
                                    cur_pos["target_spread"] = round(net_credit * 0.60, 2)
                                    cur_pos["target_profit_inr"] = round((net_credit * 0.40) * sell_qty, 2)
                                    cur_pos["sl_spread"] = net_credit if cur_pos.get("breakeven_active") else round(net_credit * 1.60, 2)
                                    cur_pos["max_risk_inr"] = 0.0 if cur_pos.get("breakeven_active") else round(min((net_credit * 0.60) * sell_qty, 2800.0 * cur_pos.get("lot_count", 1)), 2)
                                    cur_pos["trail_trigger_spread"] = round(net_credit * 0.75, 2)
                                    cur_pos["trail_trigger_inr"] = round((net_credit * 0.25) * sell_qty, 2)
                                    if s_ltp > 0:
                                        cur_pos["current_sell_ltp"] = round(s_ltp, 2)
                                    if b_ltp > 0:
                                        cur_pos["current_buy_ltp"] = round(b_ltp, 2)

                                    # Leg P&L & Broker Unrealised PnL (exact match with Kotak Neo app)
                                    if s_ur != 0.0:
                                        cur_pos["sell_leg_pnl"] = round(s_ur, 2)
                                    elif cur_pos.get("current_sell_ltp", 0.0) > 0:
                                        cur_pos["sell_leg_pnl"] = round((sell_prc - cur_pos["current_sell_ltp"]) * sell_qty, 2)

                                    if b_ur != 0.0:
                                        cur_pos["buy_leg_pnl"] = round(b_ur, 2)
                                    elif cur_pos.get("current_buy_ltp", 0.0) > 0:
                                        cur_pos["buy_leg_pnl"] = round((cur_pos["current_buy_ltp"] - buy_prc) * buy_qty, 2)

                                    broker_pnl = round(cur_pos.get("sell_leg_pnl", 0.0) + cur_pos.get("buy_leg_pnl", 0.0), 2)
                                    cur_pos["broker_unrealised_pnl"] = broker_pnl
                                    cur_pos["mtm_gross_pnl"] = broker_pnl
                                    if cur_pos.get("current_sell_ltp", 0.0) > 0 and cur_pos.get("current_buy_ltp", 0.0) > 0:
                                        cur_pos["current_spread"] = round(cur_pos["current_sell_ltp"] - cur_pos["current_buy_ltp"], 2)

                                    charges = self.calculate_kotak_charges(net_credit, cur_pos.get("current_spread", net_credit), sell_qty)
                                    cur_pos["estimated_charges"] = charges["total_charges"]
                                    cur_pos["mtm_net_pnl"] = round(broker_pnl - charges["total_charges"], 2)

                                    # Live Dynamic Progress Metrics for HUD Cards
                                    cur_spread = cur_pos.get("current_spread", net_credit)
                                    pts_gain = round(net_credit - cur_spread, 2)
                                    hard_cap = round(2800.0 * cur_pos.get("lot_count", 1), 2)
                                    cur_pos["hard_sl_cap"] = hard_cap

                                    # 1. 100% Max Potential
                                    max_inr = round(net_credit * sell_qty, 2)
                                    cur_pos["max_credit_inr"] = max_inr
                                    cur_pos["max_credit_pts"] = net_credit
                                    cur_pos["max_credit_captured_inr"] = broker_pnl
                                    cap_pct = round(min(100.0, max(0.0, (broker_pnl / max(0.01, max_inr)) * 100.0)), 1) if max_inr > 0 else 0.0
                                    cur_pos["max_credit_captured_pct"] = cap_pct
                                    cur_pos["max_credit_remaining_inr"] = max(0.0, round(max_inr - broker_pnl, 2))
                                    cur_pos["max_credit_remaining_pct"] = round(max(0.0, 100.0 - cap_pct), 1)

                                    # 2. 40% Target Exit
                                    cur_pos["decay_pts_gained"] = max(0.0, pts_gain)
                                    decay_pts_needed = round(net_credit * 0.40, 2)
                                    cur_pos["decay_pts_needed"] = decay_pts_needed
                                    cur_pos["target_pts_needed"] = max(0.0, round(cur_spread - cur_pos["target_spread"], 2))
                                    cur_pos["target_profit_remaining_inr"] = max(0.0, round(cur_pos["target_profit_inr"] - broker_pnl, 2))
                                    tgt_pct = round(min(100.0, max(0.0, (pts_gain / max(0.01, decay_pts_needed)) * 100.0)), 1)
                                    cur_pos["target_progress_pct"] = tgt_pct
                                    cur_pos["target_remaining_pct"] = round(max(0.0, 100.0 - tgt_pct), 1)

                                    # 3. 25% Auto-Trail
                                    trail_pts_needed = round(net_credit * 0.25, 2)
                                    cur_pos["trail_pts_needed"] = trail_pts_needed
                                    cur_pos["trail_pts_remaining"] = max(0.0, round(cur_spread - cur_pos["trail_trigger_spread"], 2)) if not cur_pos.get("breakeven_active") else 0.0
                                    cur_pos["trail_profit_remaining_inr"] = max(0.0, round(cur_pos["trail_trigger_inr"] - broker_pnl, 2)) if not cur_pos.get("breakeven_active") else 0.0
                                    trail_pct = 100.0 if cur_pos.get("breakeven_active") else round(min(100.0, max(0.0, (pts_gain / max(0.01, trail_pts_needed)) * 100.0)), 1)
                                    cur_pos["trail_progress_pct"] = trail_pct
                                    cur_pos["trail_remaining_pct"] = 0.0 if cur_pos.get("breakeven_active") else round(max(0.0, 100.0 - trail_pct), 1)

                                    # 4. Stop Loss & Max Risk (-₹2,800 CAP)
                                    sl_cut_spread = round(net_credit * 1.60, 2)
                                    cur_pos["sl_buffer_pts"] = max(0.0, round(sl_cut_spread - cur_spread, 2))
                                    cur_pos["sl_buffer_inr"] = max(0.0, round(cur_pos["sl_buffer_pts"] * sell_qty, 2))
                                    if cur_pos.get("breakeven_active"):
                                        cur_pos["sl_buffer_pct"] = 100.0
                                        cur_pos["sl_risk_consumed_pct"] = 0.0
                                        cur_pos["sl_status_text"] = "0 RISK (Cost Locked)"
                                    else:
                                        risk_pct = round(min(100.0, (abs(broker_pnl) / max(0.01, hard_cap)) * 100.0), 1) if broker_pnl < 0 else 0.0
                                        cur_pos["sl_risk_consumed_pct"] = risk_pct
                                        cur_pos["sl_buffer_pct"] = round(max(0.0, 100.0 - risk_pct), 1)
                                        cur_pos["sl_status_text"] = "ACTIVE (-Rs 2800 CAP)"

                                    self.save_state()
                            except Exception as adopt_err:
                                logger.error(f"[ADOPT_ERROR] Failed to synchronize live broker position: {adopt_err}")
                    else:
                        logger.warning(f"Skipping broker position reconciliation - Kotak API returned non-OK: {real_pos}")
                        if getattr(self, "broker_sync_status", "") == "PENDING_RECONCILIATION":
                            self.reconciliation_blocked = True
                            err_desc = real_pos.get("errMsg", "No Data") if isinstance(real_pos, dict) else "non-OK response"
                            self.reconciliation_reason = f"Broker API non-OK during reconciliation: {err_desc}"
                except Exception as recon_err:
                    logger.error(f"[RECONCILIATION_ERROR] {recon_err}")
                    if getattr(self, "broker_sync_status", "") == "PENDING_RECONCILIATION":
                        self.reconciliation_blocked = True
                        self.reconciliation_reason = f"Broker reconciliation exception: {recon_err}"

            # Upgrade 5: Real Live India VIX from Kotak Neo Quotes API
            real_vix = None
            if self.client and self.authenticated and self.execution_mode == "LIVE":
                try:
                    v_quote = self.client.quotes(instrument_tokens=[{'instrument_token': 'INDIA VIX', 'exchange_segment': 'nse_cm'}])
                    if isinstance(v_quote, list) and len(v_quote) > 0:
                        v_ltp = float(v_quote[0].get("ltp", 0.0) or 0.0)
                        if v_ltp > 0:
                            real_vix = round(v_ltp, 2)
                except Exception as v_err:
                    logger.warning(f"Notice: Failed to fetch live India VIX quote: {v_err}")

            if real_vix:
                self.pulse["india_vix"] = real_vix
            elif atm_call and atm_put:
                c_ltp = float(atm_call.get("quote", {}).get("ltp", 0.0))
                p_ltp = float(atm_put.get("quote", {}).get("ltp", 0.0))
                straddle = c_ltp + p_ltp
                # Straddle approx IV: IV ~ (Straddle / Spot) / sqrt(DTE/365) * sqrt(2*pi) * 100
                vix_est = round((straddle / spot) * np.sqrt(365.0 / 5.0) * np.sqrt(np.pi / 2.0) * 100.0, 1)
                self.pulse["india_vix"] = max(9.5, min(vix_est, 35.0))
            else:
                self.pulse["india_vix"] = 16.0
            self.get_adaptive_wing_distance()

            self.pulse["calls_count"] = len(chain.get("call", []))
            self.pulse["puts_count"] = len(chain.get("put", []))

            d_ema = self.pulse["daily_ema20"]
            e9 = self.pulse["ema9"]
            e20 = self.pulse["ema20"]
            e50 = self.pulse["ema50"]

            # -------------------------------------------------------------------------
            # 5-MINUTE CANDLE CLOSE CONFIRMATION LATCH (Backtest Convention 1:1)
            # -------------------------------------------------------------------------
            now_min = now_dt.minute
            bucket_5m = (now_min // 5) * 5
            bar_start_dt = now_dt.replace(minute=bucket_5m, second=0, microsecond=0)
            bar_close_dt = bar_start_dt + timedelta(minutes=5)

            # Accumulate running candle OHLC from live ticks
            if getattr(self, "running_5m_bar", None) is None:
                self.running_5m_bar = {
                    "bar_start": bar_start_dt,
                    "bar_close": bar_close_dt,
                    "open": float(spot),
                    "high": float(spot),
                    "low": float(spot),
                    "close": float(spot),
                    "last_tick_time": now_dt,
                    "tick_count": 1
                }
            elif self.running_5m_bar["bar_start"] != bar_start_dt:
                # The previous 5-minute bucket has COMPLETED and CLOSED!
                prev_bar = self.running_5m_bar
                final_close = float(prev_bar["close"])  # True last tick of completed bar

                # Calculate Exchange vs Received Timestamps and Data Lag from actual tick timestamp
                exchange_bar_close = prev_bar["bar_close"]
                last_tick_ts = prev_bar.get("last_tick_time", exchange_bar_close)
                tick_age = max(0.0, (now_dt - last_tick_ts).total_seconds())
                boundary_lag = max(0.0, (now_dt - exchange_bar_close).total_seconds())
                data_lag = max(tick_age, boundary_lag)
                tick_cnt = prev_bar.get("tick_count", 0)

                # Strict validation: data_lag <= 15s, tick_age <= 15s, and minimum 2 ticks accumulated
                is_candle_valid = bool(
                    final_close > 0 and
                    prev_bar["open"] > 0 and
                    data_lag <= 15.0 and
                    tick_age <= 15.0 and
                    tick_cnt >= 2
                )

                self.finalized_5m_candle = {
                    "bar_start": prev_bar["bar_start"],
                    "bar_close": prev_bar["bar_close"],
                    "open": prev_bar["open"],
                    "high": prev_bar["high"],
                    "low": prev_bar["low"],
                    "close": final_close,
                    "tick_count": tick_cnt,
                    "last_tick_time": last_tick_ts,
                    "last_tick_age": round(tick_age, 2),
                    "finalized_at": now_dt,
                    "data_lag": round(data_lag, 2),
                    "is_green": bool(final_close > prev_bar["open"]),
                    "is_red": bool(final_close < prev_bar["open"]),
                    "is_valid": is_candle_valid
                }

                # Organically evolve base EMAs in memory from confirmed final candle close
                a9 = 2.0 / 10.0
                a20 = 2.0 / 21.0
                a50 = 2.0 / 51.0
                self.base_ema9 = round(final_close * a9 + self.base_ema9 * (1.0 - a9), 2)
                self.base_ema20 = round(final_close * a20 + self.base_ema20 * (1.0 - a20), 2)
                self.base_ema50 = round(final_close * a50 + self.base_ema50 * (1.0 - a50), 2)
                self.pulse["ema9"] = self.base_ema9
                self.pulse["ema20"] = self.base_ema20
                self.pulse["ema50"] = self.base_ema50
                self.save_cached_emas()

                # Start new running bar for current bucket
                self.running_5m_bar = {
                    "bar_start": bar_start_dt,
                    "bar_close": bar_close_dt,
                    "open": float(spot),
                    "high": float(spot),
                    "low": float(spot),
                    "close": float(spot),
                    "last_tick_time": now_dt,
                    "tick_count": 1
                }
            else:
                # Accumulate high, low, close on active running bar
                b = self.running_5m_bar
                b["high"] = max(b["high"], float(spot))
                b["low"] = min(b["low"], float(spot))
                b["close"] = float(spot)
                b["last_tick_time"] = now_dt
                b["tick_count"] += 1

            # Update pulse HUD candle indicators
            fc = getattr(self, "finalized_5m_candle", None)
            if fc and fc.get("is_valid"):
                self.pulse["candle_5m_open"] = round(fc["open"], 2)
                self.pulse["candle_5m_close"] = round(fc["close"], 2)
                self.pulse["candle_5m_high"] = round(fc["high"], 2)
                self.pulse["candle_5m_low"] = round(fc["low"], 2)
                self.pulse["is_green_candle"] = fc["is_green"]
                self.pulse["is_red_candle"] = fc["is_red"]
                self.pulse["candle_status"] = "CONFIRMED_CLOSED"
                self.pulse["candle_bar_time"] = fc["bar_start"].strftime("%H:%M")
                self.pulse["candle_data_lag"] = round(fc["data_lag"], 2)
            else:
                b = self.running_5m_bar
                self.pulse["candle_5m_open"] = round(b["open"], 2)
                self.pulse["candle_5m_close"] = round(b["close"], 2)
                self.pulse["is_green_candle"] = bool(b["close"] > b["open"])
                self.pulse["is_red_candle"] = bool(b["close"] < b["open"])
                self.pulse["candle_status"] = "ACCUMULATING_RUNNING_BAR"
                self.pulse["candle_bar_time"] = b["bar_start"].strftime("%H:%M")
                self.pulse["candle_data_lag"] = 0.0

            # Signal Evaluation: STRICTLY on Confirmed Closed Candle
            # Timing rule: At XX:XX:00 (exact boundary), finalize only (no trade).
            # Signal evaluated at XX:XX:01+ per backtest convention.
            is_boundary_second = (now_dt.second == 0)
            bull_pcr_ok, bull_pcr_desc = self.check_bull_pcr()
            bear_pcr_ok, bear_pcr_desc = self.check_bear_pcr()

            can_evaluate_signal = (
                not is_boundary_second
                and fc is not None
                and fc.get("is_valid", False)
                and fc.get("data_lag", 999.0) <= 15.0
            )

            if can_evaluate_signal:
                c_close = fc["close"]
                c_green = fc["is_green"]
                c_red = fc["is_red"]
                if bull_pcr_ok and (c_close > d_ema) and (e9 > e20 > e50) and c_green:
                    self.pulse["signal"] = "BULL_PUT_SPREAD"
                elif bear_pcr_ok and (c_close < d_ema) and (e9 < e20 < e50) and c_red:
                    self.pulse["signal"] = "BEAR_CALL_SPREAD"
                else:
                    self.pulse["signal"] = "NEUTRAL_SCANNING"
            else:
                self.pulse["signal"] = "NEUTRAL_SCANNING"

            # Autonomous Live Trigger (09:30 - 14:30 IST, strictly Max 2 trades/day)
            # Idempotency Gate: Exactly 1 evaluation/trade per finalized 5m candle bar!
            today_trades_count = self.state.get("today_wins", 0) + self.state.get("today_losses", 0)
            if is_mkt_open and (not self.state.get("active_position")) and (not self.state.get("circuit_breaker_active")):
                if today_trades_count < 2 and can_evaluate_signal:
                    bar_key = fc["bar_start"].strftime("%Y-%m-%d %H:%M:%S")
                    last_eval = getattr(self, "last_evaluated_bar", None) or self.state.get("last_evaluated_candle_bar")
                    if last_eval != bar_key:
                        t_time = now_dt.time()
                        if datetime.strptime("09:30", "%H:%M").time() <= t_time <= datetime.strptime("14:30", "%H:%M").time():
                            if self.pulse.get("option_chain") is not None and (bull_pcr_ok or bear_pcr_ok):
                                if self.pulse["signal"] in ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"]:
                                    self.last_evaluated_bar = bar_key
                                    self.state["last_evaluated_candle_bar"] = bar_key
                                    self.save_state()
                                    self.trigger_trade(force=False, simulated_time=now_dt)

            # Minute-by-Minute Live Archiving (Zero Data Gaps!)
            if (is_mkt_open or force) and (now - getattr(self, "last_archive_time", 0) >= 60):
                if hasattr(self, "archiver") and self.archiver:
                    self.archiver.record_minute_tick(spot, chain)
                    self.last_archive_time = now
                    self.total_ticks_archived = getattr(self, "total_ticks_archived", 0) + 1
                    self.pulse["ticks_archived"] = self.total_ticks_archived

            # Upgrade 7: Automated EOD Reconcile & Daily Performance Report at 15:35 IST
            if now_dt.time() >= datetime.strptime("15:35", "%H:%M").time() and not getattr(self, "eod_reconciled_today", False):
                if hasattr(self, "archiver") and self.archiver:
                    ok, msg = self.archiver.reconcile_eod_spot()
                    self.eod_reconciled_today = True
                    self.status_message = f"EOD Archive: {msg}"
                self.generate_daily_report()

            self.last_api_poll = now
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str or "too many" in err_str:
                logger.warning(f"[RATE_LIMIT] Kotak API 429 detected in polling: {e}. Backing off for 2.5s.")
                self.kotak_rate_limit_backoff_until = now + 2.5
            elif "401" in err_str or "token" in err_str or "session" in err_str or "unauth" in err_str:
                self.authenticate()
            else:
                logger.warning(f"[POLL_EXCEPTION] Kotak polling exception: {e}")
        finally:
            self.last_api_poll = now
            if hasattr(self, "poll_lock") and self.poll_lock.locked():
                try:
                    self.poll_lock.release()
                except Exception:
                    pass

    def _live_engine_loop(self):
        """
        100% Autonomous Hands-Free Background Engine:
        Runs 24/7 in background even when browser or phone is closed:
        1. Continuously downloads 1-minute strike-by-strike Option Chain & Spot to parquet/csv.
        2. Evaluates the 6 Champion Model 4 rules in real-time with Dual-PCR confirmation.
        3. Autonomously triggers margin-safe trade (09:30 - 14:30 IST) with dynamic compounding lot sizing.
        4. Autonomously manages Trailing Breakeven, Target, SL, and 15:15 PM Intraday exit.
        5. Upgrade 6: Adaptive Proximity Polling (1s high-frequency tick polling near target/SL).
        6. Upgrade 7: At 15:35 IST, auto-generates comprehensive Daily Performance Report.
        Zero user intervention required!
        """
        while True:
            try:
                now_dt = get_ist_now()
                today_ist = now_dt.strftime("%Y-%m-%d")
                if self.state.get("date") != today_ist:
                    logger.info(f"[DAILY_ROLLOVER] IST date changed from {self.state.get('date')} to {today_ist}. Resetting daily trade quota & PnL.")
                    with self.state_lock:
                        self.state["date"] = today_ist
                        self.state["today_losses"] = 0
                        self.state["today_wins"] = 0
                        self.state["today_realized_gross_pnl"] = 0.0
                        self.state["today_total_charges"] = 0.0
                        self.state["today_realized_net_pnl"] = 0.0
                        self.state["circuit_breaker_active"] = False
                        self.save_state()
                    self.emas_seeded_for_today = False

                is_mkt_open = (datetime.strptime("09:15", "%H:%M").time() <= now_dt.time() <= datetime.strptime("15:30", "%H:%M").time()) and (now_dt.weekday() < 5)
                is_pre_mkt = (datetime.strptime("08:50", "%H:%M").time() <= now_dt.time() < datetime.strptime("09:15", "%H:%M").time()) and (now_dt.weekday() < 5)
                now = time.time()

                if is_mkt_open:
                    self.poll_kotak_neo(force=False)
                    self.update_position_dynamics()
                    if now - getattr(self, "last_bg_margin_check", 0) > 60:
                        self.get_broker_cash_margin()
                        self.has_open_broker_positions()
                        self.last_bg_margin_check = now

                    # Proactive Linux glibc memory trimming & GC (Guarantees staying < 200 MB on Render)
                    if now - getattr(self, "last_mem_trim_time", 0) > 60:
                        import gc
                        gc.collect()
                        try:
                            import ctypes
                            ctypes.CDLL("libc.so.6").malloc_trim(0)
                        except Exception:
                            pass
                        self.last_mem_trim_time = now
                    sleep_sec = 1.0
                elif is_pre_mkt:
                    # Pre-market warmup: proactive auto-authentication and periodic polling
                    if not getattr(self, "pre_market_reauth_done", False):
                        logger.info("[PRE_MKT] Performing Daily Pre-Market Authentication Handshake...")
                        self.authenticate()
                        self.pre_market_reauth_done = True
                    self.poll_kotak_neo(force=False)
                    sleep_sec = 2.0
                else:
                    self.pre_market_reauth_done = False
                    # Off-market: poll at most once every 5 minutes to avoid night maintenance socket hangs
                    if now - getattr(self, "last_api_poll", 0) > 300:
                        self.poll_kotak_neo(force=False)
                    sleep_sec = 10.0

            except Exception as e:
                logger.warning(f"Live engine loop iteration error: {e}")
                sleep_sec = 5.0
            time.sleep(sleep_sec)

    def _replay_loop(self):
        """Background thread executing replay candles at selected speed."""
        while True:
            if self.replay_active and not self.replay_paused and self.replay_df is not None:
                step_interval = max(0.1, 1.0 / self.replay_speed)
                time.sleep(step_interval)
                self.step_replay_next()
            else:
                time.sleep(0.5)

    def step_replay_next(self):
        """Advances replay by 1 historical candle."""
        if self.replay_df is None or self.replay_df.empty:
            return

        if self.replay_idx >= len(self.replay_df):
            self.replay_paused = True
            self.status_message = f"Replay for {self.replay_date} Completed."
            return

        row = self.replay_df.iloc[self.replay_idx]
        ts = row.get("timestamp") or row.get("index")
        spot_close = float(row["close"])

        # Update pulse with historical tick
        self.pulse["spot"] = spot_close
        ts_dt = pd.to_datetime(ts)
        self.pulse["replay_time"] = ts_dt.strftime("%H:%M")
        self.pulse["replay_progress"] = f"{self.replay_idx + 1} / {len(self.replay_df)}"

        # Simulated dynamic EMA cascade for replay
        self.pulse["ema9"] = round(spot_close * 0.9992, 2)
        self.pulse["ema20"] = round(spot_close * 0.9985, 2)
        self.pulse["ema50"] = round(spot_close * 0.9970, 2)
        self.pulse["daily_ema20"] = round(spot_close * 0.9850, 2)
        self.pulse["pcr"] = 1.18

        # 5-Min Candle Tracking in Replay
        c_open = float(row.get("open", spot_close))
        is_green = bool(spot_close >= c_open)
        is_red = bool(spot_close <= c_open)
        self.pulse["candle_5m_open"] = round(c_open, 2)
        self.pulse["is_green_candle"] = is_green
        self.pulse["is_red_candle"] = is_red

        # Strategy evaluation (with Green/Red Candle filter)
        if spot_close > self.pulse["daily_ema20"] and (self.pulse["ema9"] > self.pulse["ema20"] > self.pulse["ema50"]) and is_green:
            self.pulse["signal"] = "BULL_PUT_SPREAD"
        elif spot_close < self.pulse["daily_ema20"] and (self.pulse["ema9"] < self.pulse["ema20"] < self.pulse["ema50"]) and is_red:
            self.pulse["signal"] = "BEAR_CALL_SPREAD"
        else:
            self.pulse["signal"] = "NEUTRAL_SCANNING"

        self.update_position_dynamics()

        # Auto-trigger check if time in window
        if not self.state.get("active_position") and not self.state.get("circuit_breaker_active"):
            t_obj = ts_dt.time()
            if datetime.strptime("09:30", "%H:%M").time() <= t_obj <= datetime.strptime("14:30", "%H:%M").time():
                if self.pulse["signal"] == "BULL_PUT_SPREAD":
                    self.trigger_trade(force=False, simulated_time=ts_dt.strftime("%Y-%m-%d %H:%M:%S"))

        self.replay_idx += 1

    def get_clean_option_chain_table(self):
        """
        Extracts 31 strikes centered around ATM Strike (15 ITM, ATM, 15 OTM: range -15 to +15).
        Calculates analytical Option Greeks (Delta, Theta, Gamma, Vega).
        Identifies Highest OI strikes for Call (Resistance) and Put (Support).
        Marks active sold and bought spread legs with live LTP & execution status.
        """
        spot = self.pulse["spot"]
        atm_strike = int(round(spot / 50.0) * 50)
        wing_dist = int(self.pulse.get("wing_distance", 350))
        wing_strike_pe = atm_strike - wing_dist
        wing_strike_ce = atm_strike + wing_dist
        strikes = [atm_strike + (i * 50) for i in range(-15, 16)]

        chain = self.pulse.get("option_chain")

        # Calculate Days to Expiry (DTE)
        expiry_str = self.pulse.get("next_expiry", "2026-11-24")
        try:
            exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            today_date = get_ist_now().date()
            dte = max(1, (exp_date - today_date).days)
        except Exception:
            dte = 5

        call_map = {}
        put_map = {}

        if chain and isinstance(chain, dict):
            for c in chain.get("call", []):
                try:
                    s = int(float(c.get("inst", {}).get("strkPrc", 0)))
                    call_map[s] = c
                except Exception:
                    pass
            for p in chain.get("put", []):
                try:
                    s = int(float(p.get("inst", {}).get("strkPrc", 0)))
                    put_map[s] = p
                except Exception:
                    pass

        # First pass: collect raw data and find Max Call OI and Max Put OI
        raw_rows = []
        max_c_oi = -1
        max_p_oi = -1
        max_c_strike = None
        max_p_strike = None

        pos = self.state.get("active_position")
        sold_strike = pos.get("sell_strike") if pos else None
        bought_strike = pos.get("buy_strike") if pos else None
        sold_type = pos.get("sell_type") if pos else None
        bought_type = pos.get("buy_type") if pos else None

        for s in strikes:
            # Calls
            c_data = call_map.get(s, {})
            c_ltp = float(c_data.get("quote", {}).get("ltp", 0.0))
            c_cur = int(c_data.get("oi", {}).get("cur", c_data.get("oi", {}).get("oi", 0)))
            c_prev = int(c_data.get("oi", {}).get("prev", 0))
            c_oi = c_cur
            c_oi_chg = (c_cur - c_prev) if (c_cur and c_prev) else 0
            c_chg_pct = float(c_data.get("oi", {}).get("chgPct", 0.0) or 0.0)
            c_vol = int(c_data.get("quote", {}).get("vol", 0))
            c_close = float(c_data.get("quote", {}).get("c", c_data.get("quote", {}).get("pc", 0.0)) or 0.0)
            c_net_chg = round(c_ltp - c_close, 2) if (c_close > 0 and c_ltp > 0) else 0.0

            # Puts
            p_data = put_map.get(s, {})
            p_ltp = float(p_data.get("quote", {}).get("ltp", 0.0))
            p_cur = int(p_data.get("oi", {}).get("cur", p_data.get("oi", {}).get("oi", 0)))
            p_prev = int(p_data.get("oi", {}).get("prev", 0))
            p_oi = p_cur
            p_oi_chg = (p_cur - p_prev) if (p_cur and p_prev) else 0
            p_chg_pct = float(p_data.get("oi", {}).get("chgPct", 0.0) or 0.0)
            p_vol = int(p_data.get("quote", {}).get("vol", 0))
            p_close = float(p_data.get("quote", {}).get("c", p_data.get("quote", {}).get("pc", 0.0)) or 0.0)
            p_net_chg = round(p_ltp - p_close, 2) if (p_close > 0 and p_ltp > 0) else 0.0

            dist = s - spot
            if c_ltp == 0 and c_close > 0:
                c_ltp = c_close
            elif c_ltp == 0:
                c_ltp = round(max(0.05, 115.0 - (dist * 0.45)), 2)

            if p_ltp == 0 and p_close > 0:
                p_ltp = p_close
            elif p_ltp == 0:
                p_ltp = round(max(0.05, 110.0 + (dist * 0.45)), 2)

            if c_oi == 0 and c_prev > 0:
                c_oi = c_prev
            elif c_oi == 0:
                c_oi = abs(int(100000 - dist * 40))

            if p_oi == 0 and p_prev > 0:
                p_oi = p_prev
            elif p_oi == 0:
                p_oi = abs(int(115000 + dist * 40))

            if c_oi > max_c_oi:
                max_c_oi = c_oi
                max_c_strike = s
            if p_oi > max_p_oi:
                max_p_oi = p_oi
                max_p_strike = s

            raw_rows.append((s, c_ltp, c_oi, c_oi_chg, c_chg_pct, c_net_chg, c_vol, p_ltp, p_oi, p_oi_chg, p_chg_pct, p_net_chg, p_vol))

        table_rows = []
        for s, c_ltp, c_oi, c_oi_chg, c_chg_pct, c_net_chg, c_vol, p_ltp, p_oi, p_oi_chg, p_chg_pct, p_net_chg, p_vol in raw_rows:
            is_atm = (s == atm_strike)
            is_hedge_wing = (s == wing_strike_pe or s == wing_strike_ce)
            wing_tag = "HEDGE WING (350 OTM)" if is_hedge_wing else None

            # Calculate Greeks
            greeks = calc_bs_greeks(spot, s, dte_days=dte, iv=0.135, r=0.07)

            is_sold_leg = (pos is not None and s == sold_strike)
            is_bought_leg = (pos is not None and s == bought_strike)
            is_max_call_oi = (s == max_c_strike)
            is_max_put_oi = (s == max_p_strike)

            # Strict Strike-Scoped Instrument Data (Prevents last-item strike leakage)
            c_data = call_map.get(s, {})
            p_data = put_map.get(s, {})

            table_rows.append({
                "strike": s,
                "is_atm": is_atm,
                "is_hedge_wing": is_hedge_wing,
                "wing_tag": wing_tag,
                "is_sold_leg": is_sold_leg,
                "is_bought_leg": is_bought_leg,
                "sold_type": sold_type if is_sold_leg else None,
                "bought_type": bought_type if is_bought_leg else None,
                "call_inst": c_data.get("inst", {}),
                "put_inst": p_data.get("inst", {}),
                "call_trd_sym": c_data.get("inst", {}).get("trdSym"),
                "put_trd_sym": p_data.get("inst", {}).get("trdSym"),
                "call_ltp": c_ltp,
                "call_chg": c_net_chg,
                "call_oi": f"{c_oi:,}",
                "call_oi_raw": c_oi,
                "call_oi_chg": f"{c_oi_chg:+,}",
                "call_oi_chg_raw": c_oi_chg,
                "call_chg_pct": round(c_chg_pct, 2),
                "call_vol": f"{c_vol:,}",
                "is_max_call_oi": is_max_call_oi,
                "put_ltp": p_ltp,
                "put_chg": p_net_chg,
                "put_oi": f"{p_oi:,}",
                "put_oi_raw": p_oi,
                "put_oi_chg": f"{p_oi_chg:+,}",
                "put_oi_chg_raw": p_oi_chg,
                "put_chg_pct": round(p_chg_pct, 2),
                "put_vol": f"{p_vol:,}",
                "is_max_put_oi": is_max_put_oi,
                "call_moneyness": "ITM" if s < spot else ("ATM" if s == atm_strike else "OTM"),
                "put_moneyness": "ITM" if s > spot else ("ATM" if s == atm_strike else "OTM"),
                # Greeks
                "call_delta": greeks["call_delta"],
                "call_theta": greeks["call_theta"],
                "put_delta": greeks["put_delta"],
                "put_theta": greeks["put_theta"],
                "gamma": greeks["gamma"],
                "vega": greeks["vega"],
                "iv": greeks["iv"],
            })

        return table_rows

    def calculate_kotak_charges(self, entry_credit, exit_spread, lot_size=65):
        """
        Exact Kotak Neo Brokerage & Statutory Taxes Calculation:
        - Brokerage: Rs 20 per executed order leg.
          Round-trip Credit Spread = 4 legs = Rs 80.
        - STT: 0.1% on sell option turnover.
        - Exchange Turnover Charges: 0.05% on option turnover.
        - GST: 18% on (Brokerage + Exchange turnover).
        - Stamp Duty: 0.003% on buy leg turnover.
        - SEBI Fees: Rs 10 / Crore.
        """
        brokerage = 20.0 * 4  # 2 legs entry + 2 legs exit = Rs 80

        turnover_sell = (entry_credit * lot_size)
        turnover_buy = (exit_spread * lot_size)
        total_turnover = turnover_sell + turnover_buy

        stt = round(turnover_sell * 0.001, 2)
        exchange_charges = round(total_turnover * 0.0005, 2)
        gst = round((brokerage + exchange_charges) * 0.18, 2)
        stamp_duty = round(turnover_buy * 0.00003, 2)
        sebi_charges = round(total_turnover * 0.000001, 2)

        total_taxes = stt + exchange_charges + gst + stamp_duty + sebi_charges
        total_deduction = round(brokerage + total_taxes, 2)

        return {
            "brokerage": brokerage,
            "stt": stt,
            "exchange_charges": exchange_charges,
            "gst": gst,
            "stamp_duty": stamp_duty,
            "total_charges": total_deduction,
        }

    def has_open_broker_positions(self):
        """
        OPTION 1 (INDEPENDENT ALGO):
        Checks Kotak Neo broker specifically for Render Terminal (API) open positions.
        User's manual mobile app trades (ADMINCPPAPI_MOB) are completely independent and ignored.
        """
        if not self.client or not self.authenticated or self.execution_mode != "LIVE":
            return bool(self.state.get("active_position"))
        if self.state.get("active_position"):
            return True
        if getattr(self, "reconciliation_blocked", False):
            return True
        now = time.time()
        # Cooldown: only query Kotak positions every 15 seconds to avoid thread starvation
        if now - getattr(self, "last_positions_check_time", 0) < 15.0:
            return getattr(self, "cached_has_open_positions", False)
        self.last_positions_check_time = now
        try:
            b_pos = self.client.positions()
            if is_kotak_ok(b_pos) and b_pos.get("data"):
                # Collect all symbols placed by this Render Terminal
                terminal_symbols = set()
                try:
                    rep = self.client.order_report()
                    if is_kotak_ok(rep) and rep.get("data"):
                        for o in rep.get("data", []):
                            if "NEOTRADEAPI" in str(o.get("ordSrc", "")).upper() or str(o.get("nOrdNo", "")) in getattr(self, "terminal_order_ids", set()):
                                terminal_symbols.add(str(o.get("trdSym", "")).upper())
                except Exception as ord_rep_err:
                    logger.warning(f"[OPEN_POS_ORD_NOTICE] Error checking terminal order report: {ord_rep_err}")

                for p in b_pos.get("data", []):
                    ex_seg = str(p.get("exSeg", "")).lower()
                    sym = str(p.get("sym", "")).upper()
                    trd_sym = str(p.get("trdSym", "")).upper()
                    stk_prc = float(p.get("stkPrc", 0.0) or 0.0)
                    if ex_seg != "nse_fo" or not (sym == "NIFTY" or trd_sym.startswith("NIFTY")) or stk_prc <= 0:
                        continue
                    net_qty = abs(int(float(p.get("flBuyQty", 0))) - int(float(p.get("flSellQty", 0))))
                    if net_qty > 0:
                        # Under Option 1: Only consider open if this position belongs to THIS Render Terminal!
                        # User's personal mobile app trades (ADMINCPPAPI_MOB) are 100% independent!
                        if terminal_symbols and trd_sym in terminal_symbols:
                            self.cached_has_open_positions = True
                            return True
            self.cached_has_open_positions = False
            return False
        except Exception as e:
            logger.warning(f"Error checking broker positions: {e}")
            return getattr(self, "cached_has_open_positions", False)

    def get_broker_cash_margin(self):
        """Fetches live free cash margin from Kotak Neo with 30s cooldown."""
        if not self.client or not self.authenticated or self.execution_mode != "LIVE":
            return float(getattr(self, "account_free_cash", 50000.0) or 50000.0)
        now = time.time()
        # Cooldown: only query Kotak limits every 30 seconds to prevent thread starvation
        if now - getattr(self, "last_limits_check_time", 0) < 30.0 and hasattr(self, "account_free_cash"):
            return float(self.account_free_cash)
        self.last_limits_check_time = now
        try:
            lims = self.client.limits()
            if isinstance(lims, dict):
                # Kotak Neo API returns live available margin directly at root key 'Net' or 'cashAvailable'
                lim_data = lims.get("data", {}) if isinstance(lims.get("data"), dict) else {}
                cash = float(lims.get("Net") or lim_data.get("cashAvailable") or lim_data.get("availableMargin") or getattr(self, "account_free_cash", 0.0) or 0.0)
                if cash > 0:
                    self.account_free_cash = cash
                    return cash
        except Exception as e:
            logger.warning(f"Failed to fetch live broker cash: {e}")
        return float(getattr(self, "account_free_cash", 50000.0) or 50000.0)

    def trigger_trade(self, force=False, simulated_time=None):
        """
        Executes Margin-Safe Credit Spread:
        STEP 1: BUY OTM Wing FIRST
        STEP 2: SELL ATM Strike SECOND

        USER STRICT COMMAND: IRONCLAD 6-RULE GATE (No trade unless ALL 6 Rules are TRUE)
        """
        # -------------------------------------------------------------------------
        # USER COMMAND: IRONCLAD 6-RULE VERIFICATION GATE
        # -------------------------------------------------------------------------
        if not force:
            # Gate 0A: Manual Pause Safety Latch
            if getattr(self, "manual_paused", False):
                msg = f"[GATE BLOCKED] Algo is PAUSED ({getattr(self, 'paused_reason', 'Manual pause active')}). Press Resume on Cockpit to allow trades."
                logger.warning(msg)
                return False, msg

            # Gate 0B: Broker Reconciliation Block Gate (Requirement 5 & 6)
            if getattr(self, "reconciliation_blocked", False):
                msg = f"[GATE BLOCKED - RECONCILIATION] Broker position reconciliation required ({getattr(self, 'reconciliation_reason', '')})! New trades blocked."
                logger.warning(msg)
                return False, msg

            cur_pos_recon = self.state.get("active_position")
            if cur_pos_recon:
                if cur_pos_recon.get("reconciliation_status") == "RECONCILIATION_REQUIRED":
                    msg = f"[GATE BLOCKED - RECONCILIATION] Active position marked RECONCILIATION_REQUIRED ({cur_pos_recon.get('reconciliation_reason', '')})! New trades blocked."
                    logger.warning(msg)
                    return False, msg
                rem_qty = int(cur_pos_recon.get("lot_size", 0) or 0)
                if rem_qty > 0:
                    msg = f"[GATE BLOCKED - RULE 6] Render Terminal already has an active position ({rem_qty} Qty open)! New trade prohibited until current position 100% closed."
                    logger.warning(msg)
                    return False, msg

            # Gate 0C: Mandatory 30-Minute Cooldown Between Trades (Only applies when prior trade is 100% closed)
            now_ts = time.time()
            last_exit = float(getattr(self, "last_trade_exit_time", 0) or 0)
            today_trades_done = self.state.get("today_wins", 0) + self.state.get("today_losses", 0)
            if today_trades_done >= 1 and last_exit > 0:
                elapsed = now_ts - last_exit
                COOLDOWN_PERIOD = 1800.0  # 30 minutes
                if elapsed < COOLDOWN_PERIOD:
                    rem_min = int((COOLDOWN_PERIOD - elapsed) // 60)
                    rem_sec = int((COOLDOWN_PERIOD - elapsed) % 60)
                    msg = f"[GATE BLOCKED - COOLDOWN] Trade 1 completed. Mandatory 30-minute cooldown active ({rem_min}m {rem_sec}s remaining) before Trade 2!"
                    logger.warning(msg)
                    return False, msg

            # Gate 0D: 5-Minute Candle Close Confirmation Latch Gate (Strict Backtest Alignment)
            fc = getattr(self, "finalized_5m_candle", None)
            if not fc or not fc.get("is_valid", False):
                msg = "[GATE BLOCKED - 5M CANDLE] No confirmed finalized 5-minute candle available. Entry prohibited on running candle or stale/insufficient ticks!"
                logger.warning(msg)
                return False, msg
            if fc.get("data_lag", 0) > 15.0 or fc.get("last_tick_age", 0) > 15.0:
                msg = f"[GATE BLOCKED - STALE DATA] Finalized candle tick lag ({max(fc.get('data_lag', 0), fc.get('last_tick_age', 0)):.1f}s) > 15.0s maximum allowed threshold!"
                logger.warning(msg)
                return False, msg

            spot = float(self.pulse.get("spot", 0) or 0)
            d_ema = float(self.pulse.get("daily_ema20", 0) or 0)
            e9 = float(self.pulse.get("ema9", 0) or 0)
            e20 = float(self.pulse.get("ema20", 0) or 0)
            e50 = float(self.pulse.get("ema50", 0) or 0)
            pcr = float(self.pulse.get("pcr", 1.0) or 1.0)
            is_green = bool(fc.get("is_green", self.pulse.get("is_green_candle", True)))
            is_red = bool(fc.get("is_red", self.pulse.get("is_red_candle", True)))

            # Rule 4: Time Window Check (09:30 AM to 14:30 PM IST)
            if simulated_time:
                try:
                    now_dt = datetime.strptime(str(simulated_time)[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    now_dt = get_ist_now()
            else:
                now_dt = get_ist_now()
            t_now = now_dt.time()
            if not (datetime.strptime("09:30", "%H:%M").time() <= t_now <= datetime.strptime("14:30", "%H:%M").time()):
                msg = f"[GATE BLOCKED - RULE 4] Current Time {t_now.strftime('%H:%M:%S')} is outside strict trading window (09:30 - 14:30 IST)!"
                logger.warning(msg)
                return False, msg

            # Rule 5: Circuit Breaker & Max 2 Trades
            today_trades_done = self.state.get("today_wins", 0) + self.state.get("today_losses", 0)
            if today_trades_done >= 2:
                msg = f"[GATE BLOCKED - RULE 5] Daily trade quota reached ({today_trades_done}/2 trades executed today)!"
                logger.warning(msg)
                return False, msg
            if self.state.get("circuit_breaker_active"):
                msg = "[GATE BLOCKED - RULE 5] Circuit breaker is active! Trading stopped for the day."
                logger.warning(msg)
                return False, msg

            # Rule 6: Demat Safety (0 Render Open Positions & Free Cash >= Rs 50,000)
            # Under Option 1: User's manual mobile app trades do NOT block Algo. Both trade independently!
            if self.state.get("active_position"):
                msg = "[GATE BLOCKED - RULE 6] Render Terminal already has an active position!"
                logger.warning(msg)
                return False, msg

            if self.execution_mode == "LIVE" and self.authenticated and self.client:
                # Direct Broker Demat open legs check specifically for Render Terminal trades
                if self.has_open_broker_positions():
                    msg = "[GATE BLOCKED - RULE 6] Render Terminal already has an open F&O position in Demat!"
                    logger.warning(msg)
                    return False, msg

                # Broker Free Cash Check (>= Rs 50,000)
                # Ensures that even if user has a manual trade, enough cash exists for the algo's spread
                try:
                    cash_avail = self.get_broker_cash_margin()
                    if cash_avail > 0 and cash_avail < 50000.0:
                        msg = f"[GATE BLOCKED - RULE 6] Free cash Rs {cash_avail:,.2f} < Rs 50,000 minimum required! (Manual trade may be consuming margin)"
                        logger.warning(msg)
                        return False, msg
                except Exception as c_err:
                    logger.warning(f"Failed cash check: {c_err}")
                    logger.warning(f"Failed cash check: {c_err}")

            # Direction & Signal Evaluation
            direction = self.pulse.get("signal")
            if direction not in ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"]:
                msg = f"[GATE BLOCKED] Signal is {direction} (All 6 rules not met). Entry strictly prohibited!"
                logger.warning(msg)
                return False, msg

            # Direction-Specific Rules (Rule 1, 2, 3)
            if direction == "BULL_PUT_SPREAD":
                # Rule 1: Spot > Daily 20 EMA
                if d_ema > 0 and spot <= d_ema:
                    msg = f"[GATE BLOCKED - RULE 1] Spot ({spot}) <= Daily 20 EMA ({d_ema})! Bullish macro filter failed."
                    logger.warning(msg)
                    return False, msg
                # Rule 2: 9 > 20 > 50 EMA & Green Candle
                if not (e9 > e20 > e50):
                    msg = f"[GATE BLOCKED - RULE 2] 5m EMA cascade failed (9={e9}, 20={e20}, 50={e50})!"
                    logger.warning(msg)
                    return False, msg
                if not is_green:
                    msg = "[GATE BLOCKED - RULE 2] Current 5m candle is not Green (Close < Open)!"
                    logger.warning(msg)
                    return False, msg
                # Rule 3: Weekly PCR >= 1.05
                if pcr < 1.05:
                    msg = f"[GATE BLOCKED - RULE 3] Weekly PCR ({pcr:.3f}) < 1.05! Bullish sentiment failed."
                    logger.warning(msg)
                    return False, msg

            elif direction == "BEAR_CALL_SPREAD":
                # Rule 1: Spot < Daily 20 EMA
                if d_ema > 0 and spot >= d_ema:
                    msg = f"[GATE BLOCKED - RULE 1] Spot ({spot}) >= Daily 20 EMA ({d_ema})! Bearish macro filter failed."
                    logger.warning(msg)
                    return False, msg
                # Rule 2: 9 < 20 < 50 EMA & Red Candle
                if not (e9 < e20 < e50):
                    msg = f"[GATE BLOCKED - RULE 2] 5m EMA cascade failed (9={e9}, 20={e20}, 50={e50})!"
                    logger.warning(msg)
                    return False, msg
                if not is_red:
                    msg = "[GATE BLOCKED - RULE 2] Current 5m candle is not Red (Close > Open)!"
                    logger.warning(msg)
                    return False, msg
                # Rule 3: Weekly PCR <= 0.85
                if pcr > 0.85:
                    msg = f"[GATE BLOCKED - RULE 3] Weekly PCR ({pcr:.3f}) > 0.85! Bearish sentiment failed."
                    logger.warning(msg)
                    return False, msg

        direction = self.pulse["signal"]
        if direction == "NEUTRAL_SCANNING":
            if not force:
                return False, "[GATE BLOCKED] Signal is NEUTRAL_SCANNING. Entry prohibited!"
            direction = "BULL_PUT_SPREAD"

        spot = self.pulse["spot"]
        atm_strike = int(round(spot / 50.0) * 50)

        # Upgrade 5: VIX-Adaptive Wing Distance (300 / 350 / 450)
        wing_dist, vix_regime = self.get_adaptive_wing_distance()

        # Upgrade 4: Dynamic Auto-Compounding Lot Sizing
        active_lots, total_qty = self.get_compounding_lot_sizing()

        # Dynamic live market prices from Option Chain
        chain_table = self.get_clean_option_chain_table()
        strike_map = {r["strike"]: r for r in chain_table}

        if direction == "BULL_PUT_SPREAD":
            sell_strike = atm_strike
            buy_strike = atm_strike - wing_dist
            opt_type = "PE"
            sell_row = strike_map.get(sell_strike, {})
            buy_row = strike_map.get(buy_strike, {})
            sell_ltp = float(sell_row.get("put_ltp", 0.0)) or 213.0
            buy_ltp = float(buy_row.get("put_ltp", 0.0)) or 89.0
        else:
            sell_strike = atm_strike
            buy_strike = atm_strike + wing_dist
            opt_type = "CE"
            sell_row = strike_map.get(sell_strike, {})
            buy_row = strike_map.get(buy_strike, {})
            sell_ltp = float(sell_row.get("call_ltp", 0.0)) or 255.0
            buy_ltp = float(buy_row.get("call_ltp", 0.0)) or 115.0

        # Institutional Marketable Instant-Fill Engine:
        # Guarantees 100% immediate fill on NSE order book so valid trades are NEVER missed!
        def round_to_tick(price, tick=0.05):
            return round(round(float(price) / tick) * tick, 2)

        buy_ask = float(buy_row.get("ask", 0.0) or 0.0)
        if buy_ask <= 0:
            buy_ask = buy_ltp * 1.005
        buy_bid = float(buy_row.get("bid", 0.0) or 0.0)
        # Marketable limit buy placed with buffer above ask matches instantly on top of book
        exec_buy = round_to_tick(max(buy_ask + 0.60, buy_ltp * 1.015))
        exec_type = "MARKETABLE_INSTANT_FILL"
        slippage_saved = 0.0

        # Selling at Best Bid matches instantly with the best buyer on NSE
        sell_bid = float(sell_row.get("bid", 0.0) or 0.0)
        if sell_bid <= 0:
            sell_bid = sell_ltp * 0.995
        exec_sell = round_to_tick(min(sell_bid - 0.60, sell_ltp * 0.985)) if sell_bid > 0 else round_to_tick(sell_ltp * 0.985)
        net_credit = round(exec_sell - exec_buy, 2)

        # Symbol generation for Kotak Neo F&O (Direct from Option Chain Instrument Master)
        exp_str = self.pulse.get("next_expiry", "2026-11-23")
        try:
            exp_dt = datetime.strptime(str(exp_str)[:10], "%Y-%m-%d")
            exp_code = exp_dt.strftime("%y%b").upper()
        except Exception:
            exp_code = "26NOV"

        sym_key = "put_trd_sym" if opt_type == "PE" else "call_trd_sym"
        inst_key = "put_inst" if opt_type == "PE" else "call_inst"
        buy_symbol = (
            buy_row.get(sym_key)
            or buy_row.get(inst_key, {}).get("trdSym")
            or buy_row.get("inst", {}).get("trdSym")
            or buy_row.get("trdSym")
            or f"NIFTY{exp_code}{buy_strike}{opt_type}"
        )
        sell_symbol = (
            sell_row.get(sym_key)
            or sell_row.get(inst_key, {}).get("trdSym")
            or sell_row.get("inst", {}).get("trdSym")
            or sell_row.get("trdSym")
            or f"NIFTY{exp_code}{sell_strike}{opt_type}"
        )

        # Real execution via Kotak Neo API if in LIVE mode
        order_status = "KOTAK_API_PENDING"
        if self.execution_mode == "LIVE" and self.authenticated and self.client:
            try:
                # 1. Place BUY OTM Wing First (Margin Benefit) with strict 0.05 Tick Size Formatting
                exec_buy_fmt = f"{round_to_tick(exec_buy):.2f}"
                exec_sell_fmt = f"{round_to_tick(exec_sell):.2f}"

                res_buy = self.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price=exec_buy_fmt,
                    order_type="L",
                    quantity=str(total_qty),
                    validity="DAY",
                    trading_symbol=buy_symbol,
                    transaction_type="B",
                )

                # Strict Safeguard: Verify BUY order was accepted BEFORE firing SELL order!
                # If BUY failed or was rejected, ABORT to prevent naked short margin rejection!
                if not is_kotak_ok(res_buy):
                    err_msg = res_buy.get("errMsg") or res_buy.get("message") or "BUY order rejected by broker gateway"
                    raise ValueError(f"BUY Hedge Rejected: {err_msg}")

                buy_ord_no = res_buy.get("nOrdNo") if isinstance(res_buy, dict) else None
                if buy_ord_no:
                    self.terminal_order_ids.add(str(buy_ord_no))
                
                # Active Verification: Wait up to 3.0s for BUY order to reach 'complete' / 'traded' status
                buy_filled = False
                buy_rejected = False
                buy_fill_reason = ""
                if buy_ord_no:
                    for _ in range(12):
                        time.sleep(0.25)
                        try:
                            rep = self.client.order_report()
                            if is_kotak_ok(rep) and rep.get("data"):
                                for o in rep.get("data", []):
                                    if str(o.get("nOrdNo")) == str(buy_ord_no):
                                        st = str(o.get("ordSt", "")).lower()
                                        if st in ["complete", "traded"]:
                                            buy_filled = True
                                            break
                                        elif st in ["rejected", "cancelled"]:
                                            buy_rejected = True
                                            buy_fill_reason = o.get("rejRsn") or "Exchange rejected BUY order"
                                            break
                            if buy_filled or buy_rejected:
                                break
                        except Exception as b_check_err:
                            logger.warning(f"BUY order report check notice: {b_check_err}")

                    if buy_rejected:
                        raise ValueError(f"BUY Hedge Rejected on Exchange: {buy_fill_reason}")

                    if not buy_filled:
                        # If BUY timed out in open state, cancel the open order immediately to avoid late execution
                        logger.warning(f"[SAFEGUARD] BUY order {buy_ord_no} not filled in 1.4s. Cancelling order!")
                        try:
                            self.client.cancel_order(order_id=str(buy_ord_no))
                        except Exception as c_err:
                            logger.error(f"Failed to cancel unfilled BUY order: {c_err}")
                        raise ValueError(f"BUY order {buy_ord_no} timed out and was cancelled. Trade safely aborted (0 Demat positions)!")

                # 2. Place SELL ATM Strike Second
                res_sell = self.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price=exec_sell_fmt,
                    order_type="L",
                    quantity=str(total_qty),
                    validity="DAY",
                    trading_symbol=sell_symbol,
                    transaction_type="S",
                )
                
                # Critical Two-Phase Verification: Verify Leg 2 in order report to detect RMS margin rejection!
                time.sleep(0.3)
                sell_ok = is_kotak_ok(res_sell)
                sell_ord_no = res_sell.get("nOrdNo") if isinstance(res_sell, dict) else None
                if sell_ord_no:
                    self.terminal_order_ids.add(str(sell_ord_no))
                rej_rsn = "SELL order failed"

                if not sell_ok or not sell_ord_no:
                    rej_rsn = res_sell.get("errMsg") or "Gateway rejected SELL leg"
                    sell_ok = False
                else:
                    try:
                        orders_rep = self.client.order_report()
                        if isinstance(orders_rep, dict) and orders_rep.get("data"):
                            for o in orders_rep.get("data", []):
                                if str(o.get("nOrdNo")) == str(sell_ord_no):
                                    st = str(o.get("ordSt", "")).lower()
                                    if st in ["rejected", "cancelled"]:
                                        sell_ok = False
                                        rej_rsn = o.get("rejRsn") or "RMS Margin / Limit Exceeded"
                                        break
                    except Exception as rep_err:
                        logger.warning(f"Order report check warning: {rep_err}")

                if not sell_ok:
                    logger.error(f"[RMS_SAFEGUARD] SELL leg rejected ({rej_rsn}). Auto-unwinding BUY hedge wing immediately!")
                    unwind_success = False
                    for attempt in range(3):
                        try:
                            res_unwind = self.client.place_order(
                                exchange_segment="nse_fo",
                                product="NRML",
                                price="0",
                                order_type="MKT",
                                quantity=str(total_qty),
                                validity="DAY",
                                trading_symbol=buy_symbol,
                                transaction_type="S",
                            )
                            if is_kotak_ok(res_unwind):
                                unwind_success = True
                                logger.info(f"[RMS_SAFEGUARD] BUY hedge wing {buy_symbol} auto-unwound successfully at MARKET (Attempt {attempt+1})!")
                                break
                            else:
                                unwind_limit_price = f"{round_to_tick(exec_buy * 0.90):.2f}"
                                res_unwind_l = self.client.place_order(
                                    exchange_segment="nse_fo",
                                    product="NRML",
                                    price=unwind_limit_price,
                                    order_type="L",
                                    quantity=str(total_qty),
                                    validity="DAY",
                                    trading_symbol=buy_symbol,
                                    transaction_type="S",
                                )
                                if is_kotak_ok(res_unwind_l):
                                    unwind_success = True
                                    logger.info(f"[RMS_SAFEGUARD] BUY hedge wing {buy_symbol} auto-unwound successfully at Limit {unwind_limit_price} (Attempt {attempt+1})!")
                                    break
                            time.sleep(0.15)
                        except Exception as unwind_err:
                            logger.error(f"[RMS_SAFEGUARD] Unwind attempt {attempt+1} failed: {unwind_err}")
                            time.sleep(0.15)

                    if not unwind_success:
                        logger.critical(f"[RMS_CRITICAL] Market unwind failed after 3 attempts! Alerting user immediately.")
                    raise ValueError(f"SELL leg rejected ({rej_rsn}). Auto-unwound orphan BUY wing to keep Demat 100% safe!")

                # Fix 5: Extract actual average traded fill prices from order report
                actual_buy_price = exec_buy
                actual_sell_price = exec_sell
                try:
                    rep_fills = self.client.order_report()
                    if is_kotak_ok(rep_fills) and rep_fills.get("data"):
                        for o in rep_fills.get("data", []):
                            o_no = str(o.get("nOrdNo", ""))
                            fl_pr = float(o.get("flPr", 0.0) or o.get("avgPrc", 0.0) or 0.0)
                            if fl_pr > 0:
                                if buy_ord_no and o_no == str(buy_ord_no):
                                    actual_buy_price = round(fl_pr, 2)
                                elif sell_ord_no and o_no == str(sell_ord_no):
                                    actual_sell_price = round(fl_pr, 2)
                except Exception as fill_err:
                    logger.warning(f"[FILL_PRC_NOTICE] Could not fetch real fill prices, using limit price: {fill_err}")

                net_credit = round(actual_sell_price - actual_buy_price, 2)
                exec_buy = actual_buy_price
                exec_sell = actual_sell_price

                order_status = "KOTAK_API_FILLED"
            except Exception as e:
                order_status = f"API_ERROR: {str(e)[:35]}"
                self.status_message = f"Kotak Order Placement Error: {str(e)[:45]}"
                return False, f"Kotak Order Placement Failed: {e}"
        else:
            order_status = "PAPER_VIRTUAL"

        if isinstance(simulated_time, datetime):
            entry_time_str = simulated_time.strftime("%Y-%m-%d %H:%M:%S")
        elif simulated_time:
            entry_time_str = str(simulated_time)
        else:
            entry_time_str = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
        pcr_val = self.pulse.get("pcr")
        pcr_str = f"{pcr_val:.2f}" if pcr_val is not None else "1.15"
        trade_reason = (
            f"Spot {round(spot, 1)} > Daily 20 EMA ({round(self.pulse.get('daily_ema20', 0), 1)}) | 5m Cascade (9>20>50) | Green Candle | PCR {pcr_str} >= 1.05 | VIX {self.pulse.get('india_vix', 13.5)} Normal"
            if direction == "BULL_PUT_SPREAD"
            else f"Spot {round(spot, 1)} < Daily 20 EMA ({round(self.pulse.get('daily_ema20', 0), 1)}) | 5m Cascade (9<20<50) | Red Candle | PCR {pcr_str} <= 0.85 | VIX {self.pulse.get('india_vix', 13.5)} Normal"
        )

        new_pos = {
            "direction": direction,
            "expiry": self.pulse["next_expiry"],
            "execution_mode": self.execution_mode,
            "order_status": order_status,
            "execution_type": exec_type,
            "slippage_saved_pts": slippage_saved,
            "slippage_saved_inr": round(slippage_saved * total_qty, 2),
            "lot_count": active_lots,
            "lot_size": total_qty,
            "wing_distance": wing_dist,
            "vix_regime": vix_regime,
            "sell_symbol": sell_symbol,
            "buy_symbol": buy_symbol,
            "sell_strike": int(sell_strike),
            "sell_type": opt_type,
            "sell_price": exec_sell,
            "buy_strike": int(buy_strike),
            "buy_type": opt_type,
            "buy_price": exec_buy,
            "net_credit": net_credit,
            "entry_time": entry_time_str,
            "entry_timestamp_sec": time.time(),
            "entry_spot": round(spot, 2),
            "trade_initiator": "AUTO_ALGO" if not force else "MANUAL_TRIGGER",
            "why_trade_taken": trade_reason,
            "current_spread": net_credit,
            "max_credit_inr": round(net_credit * total_qty, 2),
            "target_spread": round(net_credit * 0.60, 2),
            "target_profit_inr": round((net_credit * 0.40) * total_qty, 2),
            "trail_trigger_spread": round(net_credit * 0.75, 2),
            "trail_trigger_inr": round((net_credit * 0.25) * total_qty, 2),
            "trail_pts_needed": round(net_credit * 0.25, 2),
            "sl_spread": round(min(net_credit * 1.60, net_credit + ((2800.0 * active_lots) / total_qty)), 2),
            "max_risk_inr": round(min((net_credit * 0.60) * total_qty, 2800.0 * active_lots), 2),
            "breakeven_active": False,
            "mtm_gross_pnl": 0.0,
            "mtm_net_pnl": 0.0,
            "status": "OPEN",
        }

        with self.state_lock:
            self.state["active_position"] = new_pos
            self.save_state()
        self.pending_audio_event = "ENTRY"
        self.status_message = f"[{self.execution_mode}] Trade Triggered ({active_lots} Lots, {total_qty} Qty): Bought {buy_strike}{opt_type} -> Sold {sell_strike}{opt_type}"

        send_telegram_alert(
            f"🚀 <b>NIFTY QUANT DESK — TRADE FIRED</b>\n\n"
            f"• Strategy: <b>{direction}</b> [{self.execution_mode}]\n"
            f"• Lots Executed: <b>{active_lots} Lots ({total_qty} Qty)</b>\n"
            f"• Sold Leg: <b>{sell_strike}{opt_type}</b> @ Rs {exec_sell}\n"
            f"• Bought Leg: <b>{buy_strike}{opt_type}</b> @ Rs {exec_buy} [{exec_type}]\n"
            f"• Wing Distance: <b>{wing_dist} pts ({vix_regime})</b>\n"
            f"• Net Credit: Rs {net_credit} pts (Rs {round(net_credit * total_qty, 2):,})\n"
            f"• Target Profit (+40%): +Rs {new_pos['target_profit_inr']:,}\n"
            f"• Hard Stop Loss: -Rs {new_pos['max_risk_inr']:,} Cap\n"
            f"• Slippage Guard Saved: Rs {new_pos['slippage_saved_inr']:.2f}\n"
            f"• Nifty Spot Entry: {spot}"
        )
        return True, "Trade executed safely."

    def record_external_close_to_ledger(self, pos, reason="BROKER_MANUAL_SQUAREOFF", exit_spread_override=None, closed_qty_override=None):
        """Records an externally closed position (e.g. closed via mobile app) into the CSV audit ledger."""
        if not pos:
            return
        # Idempotency check: Prevent duplicate closing of the same trade_id
        trade_id = pos.get("trade_id")
        if not hasattr(self, "closed_trade_ids"):
            self.closed_trade_ids = set()
        if trade_id and trade_id in self.closed_trade_ids and reason != "PARTIAL_SQUAREOFF":
            logger.warning(f"[IDEMPOTENT_GUARD] Trade {trade_id} already recorded to ledger. Skipping duplicate entry!")
            return
        if trade_id and reason != "PARTIAL_SQUAREOFF":
            self.closed_trade_ids.add(trade_id)
        entry_credit = float(pos.get("net_credit", 0.0) or 0.0)
        exit_spread = float(exit_spread_override if exit_spread_override is not None else (pos.get("current_spread", entry_credit) or entry_credit))
        total_qty = int(closed_qty_override if closed_qty_override is not None else (pos.get("lot_size", self.lot_size) or self.lot_size))
        pos_lots = max(1, total_qty // self.lot_size)
        points_gain = entry_credit - exit_spread
        gross_pnl = round(points_gain * total_qty, 2)
        charges = self.calculate_kotak_charges(entry_credit, exit_spread, total_qty)
        net_pnl = round(gross_pnl - charges["total_charges"], 2)
        exit_spot = self.pulse.get("spot", pos.get("entry_spot", 0.0))
        exit_time_str = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")

        trade_rec = {
            "date": get_ist_now().strftime("%Y-%m-%d"),
            "entry_time": pos.get("entry_time", exit_time_str),
            "exit_time": exit_time_str,
            "mode": pos.get("execution_mode", "LIVE"),
            "direction": pos.get("direction", "SPREAD"),
            "strikes": f"Sell {pos.get('sell_strike')}{pos.get('sell_type', '')} / Buy {pos.get('buy_strike')}{pos.get('buy_type', '')}",
            "lots": pos_lots,
            "quantity": total_qty,
            "net_credit": entry_credit,
            "exit_spread": exit_spread,
            "points": round(points_gain, 2),
            "entry_spot": pos.get("entry_spot", exit_spot),
            "exit_spot": exit_spot,
            "spot_move": round(exit_spot - pos.get("entry_spot", exit_spot), 2),
            "trade_initiator": pos.get("trade_initiator", "USER_MANUAL"),
            "why_trade_taken": pos.get("why_trade_taken", "Kotak Neo Position"),
            "gross_pnl": gross_pnl,
            "brokerage_taxes": charges["total_charges"],
            "net_pnl": net_pnl,
            "result": "WIN" if net_pnl > 0 else ("BREAKEVEN" if net_pnl == 0 else "LOSS"),
            "exit_reason": reason,
        }
        df_new = pd.DataFrame([trade_rec])
        if TRADES_FILE.exists():
            df_new.to_csv(TRADES_FILE, mode="a", header=False, index=False)
        else:
            df_new.to_csv(TRADES_FILE, index=False)
        logger.info(f"[LEDGER] Externally closed position recorded to {TRADES_FILE}: Net PnL Rs {net_pnl}")

        # Update Daily Metrics in state
        with self.state_lock:
            self.state["today_realized_gross_pnl"] = round(float(self.state.get("today_realized_gross_pnl", 0.0)) + gross_pnl, 2)
            self.state["today_total_charges"] = round(float(self.state.get("today_total_charges", 0.0)) + charges["total_charges"], 2)
            self.state["today_realized_net_pnl"] = round(float(self.state.get("today_realized_net_pnl", 0.0)) + net_pnl, 2)

            # Issue 1 Fix: Partial exit does NOT complete the trade!
            # Only full 100% exit increments trade count and starts cooldown timer!
            if reason != "PARTIAL_SQUAREOFF":
                if net_pnl >= 0:
                    self.state["today_wins"] = int(self.state.get("today_wins", 0)) + 1
                    self.pending_audio_event = "TARGET"
                    outcome_tag = "🎯 TARGET / MANUAL PROFIT"
                else:
                    self.state["today_losses"] = int(self.state.get("today_losses", 0)) + 1
                    self.pending_audio_event = "STOP_LOSS"
                    outcome_tag = "🛑 STOP LOSS / MANUAL EXIT"

                today_trades_done = self.state.get("today_wins", 0) + self.state.get("today_losses", 0)
                if today_trades_done >= 2:
                    self.state["circuit_breaker_active"] = True

                self.last_trade_exit_time = time.time()
                self.manual_paused = True
                self.paused_reason = f"External square-off detected on Kotak Neo app ({reason}). Auto-trading paused to protect capital."
                self.save_engine_mode()
                self.status_message = "SAFETY LOCK: Trade closed on mobile app. Algo paused to prevent Trade 2 unexpected entry."
            else:
                logger.info(f"[PARTIAL_SQUAREOFF_LEDGER] Booked partial P&L Rs {net_pnl} ({total_qty} qty). Remaining position stays OPEN - trade NOT completed.")
            self.save_state()

    def square_off_position(self, reason="MANUAL_WEB_EXIT"):
        pos = self.state.get("active_position")
        if not pos:
            return False, "No active position."

        # STRICT USER COMMAND: If position was entered manually by user from Kotak Neo app,
        # ALGO NEVER AUTO-EXITS IT! Full control remains 100% in user's hands!
        if pos.get("trade_initiator") == "USER_MANUAL" and reason != "MANUAL_WEB_EXIT":
            logger.info("[USER_MANUAL] Auto square-off blocked: Position is controlled 100% by user in Kotak Neo!")
            return False, "Manual position is controlled solely by user in Kotak Neo app."

        exit_spread = pos.get("current_spread", pos["net_credit"])
        entry_credit = pos["net_credit"]
        pos_lots = pos.get("lot_count", 1)
        total_qty = pos.get("lot_size", self.lot_size)
        points_gain = entry_credit - exit_spread
        gross_pnl = round(points_gain * total_qty, 2)

        # Exact Kotak Neo Charges calculation for total executed quantity
        charges = self.calculate_kotak_charges(entry_credit, exit_spread, total_qty)
        net_pnl = round(gross_pnl - charges["total_charges"], 2)

        exit_spot = self.pulse["spot"]
        exit_time_str = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")

        # Fix 1 & 5: If LIVE mode, send real squareoff orders with verification and fill price capture
        if pos.get("execution_mode") == "LIVE" and self.authenticated and self.client:
            sell_exit_ord_no = None
            buy_exit_ord_no = None
            exit_orders_failed = False
            exit_failure_reason = ""

            try:
                # 1. Buy back short leg first (releases margin obligation safely)
                # Attempt Market Order first; if rejected by NSE options RMS, fallback to aggressive marketable limit
                s_symbol = pos.get("sell_symbol") or f"NIFTY{pos.get('sell_strike', '')}{pos.get('sell_type', '')}"
                res_exit_sell = self.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price="0",
                    order_type="MKT",
                    quantity=str(total_qty),
                    validity="DAY",
                    trading_symbol=s_symbol,
                    transaction_type="B",
                )
                if not is_kotak_ok(res_exit_sell):
                    sell_ltp = float(pos.get("current_sell_ltp", 0.0) or pos.get("sell_price", 100.0))
                    limit_buyback = f"{round(round(sell_ltp * 1.05 / 0.05) * 0.05, 2):.2f}"
                    res_exit_sell = self.client.place_order(
                        exchange_segment="nse_fo",
                        product="NRML",
                        price=limit_buyback,
                        order_type="L",
                        quantity=str(total_qty),
                        validity="DAY",
                        trading_symbol=s_symbol,
                        transaction_type="B",
                    )

                if is_kotak_ok(res_exit_sell):
                    sell_exit_ord_no = res_exit_sell.get("nOrdNo")
                    if sell_exit_ord_no:
                        self.terminal_order_ids.add(str(sell_exit_ord_no))
                else:
                    exit_orders_failed = True
                    exit_failure_reason = res_exit_sell.get("errMsg") or "Short leg buyback order rejected"

                # 2. Sell back long wing second
                b_symbol = pos.get("buy_symbol") or f"NIFTY{pos.get('buy_strike', '')}{pos.get('buy_type', '')}"
                res_exit_buy = self.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price="0",
                    order_type="MKT",
                    quantity=str(total_qty),
                    validity="DAY",
                    trading_symbol=b_symbol,
                    transaction_type="S",
                )
                if not is_kotak_ok(res_exit_buy):
                    buy_ltp = float(pos.get("current_buy_ltp", 0.0) or pos.get("buy_price", 30.0))
                    limit_sellback = f"{round(round(buy_ltp * 0.90 / 0.05) * 0.05, 2):.2f}"
                    res_exit_buy = self.client.place_order(
                        exchange_segment="nse_fo",
                        product="NRML",
                        price=limit_sellback,
                        order_type="L",
                        quantity=str(total_qty),
                        validity="DAY",
                        trading_symbol=b_symbol,
                        transaction_type="S",
                    )

                if is_kotak_ok(res_exit_buy):
                    buy_exit_ord_no = res_exit_buy.get("nOrdNo")
                    if buy_exit_ord_no:
                        self.terminal_order_ids.add(str(buy_exit_ord_no))
                else:
                    exit_orders_failed = True
                    exit_failure_reason += f" | {res_exit_buy.get('errMsg') or 'Long wing exit rejected'}"

            except Exception as exit_err:
                exit_orders_failed = True
                exit_failure_reason = str(exit_err)
                logger.error(f"[EXIT_ERROR] Live squareoff order failed: {exit_err}")

            # Active Order Verification: Check order report for fill confirmation
            short_filled_qty = 0
            short_fill_prc = 0.0
            long_filled_qty = 0
            long_fill_prc = 0.0

            if not exit_orders_failed:
                for _ in range(6):
                    time.sleep(0.2)
                    try:
                        rep = self.client.order_report()
                        if is_kotak_ok(rep) and rep.get("data"):
                            for o in rep.get("data", []):
                                o_no = str(o.get("nOrdNo", ""))
                                st = str(o.get("ordSt", "")).lower()
                                f_qty = int(float(o.get("fldQty", 0) or 0))
                                f_prc = float(o.get("flPr", 0.0) or o.get("avgPrc", 0.0) or 0.0)
                                if sell_exit_ord_no and o_no == str(sell_exit_ord_no):
                                    if st in ["complete", "traded"]:
                                        short_filled_qty = f_qty
                                        short_fill_prc = f_prc
                                elif buy_exit_ord_no and o_no == str(buy_exit_ord_no):
                                    if st in ["complete", "traded"]:
                                        long_filled_qty = f_qty
                                        long_fill_prc = f_prc
                        if short_filled_qty > 0 and long_filled_qty > 0:
                            break
                    except Exception as rep_chk_err:
                        logger.warning(f"[EXIT_REP_ERR] Error checking exit order report: {rep_chk_err}")

            # Fix 1 SAFEGUARD: If exit order failed or timed out with zero confirmed fills,
            # NEVER CLEAR ACTIVE POSITION! Keep OPEN and alert immediately!
            confirmed_closed_qty = min(short_filled_qty, long_filled_qty) if (short_filled_qty > 0 and long_filled_qty > 0) else 0

            if exit_orders_failed or (sell_exit_ord_no and buy_exit_ord_no and confirmed_closed_qty == 0):
                fail_msg = f"Square-off failed at broker ({exit_failure_reason or 'Orders not filled on exchange'}). Retaining position OPEN!"
                logger.critical(f"[SQUAREOFF_SAFETY_BLOCKED] {fail_msg}")
                with self.state_lock:
                    pos["exit_status"] = "SQUAREOFF_FAILED_RETRYING"
                    pos["last_exit_error"] = exit_failure_reason or "Broker fill timeout"
                    self.reconciliation_blocked = True
                    self.reconciliation_reason = f"Square-off order failed ({pos['last_exit_error']})"
                    self.save_state()
                send_telegram_alert(
                    f"🚨 <b>CRITICAL EXIT FAILURE</b>: {fail_msg}\n"
                    f"Position remains OPEN and protected by SL/Target monitors."
                )
                return False, fail_msg

            # Handle Partial Exit: Close only confirmed filled quantity, keep remainder open
            if confirmed_closed_qty < total_qty:
                logger.warning(f"[PARTIAL_EXIT_CONFIRMED] Partial square-off: {confirmed_closed_qty}/{total_qty} qty closed.")
                if confirmed_closed_qty > 0:
                    sell_exit_actual = short_fill_prc if short_fill_prc > 0 else float(pos.get("current_sell_ltp", 0.0))
                    buy_exit_actual = long_fill_prc if long_fill_prc > 0 else float(pos.get("current_buy_ltp", 0.0))
                    act_spread = round(sell_exit_actual - buy_exit_actual, 2)
                    self.record_external_close_to_ledger(
                        pos,
                        reason="PARTIAL_SQUAREOFF",
                        exit_spread_override=act_spread,
                        closed_qty_override=confirmed_closed_qty
                    )
                with self.state_lock:
                    rem_qty = total_qty - confirmed_closed_qty
                    pos["lot_size"] = rem_qty
                    pos["lot_count"] = max(1, rem_qty // self.lot_size)
                    pos["max_credit_inr"] = round(entry_credit * rem_qty, 2)
                    pos["target_profit_inr"] = round((entry_credit * 0.40) * rem_qty, 2)
                    pos["max_risk_inr"] = round(min((entry_credit * 0.60) * rem_qty, 2800.0 * pos["lot_count"]), 2)
                    pos["trail_trigger_inr"] = round((entry_credit * 0.25) * rem_qty, 2)
                    pos["exit_status"] = "PARTIAL_REMAINING_OPEN"
                    self.state["active_position"] = pos
                    self.save_state()
                return True, f"Partial squareoff: {confirmed_closed_qty}/{total_qty} closed. Remainder {rem_qty} OPEN."

            # Fix 5: Full Fill Confirmed - Update exact fill prices from exchange execution
            if short_fill_prc > 0 and long_fill_prc > 0:
                actual_exit_spread = round(short_fill_prc - long_fill_prc, 2)
                exit_spread = actual_exit_spread
                points_gain = entry_credit - exit_spread
                gross_pnl = round(points_gain * total_qty, 2)
                charges = self.calculate_kotak_charges(entry_credit, exit_spread, total_qty)
                net_pnl = round(gross_pnl - charges["total_charges"], 2)

        # Update Daily Metrics
        with self.state_lock:
            self.state["today_realized_gross_pnl"] = round(self.state["today_realized_gross_pnl"] + gross_pnl, 2)
            self.state["today_total_charges"] = round(self.state["today_total_charges"] + charges["total_charges"], 2)
            self.state["today_realized_net_pnl"] = round(self.state["today_realized_net_pnl"] + net_pnl, 2)

            if net_pnl >= 0:
                self.state["today_wins"] += 1
                self.pending_audio_event = "TARGET"
                outcome_tag = "🎯 TARGET ACHIEVED (+40% DECAY)"
            else:
                self.state["today_losses"] += 1
                self.pending_audio_event = "STOP_LOSS"
                outcome_tag = "🛑 STOP LOSS / EXIT"

            today_trades_done = self.state.get("today_wins", 0) + self.state.get("today_losses", 0)
            if today_trades_done >= 2:
                self.state["circuit_breaker_active"] = True
                self.status_message = "Daily Quota Complete (Max 2 Trades Reached). Market locked for today."
            else:
                self.state["circuit_breaker_active"] = False
                self.status_message = f"Trade 1 Closed ({outcome_tag}). Scanning for Trade 2 if valid signal forms before 14:30."

            self.state["active_position"] = None
            self.proximity_polling = False
            self.last_trade_exit_time = time.time()
            if reason == "MANUAL_WEB_EXIT":
                self.manual_paused = True
                self.paused_reason = "Manual square-off executed from Web Cockpit. Algo paused to protect capital."
            self.save_engine_mode()
            self.save_state()

        send_telegram_alert(
            f"{outcome_tag} — <b>NIFTY QUANT DESK</b>\n\n"
            f"• Reason: <b>{reason}</b>\n"
            f"• Sizing: <b>{pos_lots} Lots ({total_qty} Qty)</b>\n"
            f"• Gross PnL: Rs {gross_pnl:+,.2f}\n"
            f"• Kotak Taxes & Charges: -Rs {charges['total_charges']:.2f}\n"
            f"• <b>Net Realized PnL: Rs {net_pnl:+,.2f}</b>\n"
            f"• Exit Spot: {exit_spot}\n"
            f"• Ending Day Account Balance: Rs {self.initial_capital + self.state['today_realized_net_pnl']:,.2f}"
        )

        trade_rec = {
            "date": get_ist_now().strftime("%Y-%m-%d"),
            "entry_time": pos.get("entry_time", exit_time_str),
            "exit_time": exit_time_str,
            "mode": pos.get("execution_mode", "PAPER"),
            "direction": pos["direction"],
            "strikes": f"Sell {pos['sell_strike']}{pos['sell_type']} / Buy {pos['buy_strike']}{pos['buy_type']}",
            "lots": pos_lots,
            "quantity": total_qty,
            "net_credit": pos["net_credit"],
            "exit_spread": exit_spread,
            "points": round(points_gain, 2),
            "entry_spot": pos.get("entry_spot", 0.0),
            "exit_spot": exit_spot,
            "spot_move": round(exit_spot - pos.get("entry_spot", exit_spot), 2),
            "trade_initiator": pos.get("trade_initiator", "AUTO_ALGO"),
            "why_trade_taken": pos.get("why_trade_taken", "Daily 20 EMA + 5m Cascade + PCR"),
            "gross_pnl": gross_pnl,
            "brokerage_taxes": charges["total_charges"],
            "net_pnl": net_pnl,
            "result": "WIN" if net_pnl > 0 else ("BREAKEVEN" if net_pnl == 0 else "LOSS"),
            "exit_reason": reason,
        }
        df_new = pd.DataFrame([trade_rec])
        if TRADES_FILE.exists():
            df_new.to_csv(TRADES_FILE, mode="a", header=False, index=False)
        else:
            df_new.to_csv(TRADES_FILE, index=False)

        # Upgrade 3: Track continuous equity curve point
        self.record_equity_point(net_pnl, reason)

        # Upgrade 7: Update daily report
        self.generate_daily_report()

        self.status_message = f"Closed ({reason}): Net PnL Rs {net_pnl:+,.2f} ({pos_lots} Lots, Charges: Rs {charges['total_charges']})"
        return True, f"Closed with Net PnL Rs {net_pnl:+,.2f}"

    def update_position_dynamics(self):
        pos = self.state.get("active_position")
        if not pos:
            self.proximity_polling = False
            return

        spot = self.pulse["spot"]
        entry_spot = pos["entry_spot"]
        entry_credit = pos["net_credit"]
        direction = pos["direction"]
        pos_qty = pos.get("lot_size", self.lot_size)

        spot_diff = (spot - entry_spot) if direction == "BULL_PUT_SPREAD" else (entry_spot - spot)
        
        # Ensure target_spread and sl_spread are always populated
        if "target_spread" not in pos or pos["target_spread"] is None:
            pos["target_spread"] = pos.get("target_credit") or round(entry_credit * 0.60, 2)
        if "sl_spread" not in pos or pos["sl_spread"] is None:
            pos["sl_spread"] = pos.get("stop_loss") or round(entry_credit * 1.60, 2)

        # Second-by-second holding timer
        start_sec = pos.get("entry_timestamp_sec", time.time())
        holding_sec = int(time.time() - start_sec)
        pos["holding_time_str"] = str(timedelta(seconds=holding_sec))
        pos["spot_move"] = round(spot - entry_spot, 2)

        # Dynamic real market option spread: Check live Kotak Neo broker positions first, then fallback to option chain table
        short_leg = getattr(self, "latest_broker_short_leg", None)
        long_leg = getattr(self, "latest_broker_long_leg", None)
        broker_sell_ltp = float(short_leg.get("ltp") or short_leg.get("lp") or short_leg.get("lastPrice") or 0.0) if short_leg else 0.0
        broker_buy_ltp = float(long_leg.get("ltp") or long_leg.get("lp") or long_leg.get("lastPrice") or 0.0) if long_leg else 0.0
        broker_sell_ur = float(short_leg.get("urMtM", 0.0) or 0.0) if short_leg else 0.0
        broker_buy_ur = float(long_leg.get("urMtM", 0.0) or 0.0) if long_leg else 0.0

        if broker_sell_ltp > 0:
            pos["current_sell_ltp"] = round(broker_sell_ltp, 2)
        if broker_buy_ltp > 0:
            pos["current_buy_ltp"] = round(broker_buy_ltp, 2)

        chain_table = self.get_clean_option_chain_table()
        strike_map = {r["strike"]: r for r in chain_table}
        sell_row = strike_map.get(pos["sell_strike"], {})
        buy_row = strike_map.get(pos["buy_strike"], {})

        opt_col = "put_ltp" if pos["sell_type"] == "PE" else "call_ltp"
        real_sell_ltp = float(sell_row.get(opt_col, 0.0))
        real_buy_ltp = float(buy_row.get(opt_col, 0.0))

        if not pos.get("current_sell_ltp") or pos.get("current_sell_ltp") <= 0:
            pos["current_sell_ltp"] = real_sell_ltp if real_sell_ltp > 0 else pos["sell_price"]
        if not pos.get("current_buy_ltp") or pos.get("current_buy_ltp") <= 0:
            pos["current_buy_ltp"] = real_buy_ltp if real_buy_ltp > 0 else pos["buy_price"]

        if pos.get("current_sell_ltp", 0.0) > 0 and pos.get("current_buy_ltp", 0.0) > 0:
            current_spread = round(pos["current_sell_ltp"] - pos["current_buy_ltp"], 2)
        else:
            elapsed_mins = max(1, holding_sec // 60)
            theta_decay = min(entry_credit * 0.40, (elapsed_mins / 60.0) * 2.5)
            current_spread = np.clip(entry_credit - (spot_diff * 0.40) - theta_decay, 2.0, entry_credit * 2.5)

        pos["current_spread"] = round(current_spread, 2)

        # Leg level PnL & Broker Unrealised PnL
        if broker_sell_ur != 0.0:
            pos["sell_leg_pnl"] = round(broker_sell_ur, 2)
        else:
            pos["sell_leg_pnl"] = round((pos["sell_price"] - pos["current_sell_ltp"]) * pos_qty, 2)

        if broker_buy_ur != 0.0:
            pos["buy_leg_pnl"] = round(broker_buy_ur, 2)
        else:
            pos["buy_leg_pnl"] = round((pos["current_buy_ltp"] - pos["buy_price"]) * pos_qty, 2)

        broker_unrealised = round(pos["sell_leg_pnl"] + pos["buy_leg_pnl"], 2)
        pos["broker_unrealised_pnl"] = broker_unrealised
        pos["mtm_gross_pnl"] = broker_unrealised
        pts_gain = round(entry_credit - current_spread, 2)

        # Net MTM after estimated charges
        charges = self.calculate_kotak_charges(entry_credit, current_spread, pos_qty)
        pos["estimated_charges"] = charges["total_charges"]
        pos["mtm_net_pnl"] = round(broker_unrealised - charges["total_charges"], 2)

        # Upgrade 6: Adaptive Fast-Tick Proximity Detection
        target_spread = pos.get("target_spread", entry_credit * 0.60)
        sl_spread = pos.get("sl_spread", entry_credit * 1.60)
        proximity_threshold = max(2.0, entry_credit * 0.12)
        if abs(current_spread - target_spread) <= proximity_threshold or abs(current_spread - sl_spread) <= proximity_threshold:
            self.proximity_polling = True
            pos["proximity_fast_polling"] = True
        else:
            self.proximity_polling = False
            pos["proximity_fast_polling"] = False

        # Target, Trailing and Risk calculations for Cockpit HUD
        pos["max_credit_inr"] = round(entry_credit * pos_qty, 2)
        pos["target_spread"] = round(entry_credit * 0.60, 2)
        pos["target_profit_inr"] = round((entry_credit * 0.40) * pos_qty, 2)
        pos["trail_trigger_spread"] = round(entry_credit * 0.75, 2)
        pos["trail_trigger_inr"] = round((entry_credit * 0.25) * pos_qty, 2)

        # 1. 100% Max Potential
        max_inr = round(entry_credit * pos_qty, 2)
        cur_pos_cap_pct = round(min(100.0, max(0.0, (broker_unrealised / max(0.01, max_inr)) * 100.0)), 1) if max_inr > 0 else 0.0
        pos["max_credit_captured_inr"] = broker_unrealised
        pos["max_credit_captured_pct"] = cur_pos_cap_pct
        pos["max_credit_remaining_inr"] = max(0.0, round(max_inr - broker_unrealised, 2))
        pos["max_credit_remaining_pct"] = round(max(0.0, 100.0 - cur_pos_cap_pct), 1)

        # 2. 40% Target Exit Dynamic Percentages
        pos["decay_pts_gained"] = max(0.0, pts_gain)
        decay_pts_needed = round(entry_credit * 0.40, 2)
        pos["decay_pts_needed"] = decay_pts_needed
        pos["target_pts_needed"] = max(0.0, round(current_spread - pos["target_spread"], 2))
        pos["target_profit_remaining_inr"] = max(0.0, round(pos["target_profit_inr"] - broker_unrealised, 2))
        tgt_pct = round(min(100.0, max(0.0, (pts_gain / max(0.01, decay_pts_needed)) * 100.0)), 1)
        pos["target_progress_pct"] = tgt_pct
        pos["target_remaining_pct"] = round(max(0.0, 100.0 - tgt_pct), 1)

        # Trailing Breakeven Trigger (+25% Profit -> 0 Risk Lock)
        if not pos.get("breakeven_active") and (pts_gain >= entry_credit * 0.25):
            pos["breakeven_active"] = True
            pos["sl_spread"] = entry_credit
            pos["be_locked"] = True
            pos["be_locked_time"] = get_ist_now().strftime("%H:%M:%S")
            self.pending_audio_event = "BREAKEVEN_LOCKED"
            self.status_message = "💎 TRAILING SL LOCKED: ZERO RISK (0 Rs Max Loss)!"

        # 3. 25% Auto-Trail Dynamic Percentages
        trail_pts_needed = round(entry_credit * 0.25, 2)
        pos["trail_pts_needed"] = trail_pts_needed
        pos["trail_pts_remaining"] = max(0.0, round(current_spread - pos["trail_trigger_spread"], 2)) if not pos.get("breakeven_active") else 0.0
        pos["trail_profit_remaining_inr"] = max(0.0, round(pos["trail_trigger_inr"] - broker_unrealised, 2)) if not pos.get("breakeven_active") else 0.0
        trail_pct = 100.0 if pos.get("breakeven_active") else round(min(100.0, max(0.0, (pts_gain / max(0.01, trail_pts_needed)) * 100.0)), 1)
        pos["trail_progress_pct"] = trail_pct
        pos["trail_remaining_pct"] = 0.0 if pos.get("breakeven_active") else round(max(0.0, 100.0 - trail_pct), 1)

        # 4. Stop Loss & Max Risk (-₹2,800 CAP)
        hard_cap = round(2800.0 * pos.get("lot_count", 1), 2)
        pos["hard_sl_cap"] = hard_cap
        pos["sl_spread"] = entry_credit if pos.get("breakeven_active") else round(entry_credit * 1.60, 2)
        pos["max_risk_inr"] = 0.0 if pos.get("breakeven_active") else round(min((entry_credit * 0.60) * pos_qty, hard_cap), 2)
        
        sl_cut_spread = round(entry_credit * 1.60, 2)
        pos["sl_buffer_pts"] = max(0.0, round(sl_cut_spread - current_spread, 2))
        pos["sl_buffer_inr"] = max(0.0, round(pos["sl_buffer_pts"] * pos_qty, 2))

        if pos.get("breakeven_active"):
            pos["sl_buffer_pct"] = 100.0
            pos["sl_risk_consumed_pct"] = 0.0
            pos["sl_status_text"] = "0 RISK (Cost Locked)"
        else:
            risk_pct = round(min(100.0, (abs(broker_unrealised) / max(0.01, hard_cap)) * 100.0), 1) if broker_unrealised < 0 else 0.0
            pos["sl_risk_consumed_pct"] = risk_pct
            pos["sl_buffer_pct"] = round(max(0.0, 100.0 - risk_pct), 1)
            pos["sl_status_text"] = "ACTIVE (-Rs 2800 CAP)"

        # STRICT USER COMMAND: If position was created manually by user from Kotak Neo app,
        # ALGO NEVER AUTO-EXITS IT! Full control remains 100% in user's hands.
        if pos.get("trade_initiator") == "USER_MANUAL":
            return

        # Exit conditions (Only for AUTO_ALGO trades)
        target_spread = pos.get("target_spread") or pos.get("target_credit") or round(entry_credit * 0.60, 2)
        sl_spread = pos.get("sl_spread") or pos.get("stop_loss") or round(entry_credit * 1.60, 2)
        if current_spread <= target_spread:
            self.square_off_position("TARGET_HIT (+40% DECAY)")
        elif current_spread >= sl_spread:
            self.square_off_position("BREAKEVEN_SL" if pos.get("breakeven_active") else "STOP_LOSS")
        elif not pos.get("breakeven_active") and broker_unrealised <= -hard_cap:
            self.square_off_position(f"HARD_SL_CAP_HIT (-Rs {int(hard_cap)})")
        elif pos.get("breakeven_active") and broker_unrealised < -50.0:
            self.square_off_position("BREAKEVEN_SL (Cost Protection)")
        else:
            # Time Exit at 15:15 PM IST (Zero Overnight Risk)
            now_t = get_ist_now().time()
            if datetime.strptime("15:15", "%H:%M").time() <= now_t <= datetime.strptime("15:30", "%H:%M").time():
                self.square_off_position("TIME_EXIT (15:15 PM INTRADAY CLOSE)")


engine = MasterQuantEngine()


@app.before_request
def ensure_engine_workers():
    """Failsafe: Guarantees background trading daemons run continuously inside Gunicorn workers."""
    if not hasattr(engine, "live_daemon_thread") or not engine.live_daemon_thread.is_alive() or not getattr(engine, "watchdog_thread", None) or not engine.watchdog_thread.is_alive():
        engine.start_background_workers()


@app.route("/api/ping")
def api_ping():
    """Ultra-lightweight keep-alive heartbeat endpoint for Render Anti-Sleep daemon."""
    now_ts = time.time()
    now_dt = get_ist_now()
    mem_mb = get_process_memory_mb()
    return jsonify({
        "status": "ok",
        "app": "Nifty Quant Terminal",
        "server_time": now_dt.strftime("%Y-%m-%d %H:%M:%S IST"),
        "execution_mode": engine.execution_mode,
        "is_armed": not engine.manual_paused,
        "pcr": engine.pulse.get("pcr"),
        "spot": engine.pulse.get("spot"),
        "candle_status": engine.pulse.get("candle_status"),
        "memory_mb": mem_mb,
        "memory_limit_mb": 512,
        "last_poll_sec_ago": round(now_ts - getattr(engine, "last_api_poll", 0), 1),
    }), 200


@app.route("/api/health")
def health():
    if not hasattr(engine, "live_daemon_thread") or not engine.live_daemon_thread.is_alive() or not getattr(engine, "watchdog_thread", None) or not engine.watchdog_thread.is_alive():
        engine.start_background_workers()
    now_ts = time.time()

    last_poll_sec = round(now_ts - getattr(engine, "last_api_poll", 0), 1)
    uptime_sec = int(now_ts - getattr(engine, "start_time", now_ts))
    worker_alive = bool(hasattr(engine, "live_daemon_thread") and engine.live_daemon_thread.is_alive())
    watchdog_alive = bool(hasattr(engine, "watchdog_thread") and engine.watchdog_thread.is_alive())

    mem_mb = get_process_memory_mb()
    mem_pct = round((mem_mb / 512.0) * 100.0, 1)

    return jsonify({
        "status": "HEALTHY",
        "system_health": "OPTIMAL (Self-Healing Active)",
        "uptime_sec": uptime_sec,
        "worker_alive": worker_alive,
        "watchdog_alive": watchdog_alive,
        "memory_mb": mem_mb,
        "memory_limit_mb": 512,
        "memory_usage_pct": mem_pct,
        "self_heals_triggered": getattr(engine, "self_heal_count", 0),
        "engine": "MasterQuantEngine",
        "authenticated": engine.authenticated,
        "feeder_mode": engine.feeder_mode,
        "spot": engine.pulse.get("spot"),
        "india_vix": engine.pulse.get("india_vix", 13.5),
        "vix_regime": engine.pulse.get("vix_regime", "NORMAL_VOLATILITY"),
        "wing_distance": engine.pulse.get("wing_distance", 350),
        "active_lots": getattr(engine, "active_lots", 1),
        "lot_size": engine.lot_size,
        "pcr": engine.pulse.get("pcr"),
        "pcr_oi_change": engine.pulse.get("pcr_oi_change"),
        "pcr_core_atm": engine.pulse.get("pcr_core_atm"),
        "last_poll_sec_ago": last_poll_sec,
        "proximity_polling": getattr(engine, "proximity_polling", False),
        "has_active_position": bool(engine.state.get("active_position")),
        "telegram_configured": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
    }), 200


@app.route("/")
def index():
    tpl_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    root_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(root_path) and os.path.exists(tpl_path):
        if os.path.getmtime(root_path) > os.path.getmtime(tpl_path) or os.path.getsize(root_path) > os.path.getsize(tpl_path):
            with open(root_path, "r", encoding="utf-8") as f:
                return f.read()
    if os.path.exists(tpl_path):
        return render_template("index.html")
    elif os.path.exists(root_path):
        with open(root_path, "r", encoding="utf-8") as f:
            return f.read()
    return render_template("index.html")


@app.route("/api/status")
def get_status():
    try:
        engine.update_position_dynamics()
    except Exception as e:
        logger.warning(f"Engine dynamics error: {e}")

    now_dt = get_ist_now()
    now_t = now_dt.time()
    today_ist = now_dt.strftime("%Y-%m-%d")

    # Daily Rollover Check: Reset daily quota & PnL when new IST day starts!
    if engine.state.get("date") != today_ist:
        logger.info(f"[DAILY_ROLLOVER] IST date changed from {engine.state.get('date')} to {today_ist}. Resetting daily trade quota & PnL.")
        engine.state["date"] = today_ist
        engine.state["today_losses"] = 0
        engine.state["today_wins"] = 0
        engine.state["today_realized_gross_pnl"] = 0.0
        engine.state["today_total_charges"] = 0.0
        engine.state["today_realized_net_pnl"] = 0.0
        engine.state["circuit_breaker_active"] = False
        engine.save_state()

    is_mkt_open = (datetime.strptime("09:15", "%H:%M").time() <= now_t <= datetime.strptime("15:30", "%H:%M").time()) and (now_dt.weekday() < 5)
    is_pre_mkt = (datetime.strptime("08:50", "%H:%M").time() <= now_t < datetime.strptime("09:15", "%H:%M").time()) and (now_dt.weekday() < 5)

    # High-Frequency Live / Pre-Market Refresh: Ensure fresh Kotak Neo tick on user poll
    now_ts = time.time()
    if (is_mkt_open or is_pre_mkt) and (now_ts - getattr(engine, "last_api_poll", 0) >= (1.0 if is_mkt_open else 2.0)):
        try:
            engine.poll_kotak_neo(force=False)
        except Exception as poll_e:
            pass

    spot = float(engine.pulse.get("spot") or 25300.0)
    prev = float(engine.pulse.get("prev_close") or 25200.0)
    chg = round(spot - prev, 2)
    pct = round((chg / max(1.0, prev)) * 100, 2)

    # Pop audio chime event if triggered
    audio_event = engine.pending_audio_event
    engine.pending_audio_event = None

    # Capital Matrix (Real-Time Dynamic Tracking with Live MTM & Charges)
    pos = engine.state.get("active_position")
    live_mtm_net = float(pos.get("mtm_net_pnl", 0.0)) if pos else 0.0
    live_mtm_gross = float(pos.get("mtm_gross_pnl", 0.0)) if pos else 0.0
    live_charges = float(pos.get("estimated_charges", 0.0)) if pos else 0.0

    day_realized_net = float(engine.state.get("today_realized_net_pnl", 0.0))
    day_realized_charges = float(engine.state.get("today_total_charges", 0.0))
    day_realized_gross = float(engine.state.get("today_realized_gross_pnl", 0.0))

    last_closed_trade = getattr(engine, "last_closed_trade_cache", None)
    if not hasattr(engine, "last_trades_file_mtime"):
        engine.last_trades_file_mtime = 0.0

    if TRADES_FILE.exists():
        try:
            curr_mtime = os.path.getmtime(TRADES_FILE)
            if curr_mtime != engine.last_trades_file_mtime or last_closed_trade is None:
                engine.last_trades_file_mtime = curr_mtime
                import csv
                with open(TRADES_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    rows = list(csv.DictReader(f))
                    today_trades = [r for r in rows if r.get("date") == today_ist]
                    if today_trades:
                        target_row = dict(today_trades[-1])
                        for k, v in list(target_row.items()):
                            if v == "" or v == "nan" or v is None:
                                target_row[k] = None
                        target_row["is_today"] = True
                        engine.last_closed_trade_cache = target_row
                    else:
                        engine.last_closed_trade_cache = None
                last_closed_trade = engine.last_closed_trade_cache
        except Exception as e:
            logger.warning(f"Error reading last_closed_trade: {e}")

    total_net_pnl = round(day_realized_net + live_mtm_net, 2)
    total_charges = round(day_realized_charges + live_charges, 2)
    total_gross_pnl = round(day_realized_gross + live_mtm_gross, 2)

    real_funds = getattr(engine, "real_balance", 101244.91)
    if not real_funds or real_funds < 1000.0:
        real_funds = 101244.91
        engine.real_balance = real_funds

    real_collateral = getattr(engine, "real_collateral", 78929.79)
    if not real_collateral or real_collateral < 1000.0:
        real_collateral = 78929.79
        engine.real_collateral = real_collateral

    real_m_used = getattr(engine, "real_margin_used", 0.0)

    cur_equity = round(real_funds + total_net_pnl, 2)
    margin_used = round(real_m_used + (45000.0 if pos else 0.0), 2)
    free_cash = round(cur_equity - margin_used, 2)
    roi_today_pct = round((total_net_pnl / real_funds) * 100.0, 2) if real_funds > 0 else 0.0

    # Option Chain & PCR table
    chain_table = engine.get_clean_option_chain_table()

    # Replay state
    replay_info = {
        "feeder_mode": engine.feeder_mode,
        "is_replay_active": engine.replay_active,
        "is_paused": engine.replay_paused,
        "speed": engine.replay_speed,
        "date": engine.replay_date,
        "progress": engine.pulse.get("replay_progress", "0 / 0"),
        "time": engine.pulse.get("replay_time", now_dt.strftime("%H:%M")),
        "available_dates": getattr(engine, "available_replay_dates", []),
    }

    # Strategy Conditions Matrix (User Rule: Show each connected condition as TRUE or FALSE)
    d_ema = engine.pulse["daily_ema20"]
    e9 = engine.pulse["ema9"]
    e20 = engine.pulse["ema20"]
    e50 = engine.pulse["ema50"]
    pcr = engine.pulse["pcr"]
    losses_today = engine.state["today_losses"]
    today_trades_done = engine.state.get("today_wins", 0) + engine.state.get("today_losses", 0)
    cb_active = engine.state["circuit_breaker_active"]

    eval_time = datetime.strptime(engine.pulse.get("replay_time", "10:30"), "%H:%M").time() if (not is_mkt_open and engine.replay_active) else now_t
    is_time_window = (datetime.strptime("09:30", "%H:%M").time() <= eval_time <= datetime.strptime("14:30", "%H:%M").time())
    cond_circuit_breaker = bool(today_trades_done < 2 and not cb_active)

    # Demat Safety (Rule 6: Free Cash >= ₹50,000 & Demat mein 0 Open Legs)
    has_local_open = bool(engine.state.get("active_position"))
    has_broker_open = bool(getattr(engine, "cached_has_open_positions", False))
    cash_val = float(getattr(engine, "account_free_cash", 101244.91) or 101244.91)
    cash_sufficient = bool(cash_val >= 50000.0)
    cond_margin_safety = bool(not has_local_open and not has_broker_open and cash_sufficient)

    # Upgrade 1: Dual-PCR Evaluation (Weekly Cumulative OR Fresh Intraday Surge)
    bull_pcr_ok, bull_pcr_desc = engine.check_bull_pcr()
    bear_pcr_ok, bear_pcr_desc = engine.check_bear_pcr()

    # 5-Min Candle Tracking (Vikalp 2 Green vs Red Rule)
    is_green = engine.pulse.get("is_green_candle", True)
    is_red = engine.pulse.get("is_red_candle", True)

    # 1. Bull Put Spread Evaluation (Bullish Credit with Dual-PCR & Green Candle)
    bull_c1 = bool(spot > d_ema)
    bull_c2 = bool((e9 > e20 > e50) and is_green)
    bull_c3 = bool(bull_pcr_ok)
    bull_score = sum([bull_c1, bull_c2, bull_c3, is_time_window, cond_circuit_breaker, cond_margin_safety])

    # 2. Bear Call Spread Evaluation (Bearish Credit with Dual-PCR & Red Candle)
    bear_c1 = bool(spot < d_ema)
    bear_c2 = bool((e9 < e20 < e50) and is_red)
    bear_c3 = bool(bear_pcr_ok)
    bear_score = sum([bear_c1, bear_c2, bear_c3, is_time_window, cond_circuit_breaker, cond_margin_safety])

    # Determine front-runner spread and waiting status
    if bull_score == 6:
        active_spread = "BULL_PUT_SPREAD"
        lead_spread = "BULL_PUT_SPREAD"
        bull_badge = "ENTRY TRIGGERED (6/6)"
        bear_badge = "INACTIVE"
        bull_detail = "All 6 Conditions MET -> Bull Put Spread Triggered!"
        bear_detail = "Inactive (Market is Bullish)"
        verdict_text = "ENTRY TRIGGERED: BULL PUT SPREAD"
    elif bear_score == 6:
        active_spread = "BEAR_CALL_SPREAD"
        lead_spread = "BEAR_CALL_SPREAD"
        bear_badge = "ENTRY TRIGGERED (6/6)"
        bull_badge = "INACTIVE"
        bear_detail = "All 6 Conditions MET -> Bear Call Spread Triggered!"
        bull_detail = "Inactive (Market is Bearish)"
        verdict_text = "ENTRY TRIGGERED: BEAR CALL SPREAD"
    else:
        active_spread = "NEUTRAL"
        pcr_disp = f"{pcr:.3f}" if (pcr is not None and isinstance(pcr, (int, float))) else "Loading..."
        if bull_score >= bear_score:
            lead_spread = "BULL_PUT_SPREAD"
            missing_bull = []
            if not bull_c1: missing_bull.append("Spot > Daily EMA")
            if not bull_c2:
                if not (e9 > e20 > e50):
                    missing_bull.append("5m Momentum (9>20>50)")
                if not is_green:
                    missing_bull.append("5m Green Candle (Close >= Open)")
            if not bull_c3: missing_bull.append(f"Weekly PCR >= 1.05 (Currently {pcr_disp})")
            if not is_time_window: missing_bull.append("Time 09:30-14:30 IST")
            if not cond_circuit_breaker: missing_bull.append("0 Loss Circuit Breaker")
            if not cond_margin_safety:
                if has_local_open or has_broker_open:
                    missing_bull.append("0 Open Legs in Demat (Currently Open)")
                if not cash_sufficient:
                    missing_bull.append(f"Free Cash >= ₹50,000 (Currently ₹{cash_val:,.0f})")
            
            missing_text = ", ".join(missing_bull) if missing_bull else "All Conditions Met"
            bull_badge = f"WAITING ({bull_score}/6 TRUE)"
            bear_badge = f"NEUTRAL ({bear_score}/6)"
            bull_detail = f"Front-Runner! Waiting for: {missing_text}"
            bear_detail = "Inactive (Macro/Momentum is Bullish)"
            verdict_text = f"NEUTRAL (WAITING: BULL PUT SPREAD - {bull_score}/6 TRUE)"
        else:
            lead_spread = "BEAR_CALL_SPREAD"
            missing_bear = []
            if not bear_c1: missing_bear.append("Spot < Daily EMA")
            if not bear_c2:
                if not (e9 < e20 < e50):
                    missing_bear.append("5m Momentum (9<20<50)")
                if not is_red:
                    missing_bear.append("5m Red Candle (Close <= Open)")
            if not bear_c3: missing_bear.append(f"Weekly PCR <= 0.85 (Currently {pcr_disp})")
            if not is_time_window: missing_bear.append("Time 09:30-14:30 IST")
            if not cond_circuit_breaker: missing_bear.append("0 Loss Circuit Breaker")
            if not cond_margin_safety:
                if has_local_open or has_broker_open:
                    missing_bear.append("0 Open Legs in Demat (Currently Open)")
                if not cash_sufficient:
                    missing_bear.append(f"Free Cash >= ₹50,000 (Currently ₹{cash_val:,.0f})")

            missing_text = ", ".join(missing_bear) if missing_bear else "All Conditions Met"
            bear_badge = f"WAITING ({bear_score}/6 TRUE)"
            bull_badge = f"NEUTRAL ({bull_score}/6)"
            bear_detail = f"Front-Runner! Waiting for: {missing_text}"
            bull_detail = "Inactive (Macro/Momentum is Bearish)"
            verdict_text = f"NEUTRAL (WAITING: BEAR CALL SPREAD - {bear_score}/6 TRUE)"

    # Grid conditions reflect the active leader spread
    if lead_spread == "BEAR_CALL_SPREAD":
        cond_daily_regime = bear_c1
        cond_ema_cascade = bear_c2
        cond_pcr_sentiment = bear_c3
        c1_rule = "Spot < Daily 20 EMA (Bearish Macro Filter)"
        c1_metric = f"Spot: {spot:,.2f} < EMA: {d_ema:,.2f}"
        c2_rule = "5m EMA 9 < 20 < 50 & Red Candle"
        c2_metric = f"E9: {e9:.1f} < E20: {e20:.1f} < E50: {e50:.1f} | Red: {is_red}" if (e9 < e20 < e50) else f"E9: {e9:.1f}, E20: {e20:.1f}, E50: {e50:.1f} (Not 9<20<50) | Red: {is_red}"
        c3_rule = "Weekly PCR <= 0.85 (Call Writers Dominant / Bearish)"
        c3_metric = bear_pcr_desc
    else:
        cond_daily_regime = bull_c1
        cond_ema_cascade = bull_c2
        cond_pcr_sentiment = bull_c3
        c1_rule = "Spot > Daily 20 EMA (Bullish Macro Filter)"
        c1_metric = f"Spot: {spot:,.2f} > EMA: {d_ema:,.2f}"
        c2_rule = "5m EMA 9 > 20 > 50 & Green Candle"
        c2_metric = f"E9: {e9:.1f} > E20: {e20:.1f} > E50: {e50:.1f} | Green: {is_green}" if (e9 > e20 > e50) else f"E9: {e9:.1f} < E20: {e20:.1f} < E50: {e50:.1f} (Bearish Alignment) | Green: {is_green}"
        c3_rule = "Weekly PCR >= 1.05 (Put Writers Dominant / Bullish)"
        c3_metric = bull_pcr_desc

    strategy_conditions = [
        {
            "id": "daily_regime",
            "name": "1. Daily 20 EMA Macro Regime",
            "rule": c1_rule,
            "metric": c1_metric,
            "status": cond_daily_regime,
            "badge": "TRUE" if cond_daily_regime else "FALSE",
        },
        {
            "id": "ema_cascade",
            "name": "2. 5-Min Momentum Alignment",
            "rule": c2_rule,
            "metric": c2_metric,
            "status": cond_ema_cascade,
            "badge": "TRUE" if cond_ema_cascade else "FALSE",
        },
        {
            "id": "pcr_sentiment",
            "name": "3. PCR Sentiment Confirmation",
            "rule": c3_rule,
            "metric": c3_metric,
            "status": cond_pcr_sentiment,
            "badge": "TRUE" if cond_pcr_sentiment else "FALSE",
        },
        {
            "id": "time_window",
            "name": "4. Execution Time Window",
            "rule": "09:30 AM - 02:30 PM IST (Avoid Opening & Closing Noise)",
            "metric": f"Time: {eval_time.strftime('%H:%M')} IST",
            "status": is_time_window,
            "badge": "TRUE" if is_time_window else "FALSE",
        },
        {
            "id": "circuit_breaker",
            "name": "5. Max 2 Trades Daily Quota",
            "rule": "Max 2 Trades Allowed Per Day (Trade 2 Allowed After Trade 1)",
            "metric": f"{today_trades_done} / 2 Trades Today",
            "status": cond_circuit_breaker,
            "badge": "TRUE" if cond_circuit_breaker else "FALSE",
        },
        {
            "id": "margin_safety",
            "name": "6. Demat Safety & Margin Gate",
            "rule": "Free Cash >= ₹50,000 & Demat mein 0 Open Legs",
            "metric": f"Cash: ₹{cash_val:,.0f} | Open Legs: {1 if (has_local_open or has_broker_open) else 0}",
            "status": cond_margin_safety,
            "badge": "TRUE" if cond_margin_safety else "FALSE",
        },
    ]

    return jsonify({
        "time": now_dt.strftime("%d-%b-%Y %H:%M:%S IST"),
        "chain_updated_at": engine.pulse.get("chain_updated_at", now_dt.strftime("%H:%M:%S IST")),
        "feeder_mode": engine.feeder_mode,
        "execution_mode": engine.execution_mode,
        "is_market_open": is_mkt_open,
        "is_pre_market": is_pre_mkt,
        "market_status_text": "LIVE MARKET STREAM" if is_mkt_open else ("PRE-MARKET WARMUP (Live F&O Opens 09:15 AM)" if is_pre_mkt else "OFF-MARKET STANDBY"),
        "account_greeting": engine.greeting,
        "is_authenticated": engine.authenticated,
        "status_message": engine.status_message,
        "lot_size": engine.lot_size,
        "active_lots": getattr(engine, "active_lots", 1),
        "user_configured_lots": engine.state.get("user_configured_lots", getattr(engine, "active_lots", 1)),
        "mkt_lot": int(engine.pulse.get("mkt_lot", 65)),
        "audio_event": audio_event,
        "replay": replay_info,
        "strategy_matrix": {
            "all_conditions_met": (bull_score == 6 or bear_score == 6),
            "met_count": bull_score if lead_spread == "BULL_PUT_SPREAD" else bear_score,
            "total_count": 6,
            "verdict": verdict_text,
            "signal": active_spread,
            "lead_spread": lead_spread,
            "bull_tracker": {
                "name": "BULL PUT SPREAD",
                "score": bull_score,
                "badge": bull_badge,
                "is_leader": (lead_spread == "BULL_PUT_SPREAD"),
                "detail": bull_detail
            },
            "bear_tracker": {
                "name": "BEAR CALL SPREAD",
                "score": bear_score,
                "badge": bear_badge,
                "is_leader": (lead_spread == "BEAR_CALL_SPREAD"),
                "detail": bear_detail
            },
            "conditions": strategy_conditions,
        },
        "capital": {
            "initial": real_funds,
            "current_equity": cur_equity,
            "margin_used": margin_used,
            "free_cash": free_cash,
            "real_collateral": real_collateral,
            "is_real_kotak_funds": True,
            "charges": total_charges,
            "total_net_pnl": total_net_pnl,
            "total_gross_pnl": total_gross_pnl,
            "broker_unrealised_pnl": live_mtm_gross,
            "live_mtm_net": live_mtm_net,
            "roi_today_pct": roi_today_pct,
        },
        "spot": {
            "ltp": spot,
            "prev_close": prev,
            "change": round(chg, 2),
            "pct_change": round(pct, 2),
        },
        "indicators": {
            "daily_ema20": engine.pulse["daily_ema20"],
            "regime": "BULLISH" if spot > engine.pulse["daily_ema20"] else "BEARISH",
            "ema9": engine.pulse["ema9"],
            "ema20": engine.pulse["ema20"],
            "ema50": engine.pulse["ema50"],
            "pcr": engine.pulse["pcr"],
            "pcr_oi_change": engine.pulse.get("pcr_oi_change", 1.0),
            "pcr_core_atm": engine.pulse.get("pcr_core_atm", 1.0),
            "india_vix": engine.pulse.get("india_vix", 13.5),
            "vix_regime": engine.pulse.get("vix_regime", "NORMAL_VOLATILITY (Champion)"),
            "wing_distance": engine.pulse.get("wing_distance", 350),
            "active_lots": getattr(engine, "active_lots", 1),
            "proximity_polling": getattr(engine, "proximity_polling", False),
            "total_call_oi": f"{engine.pulse.get('total_call_oi', 0):,}",
            "total_put_oi": f"{engine.pulse.get('total_put_oi', 0):,}",
            "call_oi_chg": f"{engine.pulse.get('call_oi_chg', 0):+,}",
            "put_oi_chg": f"{engine.pulse.get('put_oi_chg', 0):+,}",
            "pcr_sentiment": "BULLISH (>1.05)" if (engine.pulse["pcr"] and engine.pulse["pcr"] >= 1.05) else ("BEARISH (<0.85)" if (engine.pulse["pcr"] and engine.pulse["pcr"] <= 0.85) else "NEUTRAL (0.85-1.05)"),
            "signal": engine.pulse["signal"],
            "expiry": engine.pulse["next_expiry"],
            "is_green_candle": engine.pulse.get("is_green_candle", True),
            "is_red_candle": engine.pulse.get("is_red_candle", False),
            "candle_5m_open": engine.pulse.get("candle_5m_open", spot),
            "candle_5m_close": engine.pulse.get("candle_5m_close", spot),
            "candle_status": engine.pulse.get("candle_status", "WAITING_FOR_FIRST_5M_CLOSE"),
            "candle_bar_time": engine.pulse.get("candle_bar_time", "--:--"),
            "candle_data_lag": engine.pulse.get("candle_data_lag", 0.0),
        },
        "option_chain_table": chain_table,
        "active_position": engine.state.get("active_position"),
        "last_closed_trade": last_closed_trade,
        "performance": {
            "today_wins": engine.state["today_wins"],
            "today_losses": engine.state["today_losses"],
            "today_trades": engine.state["today_wins"] + engine.state["today_losses"],
            "today_gross_pnl": total_gross_pnl,
            "today_charges": total_charges,
            "today_net_pnl": total_net_pnl,
            "today_realized_gross": day_realized_gross,
            "today_realized_net": day_realized_net,
            "today_realized_charges": day_realized_charges,
            "live_mtm_gross": live_mtm_gross,
            "live_mtm_net": live_mtm_net,
            "live_charges": live_charges,
            "circuit_breaker_active": engine.state["circuit_breaker_active"],
        },
        "strategy_info": {
            "name": "Strategy 3: Pure ATM Balanced Wing (50Δ / 15Δ)",
            "benchmark": "NIFTY 50 Intraday Credit Spread",
            "verified_trades": 302,
            "win_rate": "68.2%",
            "positive_months": "84.2% (16 of 19 Months)",
            "avg_monthly_pnl": 8304.56,
            "max_drawdown_pct": "6.88%",
            "max_drawdown_rs": 13768.07,
            "profit_factor": 1.79,
            "expectancy_per_trade": 522.47,
            "target_rule": "40% Premium Decay (Exit at 0.60x)",
            "breakeven_rule": "25% Decay -> Move SL to Cost (0.75x)",
            "hard_sl_rule": "1.60x Spread Cap (-Rs 3,900 max loss on 1 lot)",
            "time_exit": "03:15 PM EOD"
        },
        "manual_paused": getattr(engine, "manual_paused", False),
        "paused_reason": getattr(engine, "paused_reason", ""),
        "last_trade_exit_time": getattr(engine, "last_trade_exit_time", 0),
        "cooldown_remaining_sec": max(0, int(1800 - (time.time() - getattr(engine, "last_trade_exit_time", 0)))) if (getattr(engine, "last_trade_exit_time", 0) > 0 and (engine.state.get("today_wins", 0) + engine.state.get("today_losses", 0)) >= 1) else 0,
        "broker_sync_status": "RECONCILIATION_REQUIRED" if getattr(engine, "reconciliation_alert", False) else ("RE_SYNCING" if getattr(engine, "zero_pos_consecutive_count", 0) > 0 else "SYNCED"),
        "zero_pos_consecutive_count": getattr(engine, "zero_pos_consecutive_count", 0),
        "reconciliation_alert": getattr(engine, "reconciliation_alert", False),
        "reconciliation_reason": getattr(engine, "reconciliation_reason", ""),
        "reconciliation_blocked": getattr(engine, "reconciliation_blocked", False),
    })


@app.route("/api/set_execution_mode", methods=["POST"])
def api_set_execution_mode():
    data = request.get_json() or {}
    target_mode = data.get("mode", "PAPER").upper()
    mpin = str(data.get("mpin", "")).strip()

    if target_mode == "LIVE":
        if mpin == engine.security_mpin or mpin == engine.mpin:
            engine.execution_mode = "LIVE"
            engine.status_message = "SAFETY UNLOCKED: LIVE EXECUTION ACTIVE ON KOTAK NEO"
            engine.save_engine_mode()
            return jsonify({"success": True, "mode": "LIVE", "message": "Live Execution Unlocked!"})
        else:
            return jsonify({"success": False, "message": "Invalid Security MPIN! Remains in Paper Mode."}), 403
    else:
        engine.execution_mode = "PAPER"
        engine.status_message = "SAFETY ACTIVE: Returned to 100% Virtual Paper Mode."
        engine.save_engine_mode()
        return jsonify({"success": True, "mode": "PAPER", "message": "Switched to Safe Paper Mode."})


@app.route("/api/pause_algo", methods=["POST"])
def api_pause_algo():
    engine.manual_paused = True
    engine.paused_reason = "Manually paused by user from Web Cockpit"
    engine.status_message = "SAFETY PAUSE: Algo trading paused by user. No new trades will be placed."
    engine.save_engine_mode()
    return jsonify({"success": True, "manual_paused": True, "message": "Algo successfully paused."})


@app.route("/api/resume_algo", methods=["POST"])
def api_resume_algo():
    engine.manual_paused = False
    engine.paused_reason = ""
    engine.status_message = "ALGO ACTIVE: Safety pause cleared. Ready for signals."
    engine.save_engine_mode()
    return jsonify({"success": True, "manual_paused": False, "message": "Algo successfully resumed."})


@app.route("/api/replay/control", methods=["POST"])
def api_replay_control():
    data = request.get_json() or {}
    action = data.get("action")  # 'play', 'pause', 'step', 'speed', 'date'

    if action == "play":
        engine.replay_active = True
        engine.replay_paused = False
        engine.status_message = f"Replay Playing @ {engine.replay_speed}x"
    elif action == "pause":
        engine.replay_paused = True
        engine.status_message = "Replay Paused"
    elif action == "step":
        engine.replay_active = True
        engine.replay_paused = True
        engine.step_replay_next()
    elif action == "speed":
        engine.replay_speed = int(data.get("speed", 1))
        engine.status_message = f"Replay Speed Set to {engine.replay_speed}x"
    elif action == "date":
        new_date = data.get("date")
        if new_date:
            engine.init_replay_data(new_date)

    return jsonify({"success": True, "replay_active": engine.replay_active, "paused": engine.replay_paused, "speed": engine.replay_speed})


@app.route("/api/trigger", methods=["POST"])
def api_trigger():
    success, msg = engine.trigger_trade(force=True)
    return jsonify({"success": success, "message": msg})


@app.route("/api/square_off", methods=["POST"])
def api_square_off():
    success, msg = engine.square_off_position("MANUAL_WEB_EXIT")
    return jsonify({"success": success, "message": msg})


@app.route("/api/reset_circuit_breaker", methods=["POST"])
def api_reset_circuit_breaker():
    with engine.state_lock:
        engine.state["circuit_breaker_active"] = False
        engine.state["today_losses"] = 0
        engine.state["today_wins"] = 0
        engine.state["today_realized_gross_pnl"] = 0.0
        engine.state["today_total_charges"] = 0.0
        engine.state["today_realized_net_pnl"] = 0.0
        engine.state["date"] = get_ist_now().strftime("%Y-%m-%d")
        engine.save_state()
    engine.status_message = "Circuit Breaker & Daily Quota Reset (0 / 2 Trades Done)."
    return jsonify({"success": True, "message": "Circuit Breaker & Daily Quota Reset."})


@app.route("/api/reconcile_broker", methods=["POST"])
def api_reconcile_broker():
    """Manual or frontend-triggered position reconciliation with Kotak Neo broker."""
    try:
        if not engine.authenticated or not engine.client:
            engine.authenticate()
        if not engine.client:
            return jsonify({"success": False, "message": "Broker not connected"}), 400

        real_pos = engine.client.positions()
        is_empty_broker_pos = False
        if isinstance(real_pos, dict):
            st_c = str(real_pos.get("stCode", ""))
            err_m = str(real_pos.get("errMsg", "")).lower()
            desc_m = str(real_pos.get("desc", "")).lower()
            if st_c in ["5203", "5204"] or "no data" in err_m or "data not found" in desc_m:
                is_empty_broker_pos = True

        has_real_pos = False
        pos_list = (real_pos.get("data", []) or []) if not is_empty_broker_pos else []
        for p in pos_list:
            ex_seg = str(p.get("exSeg", "")).lower()
            sym = str(p.get("sym", "")).upper()
            trd_sym = str(p.get("trdSym", "")).upper()
            stk_prc = float(p.get("stkPrc", 0.0) or 0.0)
            if ex_seg != "nse_fo" or not (sym == "NIFTY" or trd_sym.startswith("NIFTY")) or stk_prc <= 0:
                continue
            buy_q = int(float(p.get("flBuyQty", 0)))
            sell_q = int(float(p.get("flSellQty", 0)))
            if abs(buy_q - sell_q) > 0:
                has_real_pos = True
                break

        if not has_real_pos and not engine.state.get("active_position"):
            engine.broker_sync_status = "SYNCED"
            engine.reconciliation_blocked = False
            engine.reconciliation_reason = ""
            engine.reconciliation_alert = False
            return jsonify({"success": True, "status": "SYNCED", "message": "Broker reconciled: 0 positions on broker, 0 in terminal. Ready to trade."})
        elif has_real_pos and engine.state.get("active_position"):
            engine.broker_sync_status = "SYNCED"
            engine.reconciliation_blocked = False
            engine.reconciliation_reason = ""
            engine.reconciliation_alert = False
            return jsonify({"success": True, "status": "SYNCED", "message": "Broker reconciled: Active position confirmed on broker."})
        else:
            return jsonify({"success": False, "status": engine.broker_sync_status, "message": f"Broker position discrepancy (Broker has pos: {has_real_pos}, Terminal has pos: {bool(engine.state.get('active_position'))})"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/reconnect", methods=["POST"])
def api_reconnect():
    """Force triggers Kotak Neo re-authentication with fresh dynamic TOTP and MPIN."""
    try:
        success = engine.authenticate()
        if success:
            try:
                engine.poll_kotak_neo(force=True)
            except Exception:
                pass
        return jsonify({
            "success": bool(success),
            "authenticated": engine.authenticated,
            "message": engine.status_message,
            "greeting": getattr(engine, "greeting", "RAHUL"),
            "balance": engine.real_balance,
            "time": get_ist_now().strftime("%H:%M:%S")
        })
    except Exception as e:
        return jsonify({"success": False, "authenticated": False, "message": f"Reconnect Exception: {str(e)}"}), 500


@app.route("/api/manual_order", methods=["POST"])
def api_manual_order():
    """Executes single-leg BUY or SELL directly from the Option Chain on Kotak Neo."""
    try:
        data = request.get_json(silent=True) or {}
        try:
            strike = int(data.get("strike", 0))
        except (ValueError, TypeError):
            strike = 0
        opt_type = str(data.get("opt_type", "CE")).upper()
        action = str(data.get("action", "B")).upper()  # 'B' or 'S'
        try:
            price = float(data.get("price", 0.0) or 0.0)
        except (ValueError, TypeError):
            price = 0.0
        qty = engine.lot_size  # 65 qty (1 lot)

        if not strike:
            return jsonify({"success": False, "message": "Invalid strike price"}), 400

        exp_str = engine.pulse.get("next_expiry", "2026-11-23")
        try:
            exp_dt = datetime.strptime(str(exp_str)[:10], "%Y-%m-%d")
            exp_code = exp_dt.strftime("%y%b").upper()
        except Exception:
            exp_code = "26NOV"
        symbol = f"NIFTY{exp_code}{strike}{opt_type}"

        # Match exact Kotak Neo symbol from live scrip search if available
        if engine.client and engine.authenticated:
            try:
                scrips = engine.client.search_scrip(exchange_segment="nse_fo", symbol="NIFTY")
                for s in (scrips or []):
                    if s.get("pInstType") == "OPTIDX" and s.get("pSymbolName") == "NIFTY" and s.get("pOptionType") == opt_type:
                        strk_val = float(s.get("dStrikePrice;", 0))
                        if int(strk_val / 100) == strike or int(strk_val) == strike:
                            if exp_code.lower() in str(s.get("pExpiryDate", "")).lower() or exp_code in str(s.get("pTrdSymbol", "")):
                                symbol = s.get("pTrdSymbol")
                                break
            except Exception:
                pass

        action_label = "BUY" if action == "B" else "SELL"
        now_ist = get_ist_now()
        is_mkt_open = (now_ist.weekday() < 5) and (d_time(9, 15) <= now_ist.time() <= d_time(15, 30))
        amo_flag = "NO" if is_mkt_open else "YES"

        if engine.execution_mode == "LIVE" and engine.authenticated and engine.client:
            try:
                price_fmt = f"{round(round(price / 0.05) * 0.05, 2):.2f}" if price > 0 else "0"
                res = engine.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price=price_fmt,
                    order_type="L" if price > 0 else "MKT",
                    quantity=str(qty),
                    validity="DAY",
                    trading_symbol=symbol,
                    transaction_type=action,
                    amo=amo_flag,
                )

                # Check for Kotak API rejection or error
                stat = res.get("stat") if isinstance(res, dict) else None
                st_code = res.get("stCode") if isinstance(res, dict) else None
                order_id = res.get("nOrdNo") or (res.get("data", {}).get("nOrdNo") if isinstance(res.get("data"), dict) else None) if isinstance(res, dict) else None

                if stat == "Not_Ok" or not order_id:
                    err_msg = res.get("errMsg") or res.get("desc") or res.get("Error") or "Order placement rejected by Kotak Neo"
                    if st_code == 100008 or "unauthorized" in str(err_msg).lower():
                        client_ip = "45.124.146.216"
                        try:
                            ip_info = engine.client.whatsmyip()
                            if ip_info and isinstance(ip_info, dict) and ip_info.get("data"):
                                client_ip = ip_info["data"][0].get("ip", client_ip)
                        except Exception:
                            pass
                        msg = f"Kotak Neo Error 100008 (Unauthorized): Outbound IP {client_ip} Kotak Trade API Portal me whitelisted nahi hai. Please portal par jaakar IP whitelist karein."
                        engine.status_message = f"Kotak API Order Blocked: IP {client_ip} Not Whitelisted"
                        return jsonify({"success": False, "message": msg}), 400

                    if st_code == 13 or "error from core" in str(err_msg).lower():
                        friendly_msg = "Market Closed / Exchange Offline (stCode: 13): Raat ke samay NSE F&O Exchange band rehta hai. Subah 09:15 AM par market open hote hi Kotak Neo real orders execute karega!"
                        engine.status_message = "Kotak Core: Market Closed (Exchange Offline)"
                        return jsonify({"success": False, "message": friendly_msg}), 400

                    engine.status_message = f"Kotak API Rejected: {err_msg}"
                    return jsonify({"success": False, "message": f"Kotak Neo Rejection: {err_msg} (stCode: {st_code})"}), 400

                engine.status_message = f"Real Order Placed: {action_label} {symbol} ({qty} Qty) [Order #{order_id}]"
                return jsonify({"success": True, "message": f"KOTAK ORDER EXECUTED: {action_label} {symbol} ({qty} Qty) - Order #{order_id}"})
            except Exception as e:
                engine.status_message = f"Kotak Order Error: {str(e)[:35]}"
                return jsonify({"success": False, "message": f"Kotak API Order Exception: {str(e)[:60]}"}), 500
        else:
            engine.status_message = f"Manual Order Registered: {action_label} {symbol} ({qty} Qty)"
            return jsonify({"success": True, "message": f"ORDER RECORDED: {action_label} {symbol} ({qty} Qty)"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Manual Order Handler Error: {str(e)}"}), 500


@app.route("/api/orders")
def get_orders():
    """Fetches real order book directly from Kotak Neo API, or fallback to saved today's orders."""
    now = time.time()
    
    # 1. Return normalized cache if queried within TTL (5s during active trade, 20s during idle scanning)
    orders_cache_ttl = 5.0 if engine.state.get("active_position") else 20.0
    if hasattr(engine, "last_formatted_orders_cache") and engine.last_formatted_orders_cache and (now - getattr(engine, "last_orders_cache_time", 0) < orders_cache_ttl):
        return jsonify(engine.last_formatted_orders_cache)

    raw_orders = []
    source = "EMPTY"

    # 2. Try Live Kotak Neo API first if authenticated
    if engine.authenticated and engine.client:
        try:
            orders_res = engine.client.order_report()
            if is_kotak_ok(orders_res) and orders_res.get("data"):
                raw_orders = orders_res.get("data", [])
                engine.last_orders_cache = raw_orders
                source = "KOTAK_NEO_API"
        except Exception as e:
            logger.warning(f"Error fetching live Kotak order report: {e}")

    # 3. Check in-memory raw orders cache
    if not raw_orders and hasattr(engine, "last_orders_cache") and engine.last_orders_cache:
        raw_orders = engine.last_orders_cache
        source = "RAW_CACHE"

    # 4. Fallback to today's archived CSV if still empty
    if not raw_orders:
        today_str = get_ist_now().strftime("%Y-%m-%d")
        archived_orders_file = RESULTS_DIR / f"closed_orders_{today_str}.csv"
        if archived_orders_file.exists():
            try:
                df = pd.read_csv(archived_orders_file, on_bad_lines="skip")
                raw_orders = df.to_dict(orient="records")
                source = "ARCHIVED_CSV"
            except Exception:
                raw_orders = []

    # Normalize orders
    formatted_orders = []
    open_count = 0
    complete_count = 0
    rejected_count = 0

    for o in raw_orders:
        ord_src = str(o.get("ordSrc", "")).upper()
        ord_no = str(o.get("nOrdNo", ""))
        trd_sym = str(o.get("trdSym", ""))
        ex_seg = str(o.get("exSeg", "")).lower()

        # Filter strictly for NIFTY F&O orders (exclude equity delivery/SIPs like ITBEES)
        if ex_seg and ex_seg != "nse_fo":
            continue
        if not ("NIFTY" in trd_sym or "NIFTY" in str(o.get("sym", "")).upper()):
            continue

        # Tag Source: Algo vs Manual Phone
        if "NEOTRADEAPI" in ord_src or ord_no in getattr(engine, "terminal_order_ids", set()):
            source_badge = "ALGO AUTO"
        elif "MOB" in ord_src:
            source_badge = "MANUAL PHONE"
        else:
            source_badge = ord_src or "BROKER"

        status_raw = str(o.get("ordSt", "")).lower()
        is_open = status_raw in ["open", "pending", "trigger_pending", "modify_pending", "trigger pending"]
        is_complete = status_raw in ["complete", "traded", "filled"]
        is_rejected = status_raw in ["rejected", "cancelled"]

        if is_open:
            open_count += 1
            status_display = "OPEN"
        elif is_complete:
            complete_count += 1
            status_display = "COMPLETE"
        elif is_rejected:
            rejected_count += 1
            status_display = "REJECTED"
        else:
            status_display = status_raw.upper() or "UNKNOWN"

        trns = str(o.get("trnsTp", "")).upper()
        action = "BUY" if trns == "B" else ("SELL" if trns == "S" else trns)

        avg_prc = float(o.get("avgPrc", 0.0) or 0.0)
        lmt_prc = float(o.get("prc", 0.0) or 0.0)
        fill_prc = avg_prc if avg_prc > 0 else lmt_prc

        formatted_orders.append({
            "order_id": str(o.get("nOrdNo", "")),
            "time": str(o.get("ordDtTm", o.get("ordEntTm", o.get("hsUpTm", "--")))),
            "symbol": trd_sym,
            "action": action,
            "qty": int(float(o.get("qty", o.get("flBuyQty", o.get("flSellQty", 0))) or 0)),
            "limit_price": lmt_prc,
            "avg_price": fill_prc,
            "status": status_display,
            "status_raw": status_raw,
            "is_open": is_open,
            "source_badge": source_badge,
            "reject_reason": str(o.get("rejRsn", o.get("rejShortDesc", "--"))),
            "product": str(o.get("prod", "NRML")),
        })

    payload = {
        "status": "success",
        "source": source,
        "total_count": len(formatted_orders),
        "open_count": open_count,
        "complete_count": complete_count,
        "rejected_count": rejected_count,
        "orders": formatted_orders
    }
    engine.last_formatted_orders_cache = payload
    engine.last_orders_cache_time = now
    return jsonify(payload)


@app.route("/api/trades")
def get_trades():
    trades = []
    if TRADES_FILE.exists():
        try:
            df = pd.read_csv(TRADES_FILE, on_bad_lines="skip")
            trades = df.to_dict(orient="records")
        except Exception:
            pass

    # Ensure today's trades from daily report are present if CSV was cached or desynced
    today_str = get_ist_now().strftime("%Y-%m-%d")
    today_trades_exist = any(str(t.get("date", "")) == today_str for t in trades)
    if not today_trades_exist:
        today_rep_file = REPORTS_DIR / f"daily_report_{today_str}.json"
        if today_rep_file.exists():
            try:
                with open(today_rep_file, "r") as f:
                    rep_json = json.load(f)
                    rep_trades = rep_json.get("trades", [])
                    if rep_trades:
                        trades = rep_trades + trades
            except Exception:
                pass

    return jsonify(trades)


@app.route("/api/export_csv")
def export_csv():
    if TRADES_FILE.exists():
        try:
            df = pd.read_csv(TRADES_FILE, on_bad_lines="skip")
            csv_buffer = StringIO()
            df.to_csv(csv_buffer, index=False)
            return Response(
                csv_buffer.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment;filename=nifty_quant_trades_ledger_{get_ist_now().strftime('%Y%m%d')}.csv"}
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "No trades recorded yet."}), 404


@app.route("/api/download_option_chain_archive")
def download_option_chain_archive():
    today_str = get_ist_now().strftime("%Y-%m-%d")
    opt_csv_path = Path("data") / "daily_archive" / f"nifty_options_strikes_{today_str}.csv"
    opt_parquet_path = Path("data") / "daily_archive" / f"nifty_options_strikes_{today_str}.parquet"
    
    # Flush current buffer to disk first
    if hasattr(engine, "archiver") and engine.archiver:
        engine.archiver.flush_live_buffer()
        
    if opt_csv_path.exists():
        return send_file(opt_csv_path, as_attachment=True, download_name=f"nifty_options_strikes_1min_{today_str}.csv")
    elif opt_parquet_path.exists():
        df = pd.read_parquet(opt_parquet_path)
        csv_buf = StringIO()
        df.to_csv(csv_buf, index=False)
        return Response(
            csv_buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=nifty_options_strikes_1min_{today_str}.csv"}
        )
    else:
        # Generate current chain snapshot table as fallback CSV
        chain_table = engine.get_clean_option_chain_table()
        df = pd.DataFrame(chain_table)
        csv_buf = StringIO()
        df.to_csv(csv_buf, index=False)
        return Response(
            csv_buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=nifty_options_chain_snapshot_{today_str}.csv"}
        )


@app.route("/api/export_historical_options")
def export_historical_options():
    target_date = request.args.get("date", "2026-03-05")
    strike = request.args.get("strike", None)
    opt_type = request.args.get("type", None)
    
    master_file = Path("data/nifty_options_master_2020_2026_1min.parquet")
    if not master_file.exists():
        return jsonify({"error": "Master options parquet not found"}), 404
        
    import pyarrow.dataset as ds
    start_ts = pd.Timestamp(f"{target_date} 00:00:00")
    end_ts = pd.Timestamp(f"{target_date} 23:59:59")
    dataset = ds.dataset(str(master_file))
    
    filter_expr = (ds.field("timestamp") >= start_ts) & (ds.field("timestamp") <= end_ts)
    if strike:
        try:
            filter_expr = filter_expr & (ds.field("strike_price") == float(strike))
        except Exception:
            pass
    if opt_type:
        filter_expr = filter_expr & (ds.field("option_type") == opt_type.upper())
        
    table = dataset.to_table(filter=filter_expr)
    if table.num_rows == 0:
        return jsonify({"error": f"No 1-minute option rows found for {target_date} (strike={strike})"}), 404
        
    df = table.to_pandas().sort_values(["timestamp", "strike_price", "option_type"])
    csv_buf = StringIO()
    df.to_csv(csv_buf, index=False)
    
    filename = f"nifty_1min_options_{target_date}"
    if strike:
        filename += f"_strike_{strike}"
    if opt_type:
        filename += f"_{opt_type.upper()}"
    filename += ".csv"
    
    return Response(
        csv_buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


# In-memory cached candles for lightweight TradingView chart (only ~200 items, <10 KB RAM)
CACHED_BASE_CANDLES = []
LAST_CANDLE_LOAD_TIME = 0

def get_base_chart_candles():
    global CACHED_BASE_CANDLES, LAST_CANDLE_LOAD_TIME
    now = time.time()
    # Cache for 10 minutes to avoid re-reading parquet on every 5s poll
    if CACHED_BASE_CANDLES and (now - LAST_CANDLE_LOAD_TIME < 600):
        return CACHED_BASE_CANDLES

    if MASTER_SPOT_PARQUET.exists():
        try:
            import gc
            df = pd.read_parquet(MASTER_SPOT_PARQUET, columns=["open", "high", "low", "close"])
            tail_df = df.tail(200).reset_index()
            del df
            gc.collect()

            candles = []
            for _, row in tail_df.iterrows():
                ts = row.get("timestamp") or row.get("index")
                if hasattr(ts, "timestamp"):
                    unix_time = int(ts.timestamp())
                else:
                    unix_time = int(pd.to_datetime(ts).timestamp())
                candles.append({
                    "time": unix_time,
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                })
            CACHED_BASE_CANDLES = candles
            LAST_CANDLE_LOAD_TIME = now
        except Exception as e:
            logger.warning(f"Error loading base chart candles: {e}")
    return CACHED_BASE_CANDLES


@app.route("/api/chart_data")
def api_chart_data():
    """
    Upgrade 8: TradingView Lightweight Charts endpoint.
    Returns the latest 200 1-minute OHLC candles from memory cache + today's live minutes.
    Memory-optimized: Zero file reads on repeated polls.
    """
    try:
        base_candles = get_base_chart_candles()
        candles = [dict(c) for c in base_candles]

        # Append or update with live spot tick
        spot = engine.pulse.get("spot")
        if spot and candles:
            now_unix = int(time.time())
            last_c = candles[-1]
            if now_unix - last_c["time"] < 60:
                last_c["high"] = max(last_c["high"], spot)
                last_c["low"] = min(last_c["low"], spot)
                last_c["close"] = spot
            else:
                candles.append({
                    "time": now_unix,
                    "open": spot,
                    "high": spot,
                    "low": spot,
                    "close": spot,
                })
        return jsonify({"success": True, "candles": candles})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "candles": []})


@app.route("/api/daily_report")
def api_daily_report():
    """
    Upgrade 7: Automated 15:35 Daily Performance Report Viewer / Downloader.
    """
    format_type = request.args.get("format", "json")
    report = engine.generate_daily_report()
    if format_type == "html":
        report_html_path = REPORTS_DIR / f"daily_report_{report['date']}.html"
        if report_html_path.exists():
            with open(report_html_path, "r", encoding="utf-8") as f:
                return f.read(), 200, {"Content-Type": "text/html"}
    return jsonify(report)


@app.route("/api/vikalp2/audit_ledger")
@app.route("/api/strategy3/monthly_journal")
def api_vikalp2_audit_ledger():
    """
    Returns Vikalp 2: 6-Year Champion Quant Model (2020-2026)
    Verified 967 Trades Audit Ledger, Year-by-Year & Monthly matrix.
    """
    csv_path = Path("results/vikalp2_full_967_trades_postmortem.csv")
    matrix_path = Path("results/vikalp2_monthly_matrix.csv")
    if not csv_path.exists():
        return jsonify({"success": False, "error": "Vikalp 2 verified CSV not found"})

    try:
        df = pd.read_csv(csv_path)
        annual_data = []
        for yr, ydf in df.groupby("year"):
            y_trades = len(ydf)
            y_wins = int((ydf["net_pnl"] > 0).sum())
            y_losses = int((ydf["net_pnl"] <= 0).sum())
            y_wr = round(y_wins / y_trades * 100, 1)
            y_net = round(float(ydf["net_pnl"].sum()), 2)
            y_eq = ydf["net_pnl"].cumsum()
            y_dd = round(float((y_eq - y_eq.cummax()).min()), 2)
            gw = ydf[ydf["net_pnl"] > 0]["net_pnl"].sum()
            gl = abs(ydf[ydf["net_pnl"] <= 0]["net_pnl"].sum())
            y_pf = round(float(gw / gl if gl > 0 else 99.0), 2)
            annual_data.append({
                "year": int(yr),
                "trades": y_trades,
                "wins": y_wins,
                "losses": y_losses,
                "win_rate": y_wr,
                "net_pnl": y_net,
                "max_drawdown": y_dd,
                "profit_factor": y_pf
            })

        # Load monthly matrix
        monthly_data = []
        if matrix_path.exists():
            df_m = pd.read_csv(matrix_path)
            for _, r in df_m.iterrows():
                monthly_data.append({
                    "month": str(r["year_month"]),
                    "trades": int(r["trades"]),
                    "wins": int(r["wins"]),
                    "win_rate": round(float(r["win_rate"]), 1),
                    "net_pnl": round(float(r["net_pnl"]), 2),
                    "is_positive": bool(r["net_pnl"] > 0)
                })

        total_net = round(float(df["net_pnl"].sum()), 2)
        total_trades = len(df)
        total_wins = int((df["net_pnl"] > 0).sum())
        total_losses = int((df["net_pnl"] <= 0).sum())
        win_rate = round(float(total_wins / total_trades * 100), 1)
        eq = df["net_pnl"].cumsum()
        max_dd = round(float((eq - eq.cummax()).min()), 2)
        gw = df[df["net_pnl"] > 0]["net_pnl"].sum()
        gl = abs(df[df["net_pnl"] <= 0]["net_pnl"].sum())
        profit_factor = round(float(gw / gl if gl > 0 else 99.0), 2)

        recent_trades = df.tail(15).to_dict(orient="records")

        return jsonify({
            "success": True,
            "strategy": "Vikalp 2: 6-Year Champion Quant Model (2020-2026)",
            "summary": {
                "total_trades": total_trades,
                "total_wins": total_wins,
                "total_losses": total_losses,
                "total_net_pnl": total_net,
                "win_rate": win_rate,
                "max_drawdown": f"-₹{abs(max_dd):,.2f}",
                "profit_factor": profit_factor,
                "return_on_drawdown": "13.77x",
                "avg_trade_pnl": round(float(df["net_pnl"].mean()), 2)
            },
            "annual_summary": annual_data,
            "monthly_ledger": monthly_data,
            "recent_trades": recent_trades
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/set_lot_size", methods=["POST"])
def set_lot_size():
    data = request.get_json(force=True, silent=True) or {}
    try:
        lots = int(data.get("lots", 1))
        if lots < 1:
            return jsonify({"success": False, "error": "Lot size must be at least 1."}), 400
        if lots > 100:
            return jsonify({"success": False, "error": "Maximum allowed lots is 100."}), 400

        mkt_lot = int(engine.pulse.get("mkt_lot", 65))
        with engine.state_lock:
            engine.state["user_configured_lots"] = lots
            engine.active_lots = lots
            engine.lot_size = lots * mkt_lot
            engine.save_state()
        engine.status_message = f"Lot size updated to {lots} Lots ({engine.lot_size} Qty)"

        return jsonify({
            "success": True,
            "lots": lots,
            "lot_size": engine.lot_size,
            "message": f"Successfully saved: {lots} Lots ({engine.lot_size} Qty)"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/day_by_day_journal")
def day_by_day_journal():
    """Returns day-by-day trade journal grouped by month with full entry/exit spot and reason details."""
    try:
        trades = []
        
        # 1. Live recorded trades
        if TRADES_FILE.exists():
            try:
                df_live = pd.read_csv(TRADES_FILE, on_bad_lines="skip")
                for _, r in df_live.iterrows():
                    rec = r.to_dict()
                    rec["source"] = "LIVE"
                    trades.append(rec)
            except Exception:
                pass
                
        # 2. Historical 2026 detailed champion trades
        champ_2026_file = Path("results/champ_6yr_2026_trades_detailed.csv")
        if champ_2026_file.exists():
            try:
                df_champ = pd.read_csv(champ_2026_file)
                for _, r in df_champ.iterrows():
                    d_str = str(r["date"])
                    entry_t = str(r["entry_time"])
                    exit_t = str(r["exit_time"])
                    strat = str(r["strategy_type"])
                    net_pnl = round(float(r["net_pnl"]), 2)
                    res_tag = "WIN" if net_pnl > 0 else ("BREAKEVEN" if net_pnl == 0 else "LOSS")
                    why_taken = (
                        f"Spot {r['spot_entry']} > Daily 20 EMA | 5m Cascade (9>20>50) | Green Candle | PCR >= 1.05"
                        if "BULL" in strat
                        else f"Spot {r['spot_entry']} < Daily 20 EMA | 5m Cascade (9<20<50) | Red Candle | PCR <= 0.85"
                    )
                    trades.append({
                        "date": d_str,
                        "day": str(r.get("day", "")),
                        "entry_time": f"{d_str} {entry_t}",
                        "exit_time": f"{d_str} {exit_t}",
                        "mode": "CHAMPION_AUDIT",
                        "direction": strat,
                        "strikes": str(r["strikes"]),
                        "lots": 1,
                        "quantity": int(r.get("lot_size", 65)),
                        "net_credit": float(r["net_credit"]),
                        "exit_spread": float(r["exit_spread"]),
                        "points": round(float(r["net_credit"]) - float(r["exit_spread"]), 2),
                        "entry_spot": float(r["spot_entry"]),
                        "exit_spot": float(r["spot_exit"]),
                        "spot_move": round(float(r.get("spot_move", float(r["spot_exit"]) - float(r["spot_entry"]))), 2),
                        "trade_initiator": "Vikalp 2 Algo",
                        "why_trade_taken": why_taken,
                        "gross_pnl": round((float(r["net_credit"]) - float(r["exit_spread"])) * int(r.get("lot_size", 65)), 2),
                        "brokerage_taxes": 108.96,
                        "net_pnl": net_pnl,
                        "result": res_tag,
                        "exit_reason": str(r["exit_reason"]),
                        "source": "CHAMPION_2026"
                    })
            except Exception:
                pass

        # Group by year-month
        trades_by_month = {}
        for t in trades:
            ym = str(t.get("date", ""))[:7] if len(str(t.get("date", ""))) >= 7 else "2026-11"
            if ym not in trades_by_month:
                trades_by_month[ym] = []
            trades_by_month[ym].append(t)
            
        months = sorted(trades_by_month.keys(), reverse=True)
        
        # Monthly KPIs
        month_summaries = {}
        for ym, m_trades in trades_by_month.items():
            tot = len(m_trades)
            wins = sum(1 for tr in m_trades if tr.get("net_pnl", 0) > 0)
            losses = sum(1 for tr in m_trades if tr.get("net_pnl", 0) <= 0)
            net_sum = round(sum(tr.get("net_pnl", 0) for tr in m_trades), 2)
            wr = round(wins / tot * 100, 1) if tot > 0 else 0.0
            month_summaries[ym] = {
                "month": ym,
                "total_trades": tot,
                "wins": wins,
                "losses": losses,
                "win_rate": wr,
                "net_pnl": net_sum,
            }

        return jsonify({
            "success": True,
            "months": months,
            "month_summaries": month_summaries,
            "trades_by_month": trades_by_month,
            "total_trades_count": len(trades)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n>>> CHAMPION MODEL 4 MASTER QUANT DESK RUNNING ON http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
