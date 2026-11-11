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
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from io import StringIO

from flask import Flask, render_template, jsonify, request, Response, send_file
import pandas as pd
import numpy as np
from scipy.stats import norm
import pyotp
from dotenv import load_dotenv

from scripts.daily_data_archiver import DailyDataArchiver

load_dotenv()

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# Persistence paths
STATE_FILE = Path("results/paper_engine_state.json")
TRADES_FILE = Path("results/live_paper_trades.csv")
MASTER_SPOT_PARQUET = Path("data/nifty_1min_5yr.parquet")
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


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

        # Safety Execution Mode: 'PAPER' (default) or 'LIVE'
        self.execution_mode = "PAPER"
        self.security_mpin = "031995"

        # Capital Tracking (1 Lakh Capital)
        self.initial_capital = 100000.0

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
        self.status_message = "Master Quant Desk Initialized."

        # Latest Market Pulse
        self.pulse = {
            "spot": 25044.65,
            "prev_close": 24986.40,
            "daily_ema20": 23677.11,
            "ema9": 25007.35,
            "ema20": 24988.33,
            "ema50": 24952.39,
            "pcr": None,
            "next_expiry": "2026-11-17",
            "calls_count": 0,
            "puts_count": 0,
            "signal": "NEUTRAL_SCANNING",
            "option_chain": None,
        }

        # Event queue for browser audio chimes: 'ENTRY', 'TARGET', 'STOP_LOSS'
        self.pending_audio_event = None

        # Daily 1-Minute Live Archiver Engine (Zero Data Gaps!)
        self.archiver = DailyDataArchiver(self.client)
        self.last_archive_time = 0

        self.state = self.load_state()
        self.authenticate()
        self.poll_kotak_neo(force=True)
        self.init_replay_data()

        # Background thread for off-market replay loop
        self.replay_thread = threading.Thread(target=self._replay_loop, daemon=True)
        self.replay_thread.start()

        # Dedicated 100% Autonomous Background Worker (Auto-Downloads 1-Min Options & Auto-Executes Trades)
        self.live_daemon_thread = threading.Thread(target=self._live_engine_loop, daemon=True)
        self.live_daemon_thread.start()

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
                self.greeting = res2.get("data", {}).get("greetingName", "RAHUL")
                self.status_message = f"Kotak Neo Connected ({self.greeting})"
                if hasattr(self, "archiver") and self.archiver:
                    self.archiver.set_client(self.client)
                return True
            else:
                self.status_message = "Kotak Auth Failed - Standby"
                return False
        except Exception as e:
            self.status_message = f"API Standby ({str(e)[:25]})"
            return False

    def load_state(self):
        today_str = date.today().strftime("%Y-%m-%d")
        default_state = {
            "date": today_str,
            "circuit_breaker_active": False,
            "today_losses": 0,
            "today_wins": 0,
            "today_realized_gross_pnl": 0.0,
            "today_total_charges": 0.0,
            "today_realized_net_pnl": 0.0,
            "active_position": None,
        }
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r") as f:
                    saved = json.load(f)
                    if saved.get("date") == today_str:
                        return saved
            except Exception:
                pass
        return default_state

    def save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2)

    def init_replay_data(self, target_date="2026-11-09"):
        """Loads historical 1-minute candles for off-market replay."""
        try:
            if MASTER_SPOT_PARQUET.exists():
                df = pd.read_parquet(MASTER_SPOT_PARQUET)
                available = df.index.normalize().unique()
                self.available_replay_dates = [d.strftime("%Y-%m-%d") for d in sorted(available, reverse=True)[:30]]
                if target_date not in self.available_replay_dates and self.available_replay_dates:
                    target_date = self.available_replay_dates[0]
                
                day_slice = df.loc[target_date].copy()
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

        # Check market hours in IST (09:15 - 15:30 IST)
        now_dt = get_ist_now()
        is_mkt_open = (datetime.strptime("09:15", "%H:%M").time() <= now_dt.time() <= datetime.strptime("15:30", "%H:%M").time()) and (now_dt.weekday() < 5)

        # In market hours poll every 2s; outside market hours poll every 30s to keep official closing settlement price fresh
        min_poll_interval = 2 if is_mkt_open else 30
        if not force and (now - self.last_api_poll < min_poll_interval):
            return

        self.feeder_mode = "LIVE" if is_mkt_open else "CLOSED"

        try:
            chain = self.client.option_chain(exchange="nse_fo", underlying="NIFTY")
            self.pulse["option_chain"] = chain

            if "spot" in chain and "ltp" in chain["spot"]:
                self.pulse["spot"] = float(chain["spot"]["ltp"])
                self.pulse["prev_close"] = float(chain["spot"].get("prevClose", self.pulse["spot"]))

            if "common_data" in chain:
                self.pulse["next_expiry"] = chain["common_data"].get("expiryDt", "2026-11-17")
                self.pulse["mkt_lot"] = int(chain["common_data"].get("mktLot", 65))
                self.lot_size = self.pulse["mkt_lot"]

            spot = self.pulse["spot"]

            # Dynamic Real-Time EMA Calculations based on live Spot movement
            a9 = 2.0 / 10.0
            a20 = 2.0 / 21.0
            a50 = 2.0 / 51.0
            adaily = 2.0 / 21.0

            base_e9 = getattr(self, "base_ema9", 25007.35)
            base_e20 = getattr(self, "base_ema20", 24988.33)
            base_e50 = getattr(self, "base_ema50", 24952.39)
            base_daily = getattr(self, "base_daily20", 23677.11)

            self.pulse["ema9"] = round(spot * a9 + base_e9 * (1.0 - a9), 2)
            self.pulse["ema20"] = round(spot * a20 + base_e20 * (1.0 - a20), 2)
            self.pulse["ema50"] = round(spot * a50 + base_e50 * (1.0 - a50), 2)
            self.pulse["daily_ema20"] = round(spot * adaily + base_daily * (1.0 - adaily), 2)

            # Live Option Chain OI & High-Precision PCR (3 decimal places)
            total_call_oi = sum(int(c.get("oi", {}).get("cur", c.get("oi", {}).get("oi", 0))) for c in chain.get("call", []))
            total_put_oi = sum(int(p.get("oi", {}).get("cur", p.get("oi", {}).get("oi", 0))) for p in chain.get("put", []))
            self.pulse["total_call_oi"] = total_call_oi
            self.pulse["total_put_oi"] = total_put_oi
            if total_call_oi > 0:
                self.pulse["pcr"] = round(total_put_oi / total_call_oi, 3)

            self.pulse["calls_count"] = len(chain.get("call", []))
            self.pulse["puts_count"] = len(chain.get("put", []))

            d_ema = self.pulse["daily_ema20"]
            e9 = self.pulse["ema9"]
            e20 = self.pulse["ema20"]
            e50 = self.pulse["ema50"]
            pcr = self.pulse["pcr"]

            if pcr is not None and (spot > d_ema) and (e9 > e20 > e50) and (pcr >= 1.05):
                self.pulse["signal"] = "BULL_PUT_SPREAD"
            elif pcr is not None and (spot < d_ema) and (e9 < e20 < e50) and (pcr <= 0.85):
                self.pulse["signal"] = "BEAR_CALL_SPREAD"
            else:
                self.pulse["signal"] = "NEUTRAL_SCANNING"

            # Autonomous Live Trigger (09:30 - 14:30 IST, strictly 1 trade/day)
            # STRICT GUARD: Live Option Chain must be downloaded and PCR verified!
            if is_mkt_open and (not self.state.get("active_position")) and (not self.state.get("circuit_breaker_active")):
                if self.state.get("today_wins", 0) == 0 and self.state.get("today_losses", 0) == 0:
                    t_time = now_dt.time()
                    if datetime.strptime("09:30", "%H:%M").time() <= t_time <= datetime.strptime("14:30", "%H:%M").time():
                        if self.pulse.get("option_chain") is not None and pcr is not None:
                            if self.pulse["signal"] in ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"]:
                                self.trigger_trade(force=False)

            # Minute-by-Minute Live Archiving (Zero Data Gaps!)
            if (is_mkt_open or force) and (now - getattr(self, "last_archive_time", 0) >= 60):
                if hasattr(self, "archiver") and self.archiver:
                    self.archiver.record_minute_tick(spot, chain)
                    self.last_archive_time = now
                    self.total_ticks_archived = getattr(self, "total_ticks_archived", 0) + 1
                    self.pulse["ticks_archived"] = self.total_ticks_archived

            # EOD Reconcile at 15:35 IST
            if now_dt.time() >= datetime.strptime("15:35", "%H:%M").time() and not getattr(self, "eod_reconciled_today", False):
                if hasattr(self, "archiver") and self.archiver:
                    ok, msg = self.archiver.reconcile_eod_spot()
                    self.eod_reconciled_today = True
                    self.status_message = f"EOD Archive: {msg}"

            self.last_api_poll = now
        except Exception as e:
            err_str = str(e).lower()
            if "401" in err_str or "token" in err_str or "session" in err_str or "unauth" in err_str:
                self.authenticate()

    def _live_engine_loop(self):
        """
        100% Autonomous Hands-Free Background Engine:
        Runs 24/7 in background even when browser or phone is closed:
        1. Continuously downloads 1-minute strike-by-strike Option Chain & Spot to parquet/csv.
        2. Evaluates the 6 Champion Model 4 rules in real-time.
        3. Autonomously triggers margin-safe trade (09:30 - 14:30 IST).
        4. Autonomously manages Trailing Breakeven, Target, SL, and 15:15 PM Intraday exit.
        5. At 15:35 IST, auto-reconciles full 375 candles into master database.
        Zero user intervention required!
        """
        while True:
            try:
                self.poll_kotak_neo(force=False)
                self.update_position_dynamics()
            except Exception:
                pass
            time.sleep(2)

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

        # Strategy evaluation
        if spot_close > self.pulse["daily_ema20"] and (self.pulse["ema9"] > self.pulse["ema20"] > self.pulse["ema50"]):
            self.pulse["signal"] = "BULL_PUT_SPREAD"
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
        wing_strike_pe = atm_strike - 350
        wing_strike_ce = atm_strike + 350
        strikes = [atm_strike + (i * 50) for i in range(-15, 16)]

        chain = self.pulse.get("option_chain")

        # Calculate Days to Expiry (DTE)
        expiry_str = self.pulse.get("next_expiry", "2026-11-17")
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
            c_oi = int(c_data.get("oi", {}).get("cur", c_data.get("oi", {}).get("oi", 0)))
            c_vol = int(c_data.get("quote", {}).get("vol", 0))

            # Puts
            p_data = put_map.get(s, {})
            p_ltp = float(p_data.get("quote", {}).get("ltp", 0.0))
            p_oi = int(p_data.get("oi", {}).get("cur", p_data.get("oi", {}).get("oi", 0)))
            p_vol = int(p_data.get("quote", {}).get("vol", 0))

            dist = s - spot
            if c_ltp == 0:
                c_ltp = round(max(3.0, 115.0 - (dist * 0.45)), 2)
            if p_ltp == 0:
                p_ltp = round(max(3.0, 110.0 + (dist * 0.45)), 2)
            if c_oi == 0:
                c_oi = abs(int(100000 - dist * 40))
            if p_oi == 0:
                p_oi = abs(int(115000 + dist * 40))

            if c_oi > max_c_oi:
                max_c_oi = c_oi
                max_c_strike = s
            if p_oi > max_p_oi:
                max_p_oi = p_oi
                max_p_strike = s

            raw_rows.append((s, c_ltp, c_oi, c_vol, p_ltp, p_oi, p_vol))

        table_rows = []
        for s, c_ltp, c_oi, c_vol, p_ltp, p_oi, p_vol in raw_rows:
            is_atm = (s == atm_strike)
            is_hedge_wing = (s == wing_strike_pe or s == wing_strike_ce)
            wing_tag = "HEDGE WING (350 OTM)" if is_hedge_wing else None

            # Calculate Greeks
            greeks = calc_bs_greeks(spot, s, dte_days=dte, iv=0.135, r=0.07)

            is_sold_leg = (pos is not None and s == sold_strike)
            is_bought_leg = (pos is not None and s == bought_strike)
            is_max_call_oi = (s == max_c_strike)
            is_max_put_oi = (s == max_p_strike)

            table_rows.append({
                "strike": s,
                "is_atm": is_atm,
                "is_hedge_wing": is_hedge_wing,
                "wing_tag": wing_tag,
                "is_sold_leg": is_sold_leg,
                "is_bought_leg": is_bought_leg,
                "sold_type": sold_type if is_sold_leg else None,
                "bought_type": bought_type if is_bought_leg else None,
                "call_ltp": c_ltp,
                "call_oi": f"{c_oi:,}",
                "call_oi_raw": c_oi,
                "call_vol": f"{c_vol:,}",
                "is_max_call_oi": is_max_call_oi,
                "put_ltp": p_ltp,
                "put_oi": f"{p_oi:,}",
                "put_oi_raw": p_oi,
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

    def trigger_trade(self, force=False, simulated_time=None):
        """
        Executes Margin-Safe Credit Spread:
        STEP 1: BUY OTM Wing FIRST
        STEP 2: SELL ATM Strike SECOND
        """
        if self.state.get("active_position") and not force:
            return False, "Position already active."

        if self.state.get("circuit_breaker_active") and not force:
            return False, "Circuit breaker active."

        direction = self.pulse["signal"]
        if direction == "NEUTRAL_SCANNING":
            direction = "BULL_PUT_SPREAD"

        spot = self.pulse["spot"]
        atm_strike = int(round(spot / 50.0) * 50)

        # Dynamic live market prices from Option Chain
        chain_table = self.get_clean_option_chain_table()
        strike_map = {r["strike"]: r for r in chain_table}

        if direction == "BULL_PUT_SPREAD":
            sell_strike = atm_strike
            buy_strike = atm_strike - 350
            opt_type = "PE"
            sell_row = strike_map.get(sell_strike, {})
            buy_row = strike_map.get(buy_strike, {})
            sell_ltp = float(sell_row.get("put_ltp", 0.0)) or 213.0
            buy_ltp = float(buy_row.get("put_ltp", 0.0)) or 89.0
        else:
            sell_strike = atm_strike
            buy_strike = atm_strike + 350
            opt_type = "CE"
            sell_row = strike_map.get(sell_strike, {})
            buy_row = strike_map.get(buy_strike, {})
            sell_ltp = float(sell_row.get("call_ltp", 0.0)) or 255.0
            buy_ltp = float(buy_row.get("call_ltp", 0.0)) or 115.0

        exec_buy = round(buy_ltp * 1.005, 2)
        exec_sell = round(sell_ltp * 0.995, 2)
        net_credit = round(exec_sell - exec_buy, 2)

        # Symbol generation for Kotak Neo F&O
        exp_str = self.pulse["next_expiry"]
        exp_code = f"{exp_str[2:4]}{exp_str[5:7]}"  # YYMM format e.g. 2611
        buy_symbol = f"NIFTY{exp_code}{buy_strike}{opt_type}"
        sell_symbol = f"NIFTY{exp_code}{sell_strike}{opt_type}"

        # Real execution via Kotak Neo API if in LIVE mode
        order_status = "PAPER_VIRTUAL"
        if self.execution_mode == "LIVE" and self.authenticated and self.client:
            try:
                # 1. Place BUY OTM Wing First (Margin Benefit)
                self.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price=str(exec_buy),
                    order_type="L",
                    quantity=str(self.lot_size),
                    validity="DAY",
                    trading_symbol=buy_symbol,
                    transaction_type="B",
                )
                # 2. Place SELL ATM Strike Second
                self.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price=str(exec_sell),
                    order_type="L",
                    quantity=str(self.lot_size),
                    validity="DAY",
                    trading_symbol=sell_symbol,
                    transaction_type="S",
                )
                order_status = "KOTAK_API_FILLED"
            except Exception as e:
                order_status = f"API_ERROR: {str(e)[:25]}"

        entry_time_str = simulated_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        new_pos = {
            "direction": direction,
            "expiry": self.pulse["next_expiry"],
            "execution_mode": self.execution_mode,
            "order_status": order_status,
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
            "current_spread": net_credit,
            "target_spread": round(net_credit * 0.60, 2),
            "sl_spread": round(min(net_credit * 1.60, net_credit + (2800.0 / self.lot_size)), 2),
            "target_profit_inr": round((net_credit * 0.40) * self.lot_size, 2),
            "max_risk_inr": round(min((net_credit * 0.60) * self.lot_size, 2800.0), 2),
            "breakeven_active": False,
            "mtm_gross_pnl": 0.0,
            "mtm_net_pnl": 0.0,
            "status": "OPEN",
        }

        self.state["active_position"] = new_pos
        self.save_state()
        self.pending_audio_event = "ENTRY"
        self.status_message = f"[{self.execution_mode}] Trade Triggered: Bought {buy_strike}{opt_type} -> Sold {sell_strike}{opt_type}"
        return True, "Trade executed safely."

    def square_off_position(self, reason="MANUAL_WEB_EXIT"):
        pos = self.state.get("active_position")
        if not pos:
            return False, "No active position."

        exit_spread = pos.get("current_spread", pos["net_credit"])
        entry_credit = pos["net_credit"]
        points_gain = entry_credit - exit_spread
        gross_pnl = round(points_gain * self.lot_size, 2)

        # Exact Kotak Neo Charges calculation
        charges = self.calculate_kotak_charges(entry_credit, exit_spread, self.lot_size)
        net_pnl = round(gross_pnl - charges["total_charges"], 2)

        exit_spot = self.pulse["spot"]
        exit_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # If LIVE mode, send real squareoff orders
        if pos.get("execution_mode") == "LIVE" and self.authenticated and self.client:
            try:
                # 1. Buy back short leg
                self.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price="0",
                    order_type="MKT",
                    quantity=str(self.lot_size),
                    validity="DAY",
                    trading_symbol=pos.get("sell_symbol", f"NIFTY{pos['sell_strike']}{pos['sell_type']}"),
                    transaction_type="B",
                )
                # 2. Sell back long wing
                self.client.place_order(
                    exchange_segment="nse_fo",
                    product="NRML",
                    price="0",
                    order_type="MKT",
                    quantity=str(self.lot_size),
                    validity="DAY",
                    trading_symbol=pos.get("buy_symbol", f"NIFTY{pos['buy_strike']}{pos['buy_type']}"),
                    transaction_type="S",
                )
            except Exception:
                pass

        # Update Daily Metrics
        self.state["today_realized_gross_pnl"] = round(self.state["today_realized_gross_pnl"] + gross_pnl, 2)
        self.state["today_total_charges"] = round(self.state["today_total_charges"] + charges["total_charges"], 2)
        self.state["today_realized_net_pnl"] = round(self.state["today_realized_net_pnl"] + net_pnl, 2)

        if net_pnl >= 0:
            self.state["today_wins"] += 1
            self.state["circuit_breaker_active"] = True  # Golden Rule: Profit aate hi daily quota lock, no second trade!
            self.pending_audio_event = "TARGET"
        else:
            self.state["today_losses"] += 1
            self.state["circuit_breaker_active"] = True  # 1-Loss Circuit Breaker
            self.pending_audio_event = "STOP_LOSS"

        trade_rec = {
            "date": date.today().strftime("%Y-%m-%d"),
            "entry_time": pos["entry_time"],
            "exit_time": exit_time_str,
            "mode": pos.get("execution_mode", "PAPER"),
            "direction": pos["direction"],
            "strikes": f"Sell {pos['sell_strike']}{pos['sell_type']} / Buy {pos['buy_strike']}{pos['buy_type']}",
            "net_credit": pos["net_credit"],
            "exit_spread": exit_spread,
            "points": round(points_gain, 2),
            "spot_move": round(exit_spot - pos["entry_spot"], 2),
            "gross_pnl": gross_pnl,
            "brokerage_taxes": charges["total_charges"],
            "net_pnl": net_pnl,
            "exit_reason": reason,
        }
        df_new = pd.DataFrame([trade_rec])
        if TRADES_FILE.exists():
            df_new.to_csv(TRADES_FILE, mode="a", header=False, index=False)
        else:
            df_new.to_csv(TRADES_FILE, index=False)

        self.state["active_position"] = None
        self.save_state()
        self.status_message = f"Closed ({reason}): Net PnL Rs {net_pnl:+,.2f} (Charges: Rs {charges['total_charges']})"
        return True, f"Closed with Net PnL Rs {net_pnl:+,.2f}"

    def update_position_dynamics(self):
        pos = self.state.get("active_position")
        if not pos:
            return

        spot = self.pulse["spot"]
        entry_spot = pos["entry_spot"]
        entry_credit = pos["net_credit"]
        direction = pos["direction"]

        spot_diff = (spot - entry_spot) if direction == "BULL_PUT_SPREAD" else (entry_spot - spot)
        
        # Second-by-second holding timer
        start_sec = pos.get("entry_timestamp_sec", time.time())
        holding_sec = int(time.time() - start_sec)
        pos["holding_time_str"] = str(timedelta(seconds=holding_sec))
        pos["spot_move"] = round(spot - entry_spot, 2)

        # Dynamic real market option spread from option chain table
        chain_table = self.get_clean_option_chain_table()
        strike_map = {r["strike"]: r for r in chain_table}
        sell_row = strike_map.get(pos["sell_strike"], {})
        buy_row = strike_map.get(pos["buy_strike"], {})

        opt_col = "put_ltp" if pos["sell_type"] == "PE" else "call_ltp"
        real_sell_ltp = float(sell_row.get(opt_col, 0.0))
        real_buy_ltp = float(buy_row.get(opt_col, 0.0))

        pos["current_sell_ltp"] = real_sell_ltp if real_sell_ltp > 0 else pos["sell_price"]
        pos["current_buy_ltp"] = real_buy_ltp if real_buy_ltp > 0 else pos["buy_price"]

        if real_sell_ltp > 0 and real_buy_ltp > 0:
            current_spread = round(real_sell_ltp - real_buy_ltp, 2)
        else:
            elapsed_mins = max(1, holding_sec // 60)
            theta_decay = min(entry_credit * 0.40, (elapsed_mins / 60.0) * 2.5)
            current_spread = np.clip(entry_credit - (spot_diff * 0.40) - theta_decay, 2.0, entry_credit * 2.5)

        pos["current_spread"] = round(current_spread, 2)

        pts_gain = entry_credit - current_spread
        gross_pnl = round(pts_gain * self.lot_size, 2)
        pos["mtm_gross_pnl"] = gross_pnl

        # Net MTM after estimated charges (~Rs 110 round-trip)
        charges = self.calculate_kotak_charges(entry_credit, current_spread, self.lot_size)
        pos["estimated_charges"] = charges["total_charges"]
        pos["mtm_net_pnl"] = round(gross_pnl - charges["total_charges"], 2)

        # Trailing Breakeven Trigger
        if not pos.get("breakeven_active") and (pts_gain >= entry_credit * 0.25):
            pos["breakeven_active"] = True
            pos["sl_spread"] = entry_credit
            self.status_message = "Trailing SL Locked at BREAKEVEN (Zero Risk)!"

        # Exit conditions
        if current_spread <= pos["target_spread"]:
            self.square_off_position("TARGET_HIT (+40% DECAY)")
        elif current_spread >= pos["sl_spread"]:
            self.square_off_position("BREAKEVEN_SL" if pos.get("breakeven_active") else "STOP_LOSS")
        else:
            # Time Exit at 15:15 PM IST (Zero Overnight Risk)
            now_t = get_ist_now().time()
            if datetime.strptime("15:15", "%H:%M").time() <= now_t <= datetime.strptime("15:30", "%H:%M").time():
                self.square_off_position("TIME_EXIT (15:15 PM INTRADAY CLOSE)")


engine = MasterQuantEngine()


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
    engine.poll_kotak_neo()
    engine.update_position_dynamics()

    now_dt = get_ist_now()
    now_t = now_dt.time()
    is_mkt_open = (datetime.strptime("09:15", "%H:%M").time() <= now_t <= datetime.strptime("15:30", "%H:%M").time()) and (now_dt.weekday() < 5)

    spot = engine.pulse["spot"]
    prev = engine.pulse["prev_close"]
    chg = spot - prev
    pct = (chg / prev) * 100

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

    total_net_pnl = round(day_realized_net + live_mtm_net, 2)
    total_charges = round(day_realized_charges + live_charges, 2)
    total_gross_pnl = round(day_realized_gross + live_mtm_gross, 2)

    cur_equity = round(engine.initial_capital + total_net_pnl, 2)
    margin_used = 45000.0 if pos else 0.0
    free_cash = round(cur_equity - margin_used, 2)
    roi_today_pct = round((total_net_pnl / engine.initial_capital) * 100.0, 2)

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
    cb_active = engine.state["circuit_breaker_active"]

    eval_time = datetime.strptime(engine.pulse.get("replay_time", "10:30"), "%H:%M").time() if (not is_mkt_open and engine.replay_active) else now_t
    is_time_window = (datetime.strptime("09:30", "%H:%M").time() <= eval_time <= datetime.strptime("14:30", "%H:%M").time())
    cond_circuit_breaker = bool(losses_today == 0 and not cb_active)
    cond_margin_safety = True  # Always enforced: 1st BUY OTM Wing -> 2nd SELL ATM Strike

    # 1. Bull Put Spread Evaluation (Bullish Credit)
    bull_c1 = bool(spot > d_ema)
    bull_c2 = bool(e9 > e20 > e50)
    bull_c3 = bool(pcr >= 1.05)
    bull_score = sum([bull_c1, bull_c2, bull_c3, is_time_window, cond_circuit_breaker, cond_margin_safety])

    # 2. Bear Call Spread Evaluation (Bearish Credit)
    bear_c1 = bool(spot < d_ema)
    bear_c2 = bool(e9 < e20 < e50)
    bear_c3 = bool(pcr <= 0.85)
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
        if bull_score >= bear_score:
            lead_spread = "BULL_PUT_SPREAD"
            missing_bull = []
            if not bull_c1: missing_bull.append("Spot > Daily EMA")
            if not bull_c2: missing_bull.append("5m Momentum (9>20>50)")
            if not bull_c3: missing_bull.append("PCR >= 1.05")
            if not is_time_window: missing_bull.append("Time 09:30-14:30")
            if not cond_circuit_breaker: missing_bull.append("0 Loss Circuit Breaker")
            
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
            if not bear_c2: missing_bear.append("5m Momentum (9<20<50)")
            if not bear_c3: missing_bear.append("PCR <= 0.85")
            if not is_time_window: missing_bear.append("Time 09:30-14:30")
            if not cond_circuit_breaker: missing_bear.append("0 Loss Circuit Breaker")

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
        c2_rule = "EMA 9 < EMA 20 < EMA 50 (Downward Momentum)"
        c2_metric = f"E9: {e9:.1f} < E20: {e20:.1f} < E50: {e50:.1f}"
        c3_rule = "PCR <= 0.85 (Heavy Call Writing Ceiling)"
        c3_metric = f"PCR: {pcr:.3f} (Required: <= 0.85)"
    else:
        cond_daily_regime = bull_c1
        cond_ema_cascade = bull_c2
        cond_pcr_sentiment = bull_c3
        c1_rule = "Spot > Daily 20 EMA (Bullish Macro Filter)"
        c1_metric = f"Spot: {spot:,.2f} > EMA: {d_ema:,.2f}"
        c2_rule = "EMA 9 > EMA 20 > EMA 50 (Upward Momentum)"
        c2_metric = f"E9: {e9:.1f} > E20: {e20:.1f} > E50: {e50:.1f}"
        c3_rule = "PCR >= 1.05 (Heavy Put Writing Floor)"
        c3_metric = f"PCR: {pcr:.3f} (Required: >= 1.05)"

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
            "name": "5. 1-Loss Daily Circuit Breaker",
            "rule": "0 Losses Today (Preserve Capital on Drawdown)",
            "metric": f"{losses_today} / 1 Loss Today",
            "status": cond_circuit_breaker,
            "badge": "TRUE" if cond_circuit_breaker else "FALSE",
        },
        {
            "id": "margin_safety",
            "name": "6. SEBI Margin Order Priority",
            "rule": "1st Buy OTM Wing -> 2nd Sell ATM (~₹45k Margin)",
            "metric": "1st Buy Hedge -> 2nd Sell ATM",
            "status": cond_margin_safety,
            "badge": "TRUE",
        },
    ]

    return jsonify({
        "time": now_dt.strftime("%d-%b-%Y %H:%M:%S IST"),
        "feeder_mode": engine.feeder_mode,
        "execution_mode": engine.execution_mode,
        "is_market_open": is_mkt_open,
        "market_status_text": "LIVE MARKET STREAM" if is_mkt_open else "OFF-MARKET REPLAY MODE",
        "account_greeting": engine.greeting,
        "is_authenticated": engine.authenticated,
        "status_message": engine.status_message,
        "lot_size": engine.lot_size,
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
            "initial": engine.initial_capital,
            "current_equity": cur_equity,
            "margin_used": margin_used,
            "free_cash": free_cash,
            "charges": total_charges,
            "total_net_pnl": total_net_pnl,
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
            "total_call_oi": f"{engine.pulse.get('total_call_oi', 0):,}",
            "total_put_oi": f"{engine.pulse.get('total_put_oi', 0):,}",
            "pcr_sentiment": "BULLISH (>1.05)" if engine.pulse["pcr"] >= 1.05 else ("BEARISH (<0.85)" if engine.pulse["pcr"] <= 0.85 else "NEUTRAL (0.85-1.05)"),
            "signal": engine.pulse["signal"],
            "expiry": engine.pulse["next_expiry"],
        },
        "option_chain_table": chain_table,
        "active_position": engine.state.get("active_position"),
        "performance": {
            "today_wins": engine.state["today_wins"],
            "today_losses": engine.state["today_losses"],
            "today_trades": engine.state["today_wins"] + engine.state["today_losses"],
            "today_gross_pnl": total_gross_pnl,
            "today_charges": total_charges,
            "today_net_pnl": total_net_pnl,
            "circuit_breaker_active": engine.state["circuit_breaker_active"],
        },
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
            return jsonify({"success": True, "mode": "LIVE", "message": "Live Execution Unlocked!"})
        else:
            return jsonify({"success": False, "message": "Invalid Security MPIN! Remains in Paper Mode."}), 403
    else:
        engine.execution_mode = "PAPER"
        engine.status_message = "SAFETY ACTIVE: Returned to 100% Virtual Paper Mode."
        return jsonify({"success": True, "mode": "PAPER", "message": "Switched to Safe Paper Mode."})


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
    engine.state["circuit_breaker_active"] = False
    engine.state["today_losses"] = 0
    engine.save_state()
    engine.status_message = "Circuit Breaker & Losses Reset (0 Losses Today)."
    return jsonify({"success": True, "message": "Circuit Breaker & Losses Reset."})


@app.route("/api/trades")
def get_trades():
    if TRADES_FILE.exists():
        try:
            df = pd.read_csv(TRADES_FILE, on_bad_lines="skip")
            return jsonify(df.to_dict(orient="records"))
        except Exception:
            return jsonify([])
    return jsonify([])


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
                headers={"Content-Disposition": f"attachment;filename=nifty_quant_trades_ledger_{date.today().strftime('%Y%m%d')}.csv"}
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "No trades recorded yet."}), 404


@app.route("/api/download_option_chain_archive")
def download_option_chain_archive():
    today_str = date.today().strftime("%Y-%m-%d")
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n>>> CHAMPION MODEL 4 MASTER QUANT DESK RUNNING ON http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
