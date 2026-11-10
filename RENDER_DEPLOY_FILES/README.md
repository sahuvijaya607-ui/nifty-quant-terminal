# Quantitative Trading Backtest Project

A modular, high-performance backtesting workspace tailored for Indian market derivatives & equities (e.g. NIFTY, BANKNIFTY) with Kotak Neo API integration.

---

## 📁 Workspace Structure

```
.
├── .venv/               # Isolated Python virtual environment
├── data/                # Parquet / CSV market data files (raw & processed)
├── scripts/             # Data fetching, cleaning, and preprocessing scripts
├── strategies/          # Trading strategy logic, signals, and execution engines
├── results/             # Backtest trade logs, metrics, equity curves, and reports
├── .env                 # Local API credentials (DO NOT COMMIT)
├── .env.example         # Template for required environment variables
├── .gitignore           # Git ignore rules for venv, secrets, and large files
└── requirements.txt     # Python dependencies
```

---

## 🚀 Quick Start

### 1. Activate the Virtual Environment
- **Windows PowerShell**:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- **Windows Command Prompt (cmd)**:
  ```cmd
  .venv\Scripts\activate.bat
  ```

### 2. Configure Kotak Neo API
Edit [.env](file:///c:/Users/user/OneDrive/Desktop/NIFTY%20REAL%20DATA/.env) with your credentials:
```ini
CONSUMER_KEY=your_consumer_key
CONSUMER_SECRET=your_consumer_secret
MOBILE_NUMBER=+91XXXXXXXXXX
PASSWORD=your_password
MPIN=your_mpin
```

### 3. Installed Packages
- **`pandas`** (`3.1.1`): High-performance tabular data manipulation
- **`numpy`** (`2.4.6`): Fast numerical computing and vectorized operations
- **`pyarrow`** (`26.0.1`): Parquet storage read/write for large multi-year tick/candle datasets
- **`python-dotenv`** (`1.2.4`): Safe environment variable loading

---

## 🛡️ Quantitative Principles & Best Practices
- **Execution Realism**: Never use mark price as a guaranteed fill; account for bid/ask spread and realistic slippage.
- **Lookahead Bias Prevention**: Never use incomplete candles for signal generation.
- **Signal Integrity**: Ensure clean deduplication so active positions are strictly tracked.
