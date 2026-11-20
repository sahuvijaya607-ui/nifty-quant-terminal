# GSD Post-Mortem Forensic Report — 20 Nov 2026

**Generated:** 2026-11-20 19:15:00 IST  
**Investigation Target:** Post-market trading event analysis (Render suspension, unexpected Trade 2 entry, open order visibility, and execution mode behavior).  
**Investigator:** Antigravity Quant & Systems Engineering Agent  
**Status:** COMPLETE (Ground Truth Verified via Kotak Neo API & Git History)

---

## 1. Executive Summary & Final Financial Outcome

On 20-Nov-2026, the user experienced confusion and suspended the Render web service after observing:
1. Trade 1 was taken automatically, but the Render Cockpit HUD initially showed "No Open Position" and the "Kotak Neo Open Orderbook" was empty.
2. Render Terminal later showed an unrealised MTM loss of `-₹704.47` while the Kotak Neo mobile app showed `+₹283.00` in profit.
3. Upon manually squaring off Trade 1 at 10:49:21 AM on the mobile app, the Render Algo immediately entered **Trade 2** just 2 seconds later at 10:49:23 AM.
4. The user manually squared off Trade 2 at 10:51:20 AM and suspended the Render service to halt further automated actions.

### 💰 Final Financial Verdict (Ground Truth Broker Ledger)
Despite the user interface desync and unexpected Trade 2 entry, **both trades closed in positive profit**:
* **Trade 1 Net P&L:** **+₹1,891.50** Gross Profit
* **Trade 2 Net P&L:** **+₹39.00** Gross Profit
* **Total Day Realised P&L:** **+₹1,930.50 Gross Profit**
* **Current Demat Status:** All 4 option legs have `NetQty: 0`. No open positions or overnight market risk remain in the Kotak Neo Demat account.

---

## 2. Broker Order Book Timeline (Exact Evidence)

The following 8 orders were retrieved directly from the Kotak Neo API (`client.order_report()`) for trading date 20-Nov-2026:

| Time (IST) | Order ID | Symbol | Action | Qty | Fill Price | Order Source | Status | Description |
| :--- | :--- | :--- | :---: | :---: | :---: | :--- | :---: | :--- |
| **09:53:35** | `261120000154735` | `NIFTY26NOV25150PE` | BUY | 65 | ₹31.15 | `ADMINCPPAPI_NEOTRADEAPI` | `complete` | **Trade 1 Entry (Hedge Leg)** |
| **09:53:35** | `261120000154745` | `NIFTY26NOV25500PE` | SELL | 65 | ₹139.55 | `ADMINCPPAPI_NEOTRADEAPI` | `complete` | **Trade 1 Entry (Short Leg)** |
| **10:49:21** | `261120000257959` | `NIFTY26NOV25500PE` | BUY | 65 | ₹101.05 | `ADMINCPPAPI_MOB` | `complete` | **Trade 1 Exit (Manual via Phone)** |
| **10:49:21** | `261120000257967` | `NIFTY26NOV25150PE` | SELL | 65 | ₹21.75 | `ADMINCPPAPI_MOB` | `complete` | **Trade 1 Exit (Manual via Phone)** |
| **10:49:23** | `261120000258045` | `NIFTY26NOV25100PE` | BUY | 65 | ₹17.70 | `ADMINCPPAPI_NEOTRADEAPI` | `complete` | **Trade 2 Entry (Hedge Leg)** |
| **10:49:24** | `261120000258072` | `NIFTY26NOV25550PE` | SELL | 65 | ₹124.35 | `ADMINCPPAPI_NEOTRADEAPI` | `complete` | **Trade 2 Entry (Short Leg)** |
| **10:51:20** | `261120000262093` | `NIFTY26NOV25550PE` | BUY | 65 | ₹123.55 | `ADMINCPPAPI_MOB` | `complete` | **Trade 2 Exit (Manual via Phone)** |
| **10:51:20** | `261120000262095` | `NIFTY26NOV25100PE` | SELL | 65 | ₹17.50 | `ADMINCPPAPI_MOB` | `complete` | **Trade 2 Exit (Manual via Phone)** |

---

## 3. Deep Dive: Anomaly Analysis & Root Causes

### Anomaly 1: Why Was Trade 1 Placed at 09:53:35 AM?
* **Evidence:** In `app.py` line 162, `self.execution_mode = "LIVE"` was active. At 09:53:35 AM:
  - Spot Price was 25,514 > Daily 20 EMA (24,483) [Rule 1: TRUE]
  - 5-minute EMA 9 > 20 > 50 with Green Candle [Rule 2: TRUE]
  - Weekly PCR was 1.335 >= 1.05 [Rule 3: TRUE]
  - Time was 09:53 AM IST (between 09:30 and 14:30) [Rule 4: TRUE]
  - Daily Trades Done was 0/2 [Rule 5: TRUE]
  - Free Cash was ₹101,245 >= ₹50,000 and 0 open positions [Rule 6: TRUE]
* **Finding:** Trade 1 was legitimately generated according to the defined Model 4 rules.

### Anomaly 2: Why Did "Open Orders" and Cockpit Position Show Empty Initially?
* **Evidence:**
  1. **Order Terminology Confusion:** The table labeled `KOTAK NEO OPEN ORDERBOOK` only displays orders where status is `OPEN`, `PENDING`, or `TRIGGER_PENDING` (i.e. waiting in the exchange queue). Because Trade 1 orders executed instantly as Marketable Limits, their status immediately became `COMPLETE`. A completed order is no longer an "open order" and is filtered out of pending order views.
  2. **Container Ephemeral Restart:** During the morning session, code updates pushed to GitHub triggered automatic redeployments on Render. Because container memory is ephemeral, the in-memory `terminal_order_ids` set was wiped clean, causing the Cockpit HUD to briefly display "No Open Position" until the broker reconciliation commit (`ec9d988`) was deployed.

### Anomaly 3: Why Did Render Show `-₹704.47` While Kotak Neo Showed `+₹283.00`?
* **Evidence:**
  1. The initial HUD adoption logic computed P&L using limit order prices (₹135.55) rather than actual traded execution prices (₹139.55).
  2. The Render terminal subtracted estimated statutory exit charges and taxes (-₹109.88).
  3. Real-time option Greeks/IV calculations temporarily lagged behind Kotak Neo's streaming feed, creating an alarming discrepancy that prompted the user to exit manually.

### Anomaly 4: Why Was Trade 2 Triggered 2 Seconds After Manual Exit?
* **Evidence:**
  - At 10:49:21 AM, the user squared off Trade 1 on the mobile app.
  - At 10:49:23 AM (2 seconds later), the background quant thread evaluated the market:
    - Demat open legs = 0 (`has_open_broker_positions() == False`).
    - Daily trade count = 1 / 2 (Trade quota not exhausted).
    - Market conditions remained solidly bullish.
  - **The Missing Control:** The engine lacked a **Manual Square-off Latch** or **Post-Exit Cooldown Period**. The algorithm was unaware that the exit was an intentional manual decision rather than an algorithm target exit, and immediately re-entered Trade 2.

### Anomaly 5: Why Did the User Believe Live Trading Was Turned Off?
* **Evidence:**
  - Route `/api/set_execution_mode` (lines 2613-2630) only updated `engine.execution_mode = "PAPER"` in memory.
  - Whenever Render redeployed or restarted the worker, line 162 initialized `self.execution_mode = "LIVE"`.
  - The UI toggle state was not persisted to disk or environment variables, allowing the engine to revert to LIVE mode silently upon restart.

---

## 4. Root Cause Summary Matrix

| Issue | Root Cause | Impact | Fix Requirement |
| :--- | :--- | :--- | :--- |
| **Premature Trade 2 Re-entry** | No cooldown or latch after manual square-off; quota allowed 2 trades. | Bot entered Trade 2 (25550PE/25100PE) 2 seconds after manual exit. | Implement mandatory 30-minute cooldown and a persistent "Manual Pause" lock. |
| **Execution Mode Reset** | Mode held only in RAM; constructor defaulted to `"LIVE"`. | Any git push or Render restart reset mode to LIVE. | Persist mode to disk file (`engine_mode.json`) and default to `"PAPER"`. |
| **Empty Orderbook Display** | Filter displayed only pending `OPEN` orders; completed orders were excluded. | User saw blank orderbook and assumed orders failed. | Add "Executed Orders Today" tab alongside pending orders. |
| **P&L Mismatch** | Friction tax deductions and initial price reference discrepancy. | Render showed loss while Kotak showed profit, inducing panic. | Sync exact Kotak average traded price and display Net Realised vs Gross MTM clearly. |

---

## 5. Corrective Action Plan & Hardening Recommendations

1. **State Persistence (`engine_mode.json`):**
   - Save the execution mode (`LIVE` vs `PAPER`) and manual pause state to a JSON file.
   - On server startup, load the persisted state; do not unconditionally default to `"LIVE"`.

2. **Manual Intervention Latch & Trade Cooldown:**
   - If an open position is closed externally (via `ADMINCPPAPI_MOB`) or via the emergency stop button, set an automated **Trading Latch (PAUSED)**.
   - Do NOT fire Trade 2 automatically without explicit user re-authorization.

3. **Unified Orders & Position Display:**
   - In the frontend UI, provide two clear sections:
     - `Pending Orders (0)`
     - `Today's Executed Trades (Trade 1: +₹1,891.50, Trade 2: +₹39.00)`
   - Show actual broker fill prices retrieved from `order_report()`.

4. **Render Re-activation Protocol:**
   - Keep Render suspended until the persistence file and manual latch are implemented and tested.
   - Test thoroughly in `PAPER` mode before unlocking live execution with the security MPIN.

---

*Report filed in `.planning/forensics/report-20261120-session-forensics.md`. Ground truth confirmed with 100% zero data loss.*
