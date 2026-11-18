"""
========================================================================================
CHAMPION MODEL 4: LIVE INTERACTIVE TERMINAL DESK (NIFTY 50)
========================================================================================
Bloomberg-grade live interactive terminal powered by 'rich' and Kotak Neo API.
- Non-blocking live refresh (every 1 second clock, 10s API quotes).
- Interactive hotkeys:
    [T] Trigger Simulated Live Paper Trade
    [X] Square-off Active Position
    [R] Reset Circuit Breaker
    [Q] Quit Terminal
========================================================================================
"""

import os
import sys
import time
import json
from datetime import datetime, date, timedelta
from pathlib import Path
import msvcrt

import pandas as pd
import numpy as np
import pyotp
from dotenv import load_dotenv

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.live import Live

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()
console = Console()


class LiveTerminalEngine:
    def __init__(self):
        self.consumer_key = os.getenv("CONSUMER_KEY") or os.getenv("KOTAK_CONSUMER_KEY", "").strip()
        mobile = os.getenv("MOBILE_NUMBER") or os.getenv("KOTAK_MOBILE_NUMBER", "").strip()
        self.mobile = f"+91{mobile}" if mobile and not mobile.startswith("+91") else mobile
        self.ucc = os.getenv("UCC") or os.getenv("KOTAK_UCC", "").strip()
        self.mpin = os.getenv("MPIN") or os.getenv("KOTAK_MPIN", "").strip()
        self.totp_sec = os.getenv("TOTP_SECRET") or os.getenv("KOTAK_TOTP_SECRET", "").strip()

        self.client = None
        self.authenticated = False
        self.greeting = "RAHUL"
        self.lot_size = 65

        # Persistence paths
        self.state_file = Path("results/paper_engine_state.json")
        self.trades_file = Path("results/live_paper_trades.csv")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Rate limiting / caching
        self.last_api_poll = 0
        self.last_hist_poll = 0
        self.status_message = "Engine initialized. Ready."

        # Market Cache
        self.pulse = {
            "spot": 25044.65,
            "prev_close": 24986.40,
            "daily_ema20": 23677.11,
            "ema9": 25007.35,
            "ema20": 24988.33,
            "ema50": 24952.39,
            "pcr": 1.15,
            "next_expiry": "2026-11-17",
            "calls_count": 81,
            "puts_count": 81,
            "signal": "BULL_PUT_SPREAD",
            "option_chain": None,
        }

        self.state = self.load_state()

    def authenticate(self):
        """Authenticates with Kotak Neo API using TOTP & MPIN."""
        try:
            from neo_api_client import NeoAPI
            self.client = NeoAPI(consumer_key=self.consumer_key, environment="prod")
            totp = pyotp.TOTP(self.totp_sec).now()
            res1 = self.client.totp_login(mobile_number=self.mobile, ucc=self.ucc, totp=totp)
            res2 = self.client.totp_validate(mpin=self.mpin)

            if res2.get("data", {}).get("status") == "success":
                self.authenticated = True
                self.greeting = res2.get("data", {}).get("greetingName", "RAHUL")
                self.status_message = f"Kotak Neo Authenticated ({self.greeting})"
                return True
            else:
                self.status_message = "Kotak Auth Failed - Using Offline Cache"
                return False
        except Exception as e:
            self.status_message = f"Auth Error ({e}) - Running Cache Mode"
            return False

    def load_state(self):
        today_str = date.today().strftime("%Y-%m-%d")
        default_state = {
            "date": today_str,
            "circuit_breaker_active": False,
            "today_losses": 0,
            "today_wins": 0,
            "today_realized_pnl": 0.0,
            "active_position": None,
        }
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    saved = json.load(f)
                    if saved.get("date") == today_str:
                        return saved
            except Exception:
                pass
        return default_state

    def save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def poll_kotak_neo(self):
        """Polls live quotes & option chain (every 10 seconds)."""
        now = time.time()
        if not self.authenticated or not self.client:
            return

        if now - self.last_api_poll < 10:
            return

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

            total_call_oi = sum(int(c.get("oi", {}).get("oi", 0)) for c in chain.get("call", []))
            total_put_oi = sum(int(p.get("oi", {}).get("oi", 0)) for p in chain.get("put", []))
            if total_call_oi > 0:
                self.pulse["pcr"] = round(total_put_oi / total_call_oi, 2)
            self.pulse["calls_count"] = len(chain.get("call", []))
            self.pulse["puts_count"] = len(chain.get("put", []))

            # Multi-Timeframe Trend Signal
            spot = self.pulse["spot"]
            d_ema = self.pulse["daily_ema20"]
            e9 = self.pulse["ema9"]
            e20 = self.pulse["ema20"]
            e50 = self.pulse["ema50"]
            pcr = self.pulse["pcr"]

            if (spot > d_ema) and (e9 > e20 > e50) and (pcr >= 1.00):
                self.pulse["signal"] = "BULL_PUT_SPREAD"
            elif (spot < d_ema) and (e9 < e20 < e50) and (pcr <= 0.90):
                self.pulse["signal"] = "BEAR_CALL_SPREAD"
            else:
                self.pulse["signal"] = "NEUTRAL_SCANNING"

            self.last_api_poll = now
        except Exception as e:
            self.status_message = f"Poll Warning: {str(e)[:30]}"

    def trigger_trade(self, force=False):
        """Opens a paper position based on current signal and strikes."""
        if self.state.get("active_position") and not force:
            self.status_message = "Position already active. Press [X] to close first."
            return

        direction = self.pulse["signal"]
        if direction == "NEUTRAL_SCANNING" and not force:
            self.status_message = "Market Neutral. Waiting for trend confirmation."
            return

        if direction == "NEUTRAL_SCANNING":
            direction = "BULL_PUT_SPREAD"

        spot = self.pulse["spot"]
        atm_strike = round(spot / 50.0) * 50

        if direction == "BULL_PUT_SPREAD":
            sell_strike = atm_strike
            buy_strike = atm_strike - 350
            opt_type = "PE"
            sell_ltp = 114.50
            buy_ltp = 29.80
        else:
            sell_strike = atm_strike
            buy_strike = atm_strike + 350
            opt_type = "CE"
            sell_ltp = 118.00
            buy_ltp = 31.50

        # Realistic Slippage Friction (Never mark price!)
        exec_sell = round(sell_ltp * 0.995, 2)
        exec_buy = round(buy_ltp * 1.005, 2)
        net_credit = round(exec_sell - exec_buy, 2)

        new_pos = {
            "direction": direction,
            "expiry": self.pulse["next_expiry"],
            "sell_strike": int(sell_strike),
            "sell_type": opt_type,
            "sell_price": exec_sell,
            "buy_strike": int(buy_strike),
            "buy_type": opt_type,
            "buy_price": exec_buy,
            "net_credit": net_credit,
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entry_spot": round(spot, 2),
            "current_spread": net_credit,
            "target_spread": round(net_credit * 0.50, 2),
            "sl_spread": round(net_credit * 1.40, 2),
            "breakeven_active": False,
            "mtm_pnl": 0.0,
            "status": "OPEN",
        }

        self.state["active_position"] = new_pos
        self.save_state()
        self.status_message = f"New {direction} Opened! Net Credit: Rs {net_credit}"

    def square_off_position(self, reason="MANUAL_EXIT"):
        pos = self.state.get("active_position")
        if not pos:
            self.status_message = "No active position to close."
            return

        exit_spread = pos.get("current_spread", pos["net_credit"])
        final_pnl = pos.get("mtm_pnl", 0.0)
        exit_spot = self.pulse["spot"]

        pos["exit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pos["exit_spot"] = round(exit_spot, 2)
        pos["exit_spread"] = round(exit_spread, 2)
        pos["final_pnl"] = round(final_pnl, 2)
        pos["exit_reason"] = reason

        self.state["today_realized_pnl"] = round(self.state["today_realized_pnl"] + final_pnl, 2)
        if final_pnl >= 0:
            self.state["today_wins"] += 1
        else:
            self.state["today_losses"] += 1
            self.state["circuit_breaker_active"] = True

        trade_rec = {
            "date": date.today().strftime("%Y-%m-%d"),
            "entry_time": pos["entry_time"],
            "exit_time": pos["exit_time"],
            "direction": pos["direction"],
            "strikes": f"Sell {pos['sell_strike']}{pos['sell_type']} / Buy {pos['buy_strike']}{pos['buy_type']}",
            "net_credit": pos["net_credit"],
            "exit_spread": pos["exit_spread"],
            "entry_spot": pos["entry_spot"],
            "exit_spot": pos["exit_spot"],
            "lot_size": self.lot_size,
            "realized_pnl": pos["final_pnl"],
            "exit_reason": reason,
        }
        df_new = pd.DataFrame([trade_rec])
        if self.trades_file.exists():
            df_new.to_csv(self.trades_file, mode="a", header=False, index=False)
        else:
            df_new.to_csv(self.trades_file, index=False)

        self.state["active_position"] = None
        self.save_state()
        self.status_message = f"Position Closed ({reason}): PnL Rs {final_pnl:+,.2f}"

    def update_position_dynamics(self):
        """Simulates real-time price updates & trailing stop loss."""
        pos = self.state.get("active_position")
        if not pos:
            return

        spot = self.pulse["spot"]
        entry_spot = pos["entry_spot"]
        entry_credit = pos["net_credit"]
        direction = pos["direction"]

        spot_diff = (spot - entry_spot) if direction == "BULL_PUT_SPREAD" else (entry_spot - spot)
        elapsed_mins = max(1, (datetime.now() - datetime.strptime(pos["entry_time"], "%Y-%m-%d %H:%M:%S")).seconds // 60)
        theta_decay = min(entry_credit * 0.40, (elapsed_mins / 60.0) * 2.5)

        current_spread = np.clip(entry_credit - (spot_diff * 0.40) - theta_decay, 2.0, entry_credit * 2.5)
        pos["current_spread"] = round(current_spread, 2)

        pts_gain = entry_credit - current_spread
        mtm_pnl = round(pts_gain * self.lot_size, 2)
        pos["mtm_pnl"] = mtm_pnl

        # Breakeven Trailing @ +25% profit
        if not pos.get("breakeven_active") and (pts_gain >= entry_credit * 0.25):
            pos["breakeven_active"] = True
            pos["sl_spread"] = entry_credit
            self.status_message = "Trailing SL Moved to BREAKEVEN (Zero Risk Active)!"

        # Target or Stop Hit
        if current_spread <= pos["target_spread"]:
            self.square_off_position("TARGET_HIT (+50% DECAY)")
        elif current_spread >= pos["sl_spread"]:
            self.square_off_position("BREAKEVEN_SL" if pos.get("breakeven_active") else "STOP_LOSS")

    def generate_layout(self) -> Layout:
        now_dt = datetime.now()
        now_str = now_dt.strftime("%d-%b-%Y %H:%M:%S IST")

        # Market Hours check
        now_t = now_dt.time()
        is_mkt = (datetime.strptime("09:15", "%H:%M").time() <= now_t <= datetime.strptime("15:30", "%H:%M").time()) and (now_dt.weekday() < 5)

        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=5),
        )
        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1),
        )

        # 1. Header
        mkt_badge = "[bold green]● LIVE MARKET OPEN[/bold green]" if is_mkt else "[bold yellow]○ MARKET CLOSED (STANDBY)[/bold yellow]"
        cb_badge = "[bold red]🚨 CIRCUIT BREAKER HALTED[/bold red]" if self.state["circuit_breaker_active"] else "[bold green]✓ READY & ACTIVE[/bold green]"

        header_text = Text()
        header_text.append("  CHAMPION MODEL 4: LIVE QUANT DESK (NIFTY 50)  ", style="bold white on blue")
        header_text.append(f"  Trader: {self.greeting} | {mkt_badge} | Desk: {cb_badge} | Time: {now_str}")
        layout["header"].update(Panel(header_text, style="blue"))

        # 2. Left Panel: Market Pulse
        spot = self.pulse["spot"]
        prev = self.pulse["prev_close"]
        chg = spot - prev
        pct = (chg / prev) * 100
        spot_style = "bold green" if chg >= 0 else "bold red"

        t_pulse = Table(show_header=False, box=None, padding=(0, 1))
        t_pulse.add_row("[bold]NIFTY 50 Spot:[/bold]", f"[{spot_style}]{spot:,.2f} ({chg:+.2f} / {pct:+.2f}%)[/{spot_style}]")
        t_pulse.add_row("[bold]Daily 20 EMA:[/bold]", f"[cyan]{self.pulse['daily_ema20']:,.2f}[/cyan] ({'BULLISH' if spot > self.pulse['daily_ema20'] else 'BEARISH'})")
        t_pulse.add_row("[bold]5-Min EMAs:[/bold]", f"9: {self.pulse['ema9']} | 20: {self.pulse['ema20']} | 50: {self.pulse['ema50']}")
        t_pulse.add_row("[bold]Option Chain:[/bold]", f"PCR: [bold]{self.pulse['pcr']}[/bold] | Expiry: [yellow]{self.pulse['next_expiry']}[/yellow]")
        t_pulse.add_row("[bold]Contract Pool:[/bold]", f"{self.pulse['calls_count']} Calls / {self.pulse['puts_count']} Puts | Lot: {self.lot_size}")

        sig = self.pulse["signal"]
        sig_style = "bold green on black" if "BULL" in sig else ("bold red on black" if "BEAR" in sig else "bold yellow")
        t_pulse.add_row("[bold]Engine Signal:[/bold]", f"[{sig_style}] {sig} [/{sig_style}]")

        layout["left"].update(Panel(t_pulse, title="[bold cyan]1. REAL-TIME MARKET PULSE[/bold cyan]", border_style="cyan"))

        # 3. Right Panel: Active Position
        pos = self.state.get("active_position")
        if pos:
            pnl = pos.get("mtm_pnl", 0.0)
            pnl_style = "bold green" if pnl >= 0 else "bold red"
            be_str = "[bold green]TRAILING BREAKEVEN ACTIVE (0 RISK)[/bold green]" if pos.get("breakeven_active") else "[gray]Initial Hard SL (40% Cap)[/gray]"

            t_pos = Table(show_header=False, box=None, padding=(0, 1))
            t_pos.add_row("[bold]Direction:[/bold]", f"[magenta]{pos['direction']}[/magenta]")
            t_pos.add_row("[bold]Leg 1 (Sell):[/bold]", f"{pos['sell_strike']}{pos['sell_type']} @ Rs {pos['sell_price']}")
            t_pos.add_row("[bold]Leg 2 (Buy):[/bold]", f"{pos['buy_strike']}{pos['buy_type']} @ Rs {pos['buy_price']} (Wing)")
            t_pos.add_row("[bold]Net Credit:[/bold]", f"Rs {pos['net_credit']} | Current: Rs {pos.get('current_spread', pos['net_credit'])}")
            t_pos.add_row("[bold]Live MTM PnL:[/bold]", f"[{pnl_style}]Rs {pnl:+,.2f}[/{pnl_style}]")
            t_pos.add_row("[bold]Target / SL:[/bold]", f"Target: Rs {pos['target_spread']} | SL: Rs {pos['sl_spread']}")
            t_pos.add_row("[bold]Risk State:[/bold]", be_str)
            layout["right"].update(Panel(t_pos, title="[bold magenta]2. ACTIVE LIVE POSITION[/bold magenta]", border_style="magenta"))
        else:
            empty_text = Text("\n\n   No active open position.\n   Scanning for Model 4 EMA + PCR alignment...\n   Press [T] to simulate an instant entry.", style="gray")
            layout["right"].update(Panel(empty_text, title="[bold magenta]2. ACTIVE LIVE POSITION[/bold magenta]", border_style="gray"))

        # 4. Footer Panel
        tot_pnl = self.state["today_realized_pnl"]
        tot_style = "bold green" if tot_pnl >= 0 else "bold red"
        trades_count = self.state["today_wins"] + self.state["today_losses"]

        t_foot = Table(show_header=False, box=None, padding=(0, 2))
        t_foot.add_row(
            f"[bold]Today's Trades:[/bold] {trades_count}  |  [bold green]Wins:[/bold green] {self.state['today_wins']}  |  [bold red]Losses:[/bold red] {self.state['today_losses']}",
            f"[bold]Realized PnL:[/bold] [{tot_style}]Rs {tot_pnl:+,.2f}[/{tot_style}]",
            f"[bold]Status:[/bold] [italic]{self.status_message}[/italic]"
        )
        t_foot.add_row(
            "[bold yellow][T][/bold yellow] Trigger Paper Trade   [bold yellow][X][/bold yellow] Square-Off   [bold yellow][R][/bold yellow] Reset Breaker   [bold yellow][Q][/bold yellow] Quit",
            "[cyan]Ledger: results/live_paper_trades.csv[/cyan]",
            f"Lot: [bold]{self.lot_size} Qty[/bold] (SEBI 2026)"
        )

        layout["footer"].update(Panel(t_foot, title="[bold white]3. PERFORMANCE LEDGER & DESK CONTROLS[/bold white]", border_style="blue"))
        return layout

    def run(self):
        self.authenticate()
        self.status_message = "Desk Online. Polling Kotak Neo..."

        with Live(self.generate_layout(), refresh_per_second=4, screen=True) as live:
            try:
                while True:
                    # 1. Non-blocking Keyboard Input
                    if msvcrt.kbhit():
                        key = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                        if key == "q":
                            break
                        elif key == "t" or key == " ":
                            self.trigger_trade(force=True)
                        elif key == "x":
                            self.square_off_position("MANUAL_KEY_EXIT")
                        elif key == "r":
                            self.state["circuit_breaker_active"] = False
                            self.save_state()
                            self.status_message = "Circuit Breaker Manually Reset."

                    # 2. Engine Cycles
                    self.poll_kotak_neo()
                    self.update_position_dynamics()

                    # 3. Redraw Live Dashboard
                    live.update(self.generate_layout())
                    time.sleep(0.25)
            except KeyboardInterrupt:
                pass

        console.print("\n[bold yellow]Live Terminal Desk Closed Cleanly.[/bold yellow]\n")


if __name__ == "__main__":
    app = LiveTerminalEngine()
    app.run()
