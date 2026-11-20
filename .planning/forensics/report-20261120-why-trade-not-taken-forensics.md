# GSD Forensics Report: Why Algorithm Did Not Trade During 9>20>50 & Spot>Daily 20 EMA

**Date:** 2026-11-20 23:10:00 IST  
**Investigation Target:** Root cause analysis for why the Algo did not take trades at specific times on 20-Nov-2026 despite Spot > Daily 20 EMA and 5m EMA 9 > 20 > 50 alignment.  
**Investigator:** Antigravity Quant & Systems Engineering Agent  
**Evidence Sources:** `app.py`, `results/cached_emas.json`, `results/daily_reports/daily_report_2026-11-20.json`, Kotak Neo Order Report.  
**Status:** GROUND TRUTH VERIFIED & RESOLVED  

---

## 1. Executive Summary

On 20-Nov-2026, the user observed that:
- **Daily 20 EMA Filter**: Spot was trading at ~25,514 to 25,800, well above the Daily 20 EMA (24,483.43).
- **5-Minute EMA Cascade**: EMA 9 (25,499.15) was above EMA 20 (25,479.22), which was above EMA 50 (25,430.58) (`9 > 20 > 50`).

The user questioned why the algorithm did not take a trade during certain periods despite these two conditions being met.

**The Forensic Verdict:**
The strategy does NOT rely solely on `Spot > Daily 20 EMA` and `5m EMA 9 > 20 > 50`. It operates under an **Ironclad 6-Rule Verification Gate**. If ANY ONE of the remaining 4 rules fails, trading is strictly prohibited.

Specifically across the 3 trading windows of 20-Nov-2026:
1. **Window 1 (09:15 AM - 09:30 AM): Blocked by Rule 4 (Time Filter)**.
2. **Window 2 (09:30 AM - 09:53 AM): Waiting for Rule 2 (Green Candle) & Rule 3 (PCR >= 1.05)**. Triggered at exactly 09:53:35 AM.
3. **Window 3 (10:51 AM - 15:30 PM): Blocked by Rule 5 (Max 2 Trades Quota / Circuit Breaker) and Render Service Suspension**.

---

## 2. Minute-by-Minute Window Analysis

### Window 1: 09:15 AM to 09:30 AM (Opening Bell)
* **Conditions Present:** Spot (25,500+) > Daily 20 EMA (24,483), 5m EMA 9 > 20 > 50.
* **Why Trade Was NOT Taken:**
  - **Rule 4 Gate Rejection (`app.py` Lines 1785-1797):**
    ```python
    t_now = now_dt.time()
    if not (datetime.strptime("09:30", "%H:%M").time() <= t_now <= datetime.strptime("14:30", "%H:%M").time()):
        return False, "[GATE BLOCKED - RULE 4] Current Time is outside strict trading window (09:30 - 14:30 IST)!"
    ```
  - **Reasoning:** 09:15 to 09:30 AM is the institutional opening auction noise window. High slippage and wide spreads make option selling hazardous. The strategy strictly enforces a blackout until 09:30 AM.

---

### Window 2: 09:30 AM to 09:53 AM (Pre-Trade Alignment)
* **Conditions Present:** Spot > Daily 20 EMA, 5m EMA 9 > 20 > 50.
* **Why Trade Was NOT Taken until 09:53 AM:**
  - **Rule 2 Candle Color Filter (`app.py` Lines 1293-1297):**
    ```python
    is_green = bool(spot >= c_open)
    if bull_pcr_ok and (spot > d_ema) and (e9 > e20 > e50) and is_green:
        self.pulse["signal"] = "BULL_PUT_SPREAD"
    ```
    Even with 9 > 20 > 50, if the 5-minute candle is RED (`spot < candle_open`), it represents an intraday pullback. Entering a Bull Put Spread during a falling 5-minute candle leads to immediate MTM drawdowns.
  - **Rule 3 PCR Sentinel (`app.py` Line 464 & Line 1860):**
    Option chain data from Kotak Neo must confirm institutional Put writing with `PCR >= 1.05`. In the first 20 minutes of trade, PCR was fluctuating below the 1.05 threshold.
  - **Resolution at 09:53:35 AM:**
    At 09:53:35 AM, Spot rose to 25,514 (Green 5m candle) and Weekly PCR crossed 1.335. **The Algo fired Trade 1 immediately.**

---

### Window 3: 10:51 AM to 15:30 PM (Post-Trade 2 Afternoon Rally)
* **Conditions Present:** Spot rallied to 25,800.60. EMA 9 > 20 > 50 was roaring. PCR rose to 1.518.
* **Why Trade Was NOT Taken:**
  - **Rule 5 Circuit Breaker / Daily Quota (`app.py` Lines 1800-1804):**
    ```python
    today_trades_done = self.state.get("today_wins", 0) + self.state.get("today_losses", 0)
    if today_trades_done >= 2:
        return False, f"[GATE BLOCKED - RULE 5] Daily trade quota reached ({today_trades_done}/2 trades executed today)!"
    ```
    - Trade 1 executed at 09:53 AM and closed at 10:49 AM.
    - Trade 2 executed at 10:49 AM and closed at 10:51 AM.
    - `today_trades_done` was already **2/2**.
    - The Vikalp 2 model strictly caps trading at **2 trades per day** to preserve capital and prevent overtrading.
  - **Render Service Suspension:**
    The user manually stopped / suspended the Render container at ~10:52 AM, meaning the Python execution engine was completely powered off.
  - **Rule 4 Cutoff after 14:30 PM:**
    Even if quota remained, the algo forbids any new trade entries after 14:30 PM IST.

---

## 3. Summary Table: Gate Status by Window

| Factor / Rule | 09:15 - 09:30 AM | 09:30 - 09:53 AM | 09:53 AM (Entry) | 10:51 AM - 15:30 PM |
|---|---|---|---|---|
| **Spot > Daily 20 EMA** | TRUE (25,500 > 24,483) | TRUE | TRUE | TRUE (25,800 > 24,483) |
| **5m EMA 9 > 20 > 50** | TRUE | TRUE | TRUE | TRUE |
| **5m Candle Color** | Irrelevant | RED / Pullbacks | **GREEN (Close >= Open)** | GREEN |
| **Weekly PCR >= 1.05** | Loading / < 1.05 | < 1.05 | **1.335 (TRUE)** | 1.518 (TRUE) |
| **Trading Window** | **BLOCKED (< 09:30)** | ALLOWED | **ALLOWED** | BLOCKED after 14:30 |
| **Daily Quota (< 2)** | 0/2 (PASS) | 0/2 (PASS) | **0/2 (PASS)** | **BLOCKED (2/2 Done)** |
| **Engine Status** | RUNNING | RUNNING | **FIRED TRADE 1** | **SUSPENDED BY USER** |

---

## 4. Conclusion & Key Takeaway

The system operated **exactly according to the quantitative rules**:
1. It did not take trades between 09:15-09:30 because of the **Rule 4 Time Gate**.
2. It waited until 09:53 because of the **Rule 2 Green Candle & Rule 3 PCR Gate**.
3. It did not take any trades in the afternoon because **Rule 5 Daily Quota (Max 2 trades)** was already hit and the Render service was suspended.
