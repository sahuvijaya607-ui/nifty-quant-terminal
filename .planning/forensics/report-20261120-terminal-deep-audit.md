# GSD Forensic Investigation Report: Top 5 Real Production Vulnerabilities

**Generated:** 2026-11-20T01:14:00+05:30  
**Investigator:** Antigravity Autonomous Quant Systems Auditor  
**Trigger Query:** `/gsd-forensics ab puar terminal ko dekhgo or find out kro or kuch dikkat hia jo treal hai iiusse hai top 5 btao issures jo real mien isssue de skti hai`  
**Target Environment:** MasterQuantEngine (`app.py`), Render Production (`nifty-quant-terminal.onrender.com`), Kotak Neo Broker API

---

## Executive Summary: System Health & Threat Model

While today's 10 major architectural bugs (stat 'ok' amnesia, missing auto-rollback, ITBEES equity confusion, lack of isolation, Net margin reading) were successfully audited, resolved, and verified (`ALL 10 POINTS AUDITED & VERIFIED 100% OPERATIONAL`), a rigorous line-by-line forensic scan of the entire production pipeline revealed **5 remaining REAL-WORLD VULNERABILITIES** that could cause operational friction or trade execution anomalies in live market conditions.

---

## The Top 5 Real Production Issues

### 🔴 Issue 1: Kotak Neo Historical Data API 429 Rate Limiting & EMA Drift
* **Forensic Evidence:** Direct API probe to `self.client.historical_data(neosymbol="nse_cm|Nifty 50", interval="5min", ...)` returned `{"status_code": 429, "error": "Rate limit exceeded"}`.
* **Mechanism of Failure:** When `update_real_emas_from_candles()` gets rate-limited by Kotak Securities, `candles_5m` and `candles_d` are empty. The engine is forced to fall back on hardcoded constants (`base_e9 = 25261.28`, `base_e20 = 25240.97`, `base_e50 = 25234.28`, `base_daily = 24483.43`).
* **Real-World Impact:** If NIFTY trends significantly higher or lower, the calculated 5-minute EMAs and Daily 20 EMA will remain anchored to the fallback prices, delaying or distorting the 6-Rule Gate signal.
* **Remediation:** Implement local persistent caching of the Daily 20 EMA, and accumulate 5-minute candle closes in memory from real-time spot updates rather than repeatedly hammering the fragile historical data endpoint.

---

### 🟠 Issue 2: Render Container Spin-Down / Cold-Start Latency (09:15 AM Opening)
* **Forensic Evidence:** Render free/starter tiers enter dormancy (spin-down) after 15 minutes of HTTP inactivity.
* **Mechanism of Failure:** Between market close (15:30 IST) and market open (09:15 IST next day), no incoming web requests hit the terminal. The container sleeps. A cold-start wake-up takes 50–90 seconds.
* **Real-World Impact:** If the user does not open the Cockpit before 09:15 AM, the first 1-2 minutes of the market open (09:15 to 09:17) can suffer delayed token handshake or missed opening candles.
* **Remediation:** 
  1. Add an external health-check ping (e.g., UptimeRobot, cron scheduler) every 10 minutes.
  2. Open the Cockpit web page at 09:10 AM to guarantee a warmed-up container before market open.

---

### 🟡 Issue 3: Kotak Neo 24-Hour Auth Token Expiry & Midnight Handshake Desync
* **Forensic Evidence:** Kotak Neo invalidates session tokens every 24 hours (overnight maintenance).
* **Mechanism of Failure:** While `poll_kotak_neo()` catches HTTP 401 and calls `authenticate()`, if the container is running continuously, the background loop might encounter a brief window where requests return 401 until the next poll cycle.
* **Real-World Impact:** If an entry signal occurs exactly as the token expires, order placement could return `ST_CODE: 100008` (Unauthorized) before re-authentication completes.
* **Remediation:** Ensure `authenticate()` is proactively called during pre-market (09:10 AM IST) in the Watchdog daemon loop rather than waiting for an API error.

---

### 🟡 Issue 4: Holiday Expiry Shift vs Fixed Day Name Calculation
* **Forensic Evidence:** Kotak Neo official expiry list returned `['2026-11-23', ...]` (Monday), while `get_next_tuesday_expiry()` calculated `2026-11-24` (Tuesday).
* **Mechanism of Failure:** When an official trading holiday falls on a Tuesday or Thursday, NSE pre-pones the contract expiry to the previous business day (Monday/Wednesday).
* **Real-World Impact:** If code relies on `get_next_tuesday_expiry()` instead of reading `chain["common_data"]["expiryDt"]`, it could search for a non-existent date contract.
* **Remediation:** Always prioritize `self.pulse["next_expiry"]` obtained directly from `chain["common_data"]["expiryDt"]` and deprecate rigid weekday-based date generators.

---

### 🟢 Issue 5: Square-Off `MKT` Rejection Risk on Fast Option Spikes
* **Forensic Evidence:** In `close_trade()`, exit orders currently use `order_type="MKT", price="0"`.
* **Mechanism of Failure:** On high-volatility days (VIX > 18) or sudden momentum candles, NSE occasionally rejects pure `MKT` orders for index options with the error *"Market orders not allowed for options, use limit orders"*.
* **Real-World Impact:** If the short leg buyback market order is rejected, the trade might not exit at the exact target or stop loss price.
* **Remediation:** Apply the same dual-tier execution pattern used in Auto-Rollback: attempt `order_type="MKT"`, and if rejected, immediately fallback to an Aggressive Marketable Limit Order (`LTP * 1.05` for buying back short, `LTP * 0.90` for selling long wing).
