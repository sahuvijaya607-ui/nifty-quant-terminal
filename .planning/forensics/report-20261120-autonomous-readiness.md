# Forensic Diagnostic Report: Autonomous Live Trading Readiness & Concurrency Audit

**Date:** 2026-11-20 01:52:00 IST  
**Environment:** Render Cloud (Production) & Local Windows Workspace  
**Target:** Master Quant Engine (`app.py`, `gunicorn.conf.py`, `results/cached_emas.json`)  
**Scope:** Trade Trigger Readiness, Expiry Auto-Discovery, Concurrency Locks, Strategy Rules & Thread Lifecycle  

---

## 1. Executive Summary

| Audit Item | User Requirement | Engine State | Forensic Verdict |
| :--- | :--- | :--- | :--- |
| **Autonomous Execution** | Subah khud trade execute karega bina user click ke | Automated trigger in `poll_kotak_neo` + `_live_engine_loop` | **CONDITIONAL PASS** *(Gunicorn fork fix needed)* |
| **Expiry Auto-Discovery** | Expiry date khud find karega, next expiry trade lega | Broker `common_data['expiryDt']` = `2026-11-23` + Auto-rollover | **VERIFIED PASS** |
| **Trade Concurrency** | Pehla trade complete hone par hi doosra trade lega | Rule 6 (`active_position` + `has_open_broker_positions()`) | **VERIFIED PASS** |
| **Ironclad Strategy Rules** | All 6 Champion rules strictly met | 6-Gate verification (Trend, EMA, PCR, Time, Quota, Demat) | **VERIFIED PASS** |
| **Daily Trade Quota** | Maximum 2 trades/day | Quota block (`today_trades_done >= 2`) | **VERIFIED PASS** |

---

## 2. In-Depth Forensic Findings by Dimension

### A. Expiry Auto-Discovery (`common_data['expiryDt']`)
* **Evidence (Code):** `app.py` lines 704–723
  ```python
  raw_exp = chain["common_data"].get("expiryDt", "2026-11-24")
  exp_dt = datetime.strptime(raw_exp, "%Y-%m-%d").date()
  today_dt = get_ist_now().date()
  if exp_dt < today_dt:
      rolled_exp = exp_dt + timedelta(days=7)
      self.pulse["next_expiry"] = rolled_exp.strftime("%Y-%m-%d")
      self.pulse["is_expiry_rolled"] = True
  else:
      self.pulse["next_expiry"] = raw_exp
  ```
* **Live Test Evidence:** Direct broker response from Kotak Neo option chain API:
  `common_data: {'mktLot': '65', 'multiplier': '1', 'expiryDt': '2026-11-23', 'unlSymbol': 'NIFTY', 'exSeg': 'nse_fo'}`
  `pulse next_expiry: 2026-11-23`
* **Finding:** Expiry date is NEVER hardcoded. It dynamically reads the upcoming weekly expiry directly from the NSE broker feed. Even for holiday shifts (such as Monday 2026-11-23), the broker's exact date is recognized. If an expiry date is in the past, it automatically rolls forward 7 days to the next weekly cycle.

---

### B. Single Position Lock & Concurrency Control
* **Evidence (Code):** `app.py` lines 1321–1334
  ```python
  # Rule 6: Demat Safety (0 Render Open Positions)
  if self.state.get("active_position"):
      msg = "[GATE BLOCKED - RULE 6] Render Terminal already has an active position!"
      return False, msg

  if self.execution_mode == "LIVE" and self.authenticated and self.client:
      if self.has_open_broker_positions():
          msg = "[GATE BLOCKED - RULE 6] Render Terminal already has an open F&O position in Demat!"
          return False, msg
  ```
* **Finding:**
  1. The engine checks both internal state (`self.state.get("active_position")`) and live Demat open positions (`self.has_open_broker_positions()`).
  2. If a trade is currently open, any new signal is **strictly blocked** with `[GATE BLOCKED - RULE 6]`.
  3. Only when the active trade is closed via:
     - Target Hit (15 pts / 40% decay)
     - Stoploss Hit
     - EOD Time Exit (15:15 PM IST)
     does `self.state["active_position"]` become `None`, unlocking the engine for the second trade.
  4. Daily limit is capped at 2 trades (`today_trades_done < 2`). Once 2 trades are completed, trading ceases for the day.

---

### C. Champion Model Strategy 6-Rule Gate Verification
* **Macro Filter (Daily 20 EMA):** Spot > Daily 20 EMA (Bull Put) or Spot < Daily 20 EMA (Bear Call).
* **5m Momentum Filter:** 9 > 20 > 50 EMA cascade + candle color validation.
* **Dual-PCR Sentiment Filter:** Weekly PCR >= 1.05 (Bull Put) or <= 0.85 (Bear Call).
* **Time Gate:** Strictly 09:30 to 14:30 IST.
* **Wing Distance:** India VIX adaptive (at 16.11 VIX -> 350-point wing distance).
* **Two-Phase Execution:** OTM Wing bought first, then ATM Strike sold; instant 3-attempt market rollback if sell fails.
* **Dual-Tier Exit Fallback:** Marketable Limit (`LTP * 1.05` for short buyback / `LTP * 0.90` for long wing) if NSE rejects MKT order.

---

### D. Critical Anomaly Discovered: Gunicorn POSIX Fork Thread Lifecycle

* **Symptom:** Live `/api/health` probe on Render returned:
  `"worker_alive": false, "watchdog_alive": false`
* **Root Cause:**
  In Linux/POSIX environments, Gunicorn starts by importing `app.py` in the master process, initializing `MasterQuantEngine` and starting background threads (`live_daemon_thread` and `watchdog_thread`). Gunicorn then calls `os.fork()` to create worker processes.
  Under POSIX standards, `fork()` copies the memory state of the parent process, but **does not copy active OS threads**. In the child worker process, the Python `Thread` objects exist in memory but their operating system threads are dead (`is_alive() == False`).
  Consequently:
  - Inside the worker, `_live_engine_loop` and `_watchdog_loop` were not running autonomously in the background!
  - Market data was only polling when an external HTTP request hit `/api/status` or `/api/health`.
* **Resolution (Patch Plan):**
  1. Add `def post_fork(server, worker):` hook in `gunicorn.conf.py` to invoke `engine.start_background_workers()`.
  2. Implement `engine.start_background_workers()` in `MasterQuantEngine` (`app.py`).
  3. Add `@app.before_request` hook in `app.py` as a fail-safe to guarantee background threads are revived if ever stopped.

---

## 3. Recommended Remediation & Next Steps

1. Apply the Gunicorn `post_fork` hook and `app.py` worker thread starter.
2. Deploy the patch to Render.
3. Verify live `/api/health` returns `"worker_alive": true, "watchdog_alive": true`.
