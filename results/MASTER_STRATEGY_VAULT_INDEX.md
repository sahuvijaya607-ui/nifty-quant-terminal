# 🏛️ MASTER QUANT STRATEGY & BACKTEST VAULT INDEX
### NIFTY REAL DATA QUANTITATIVE AUDIT & TOURNAMENT VAULT (2020 TO 2026)

This repository contains all historical backtest engines, verified trade ledgers, parameter audits, and tournament results across **74.3 Million Rows of 1-minute tick data** (`nifty_options_master_2020_2026_1min.parquet`) and **35.9 Million Rows** of clean options chains (`nifty_option_chain_1min_clean.parquet`).

---

## 🏆 1. 6-Year Master Tournament Results (2020 to 2026)

* **Master Summary CSV**: [`results/all_strategies_6yr_master_summary.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/all_strategies_6yr_master_summary.csv)
* **Year-by-Year Matrix (2020-2026)**: [`results/all_strategies_6yr_yearly_matrix.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/all_strategies_6yr_yearly_matrix.csv)
* **Master Tournament Runner Script**: [`strategies/tournament_full_6yr_all_strategies.py`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/strategies/tournament_full_6yr_all_strategies.py)

### Individual 6-Year Trade Ledgers:
1. **Strat F: 4-Day Engine (ATM D50_L10 Tue-Wed-Thu-Fri)**:
   * Ledger: [`results/6yr_trades_Strat_F_4Day_ATM_D50_L10.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/6yr_trades_Strat_F_4Day_ATM_D50_L10.csv)
   * Stats: 1,310 Trades | Net PnL: **+₹1,56,436.73** | Win Rate: **53.66%** | Avg Monthly: **₹2,085.82**
2. **Strat B: Pure ATM Asymmetric (D50_L10 Tue-Thu-Fri - 3 Days)** *(CHAMPION)*:
   * Ledger: [`results/6yr_trades_Strat_B_Pure_ATM_D50_L10_3Day.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/6yr_trades_Strat_B_Pure_ATM_D50_L10_3Day.csv)
   * Stats: 919 Trades | Net PnL: **+₹1,45,236.78** | Win Rate: **53.97%** | Net/Trade: **₹158.04**
3. **Strat C: Pure ATM Moderate Wing (D50_L15 Tue-Thu-Fri)**:
   * Ledger: [`results/6yr_trades_Strat_C_Pure_ATM_D50_L15_3Day.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/6yr_trades_Strat_C_Pure_ATM_D50_L15_3Day.csv)
   * Stats: 908 Trades | Net PnL: **+₹1,17,343.20** | Win Rate: **53.41%**
4. **Strat D: Balanced OTM Asymmetric (D45_L10 Tue-Thu-Fri)**:
   * Ledger: [`results/6yr_trades_Strat_D_Balanced_D45_L10_3Day.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/6yr_trades_Strat_D_Balanced_D45_L10_3Day.csv)
   * Stats: 938 Trades | Net PnL: **+₹1,00,264.15** | Win Rate: **53.84%**
5. **Strat E: Balanced OTM Moderate (D45_L15 Tue-Thu-Fri)**:
   * Ledger: [`results/6yr_trades_Strat_E_Balanced_D45_L15_3Day.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/6yr_trades_Strat_E_Balanced_D45_L15_3Day.csv)
   * Stats: 929 Trades | Net PnL: **+₹79,056.66** | Win Rate: **52.96%**
6. **Strat A: Baseline Benchmark (35Δ/15Δ All 5 Days)**:
   * Ledger: [`results/6yr_trades_Strat_A_Base_35D_15D_AllDays.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/6yr_trades_Strat_A_Base_35D_15D_AllDays.csv)
   * Stats: 1,898 Trades | Net PnL: **+₹71,225.36** | Max DD: **-₹27,916**

---

## 🔍 2. Detailed Forensics & Deployment Verification

1. **Jan 2026 – Mar 2026 Live Audit (Spot Entry/Exit Prices)**:
   * File: [`results/strat2_jan_mar_2026_detailed_trades.csv`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/results/strat2_jan_mar_2026_detailed_trades.csv)
   * Contains all 46 trades enriched with NIFTY spot entry, spot exit, spot points moved, strikes traded, entry credit, exit spread, and net PnL (65 Qty enforced).
2. **06-Jan-2026 Forensic Audit Script**:
   * Script: [`scripts/forensic_audit_6_jan_2026.py`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/scripts/forensic_audit_6_jan_2026.py)
   * Minute-by-minute spot, PCR (0.8488), option strikes (26250 CE / 26600 CE), MTM swings (-₹1,657 worst drawdown at 10:02 to +₹2,135 peak at 14:10), and final +₹1,773 profit.
3. **Dynamic SEBI Historical Lot Size Schedule**:
   * Module: [`utils/nifty_lot_size.py`](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/utils/nifty_lot_size.py)
   * Accurately enforces 75 $\to$ 50 $\to$ 25 $\to$ 75 $\to$ 65 qty across history.

---

## 📑 3. Exhaustive Reports & Research Dossiers

1. **Master 6-Year All-Strategy Tournament Dossier**:  
   [`master_6year_all_strategies_tournament_dossier.md`](file:///C:/Users/user/.gemini/antigravity/brain/1d9a796e-b5cd-4415-a0a4-4ebcef8260d7/master_6year_all_strategies_tournament_dossier.md)
2. **All Strategies Complete Backtest Dossier**:  
   [`all_strategies_complete_backtest_dossier.md`](file:///C:/Users/user/.gemini/antigravity/brain/1d9a796e-b5cd-4415-a0a4-4ebcef8260d7/all_strategies_complete_backtest_dossier.md)
3. **Exhaustive Multi-Parameter Strategy Comparison (30+ Metrics)**:  
   [`exhaustive_multi_parameter_strategy_comparison.md`](file:///C:/Users/user/.gemini/antigravity/brain/1d9a796e-b5cd-4415-a0a4-4ebcef8260d7/exhaustive_multi_parameter_strategy_comparison.md)
4. **Competitor Credit Spread Tournament Audit**:  
   [`competitor_credit_spread_strategies_audit.md`](file:///C:/Users/user/.gemini/antigravity/brain/1d9a796e-b5cd-4415-a0a4-4ebcef8260d7/competitor_credit_spread_strategies_audit.md)
5. **Master 6-Year Baseline PCR+EMA Report**:  
   [`master_6yr_pcr_ema_backtest_report.md`](file:///C:/Users/user/.gemini/antigravity/brain/1d9a796e-b5cd-4415-a0a4-4ebcef8260d7/master_6yr_pcr_ema_backtest_report.md)

---
*Created on 2026-11-10. All data backed by actual NSE tick data on disk.*
