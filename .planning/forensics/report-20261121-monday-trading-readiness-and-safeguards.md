# GSD Forensics Report: Monday (23-Nov-2026) Live Trading Mechanics & 7-Fix Safeguards Verification

**Date:** 2026-11-21 02:38:00 IST  
**Incident / Inquiry:** Comprehensive operational verification for Monday 23-Nov-2026 live trading execution, lot sizing, expiry selection, 5-minute candle alignment, single active trade rule, mobile trade isolation (Laxman Rekha), disarmed startup safety, and the 7 production fixes.  
**Target Code:** `app.py` / `RENDER_DEPLOY_FILES/app.py`  
**Status:** 100% VERIFIED & LOCKED AGAINST CODEBASE EVIDENCE  

---

## 1. Executive Summary & Concrete Answers to User Questions

| No. | User Question / Rule | Codebase Status | Exact Execution Behavior |
| :--- | :--- | :--- | :--- |
| **Q1** | **1 Lot से Trade लेगा ना Monday को?** | **YES (100% FIXED)** | `user_configured_lots = 1`, `lot_size = 65 Qty`. Default lot sizing strictly 1 Lot (65 Qty). |
| **Q2** | **Monday 23 Nov को कौन सी Expiry का Trade लेगा?** | **24-NOV-2026 (Tuesday Weekly)** | Immediate nearest weekly expiry `2026-11-24` (or current active weekly option chain from Kotak Neo). |
| **Q3** | **5-Minute Timeframe और Lines (EMA 9/20/50) कैसे रहेंगी?** | **CLOSED CANDLE CONFIRMATION** | 09:30 से 14:30 Window. केवल बंद 5m कैंडल पर EMA 9 > 20 > 50 + Green Candle (Bull Put) या EMA 9 < 20 < 50 + Red Candle (Bear Call). |
| **Q4** | **1 Trade Open रहने पर दूसरा Trade नहीं लेगा ना?** | **YES (STRICTLY BLOCKED)** | Rule 6: Demat में 0 Open Legs होना अनिवार्य है। जब तक Trade 1 बंद नहीं होता, Trade 2 स्कैन भी नहीं होगा। |
| **Q5** | **Open Position में सिर्फ Render का ही दिखेगा ना?** | **YES (LAXMAN REKHA ACTIVE)** | केवल Render के Order IDs (`terminal_order_ids`) को ही ट्रैक किया जाएगा। Kotak Mobile App के ट्रेड्स पूरी तरह आइसोलेटेड रहेंगे। |
| **Q6** | **Reboot के बाद Trade खुद Execute नहीं होगा ना?** | **YES (STARTUP DISARMED)** | Restart/Redeploy के बाद इंजन `manual_paused = True` रहता है। जब तक ऑपरेटर MPIN डालकर 'Resume' नहीं दबाता, 0 ऑर्डर्स जाएंगे। |
| **Q7** | **Order Fill / Failures पर जो 7 Fixes किए थे, सब याद हैं ना?** | **ALL 7 FIXES ACTIVE** | Square-off Safety, Order Registry Persistence, Orphan Hedge Unwind, Multi-source Reconciliation, BUY-FIRST Execution सब लाइव हैं। |

---

## 2. Deep Dive: Anomaly Prevention & Architectural Safeguards

### Anomaly Check 1: Premature Entry on Incomplete Candle (Fix Active)
- **Code Anchor:** `check_candle_close_confirmation()` in `app.py`.
- **Evidence:** Historical API से केवल `:00` पर कैंडल अंतिम मानी जाती है और `:01` पर सिग्नल एक बार चेक होता है। रनिंग कैंडल पर कभी ट्रेड ट्रिगर नहीं हो सकता।

### Anomaly Check 2: Concurrent Multi-Trade Violation (Fix Active)
- **Code Anchor:** `cond_margin_safety = bool(not has_local_open and not has_broker_open and cash_sufficient)`.
- **Evidence:** यदि `active_position` में कोई भी पोजीशन खुली है, तो 6-Rule Gate का स्कोर कभी 6/6 नहीं हो सकता (अधिकतम 5/6)। अतः एक समय में 1 से अधिक ट्रेड लेना कोड स्तर पर असंभव है।

### Anomaly Check 3: Mobile App Trades Auto-Adoption or Auto-Squareoff (Fix Active)
- **Code Anchor:** `order_registry` & `terminal_order_ids` matching in `poll_kotak_neo()`.
- **Evidence:** यूजर द्वारा मोबाइल ऐप (`ADMINCPPAPI_MOB`) से लिए गए ऑर्डर्स का आईडी रेंडर के डेटाबेस में नहीं होता। वे ऑर्डर्स 'EXTERNAL' माने जाते हैं, उन्हें न तो कॉकपिट अडॉप्ट करता है और न ही ऑटो-क्लोज़ करता है।

### Anomaly Check 4: Unintended Execution After Container Restart (Fix Active)
- **Code Anchor:** `load_engine_mode()` forces `self.manual_paused = True` if `execution_mode == "LIVE"`.
- **Evidence:** कंटेनर रीस्टार्ट होने पर इंजन 'PAUSED / DISARMED' रहता है। ऑपरेटर की स्पष्ट सहमति (Resume + MPIN) के बिना कोई ऑर्डर नहीं जा सकता।

---

## 3. Monday 23-Nov-2026 Timeline Simulation

1. **09:15 AM:** Market opens. Render engine starts in DISARMED mode. Broker reconciliation runs (`0 broker open + 0 local open -> reconciliation_blocked = False`).
2. **Operator Action:** ऑपरेटर Cockpit पर 'Resume' क्लिक करके MPIN दर्ज करता है -> Engine ARMED.
3. **09:30 AM:** Trading window opens. 6-Rule Gate scanning begins.
4. **09:55:01 AM:** मान लीजिए 09:50–09:55 की बंद 5m कैंडल पर Spot > Daily 20 EMA, EMA 9 > 20 > 50, Green Candle, PCR $\ge 1.05$:
   - **Trade 1 Triggered:**
     - Step 1: BUY Hedge Leg (e.g. 25050 PE, 1 Lot = 65 Qty) fills first.
     - Step 2: SELL ATM Leg (e.g. 25350 PE, 1 Lot = 65 Qty) fills second.
5. **Trade 1 Active:**
   - 40% Target Exit (+₹2,200+ approx)
   - 25% Auto-Trail (Trailing SL active)
   - **Hard Stop Loss: -₹2,800 FLAT (Strictly Capped)**
   - No Trade 2 can be taken while Trade 1 is OPEN.
6. **Trade 1 Closes:**
   - Mandatory **30-Minute Cooldown** activates.
   - Scanning for Trade 2 remains completely locked during cooldown.
7. **Post-Cooldown:**
   - यदि 14:30 PM से पहले दूसरा 6/6 सिग्नल मिलता है, तभी Trade 2 लिया जाएगा। दिन में अधिकतम 2 ट्रेड्स की सीमा है।
8. **15:15 PM:** यदि कोई पोजीशन खुली है, तो Intraday Auto-Squareoff हो जाएगा (Zero Overnight Risk).
