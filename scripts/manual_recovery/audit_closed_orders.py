import csv
from collections import defaultdict

csv_path = r"e:\sanatan logo\Closed orders.csv"

with open(csv_path, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"Total Rows in CSV: {len(rows)}\n")

# Chronological order
rows_chrono = list(reversed(rows))

pnl_by_sym = defaultdict(lambda: {"buy_qty": 0, "buy_amt": 0.0, "sell_qty": 0, "sell_amt": 0.0})

print(f"{'TIME':<20} | {'ORDER ID':<16} | {'SYMBOL':<26} | {'T':<4} | {'QTY':<4} | {'AVG PRC':<8} | {'STATUS':<9}")
print("-" * 95)

for r in rows_chrono:
    sym = r["Name"].strip()
    status = r["Status"].strip()
    ttype = r["Transaction Type"].strip()
    qty = int(r["Filled Quantity"])
    avg_prc = float(r["Average Price"])
    amt = qty * avg_prc
    t_str = r["Order Date and Time"].strip()
    ord_no = r["Order Number"].strip()
    
    print(f"{t_str:<20} | {ord_no:<16} | {sym[:26]:<26} | {ttype:<4} | {qty:<4} | {avg_prc:<8.2f} | {status:<9}")
    
    if status == "COMPLETED" and "ITBEES" not in sym:
        if ttype == "BUY":
            pnl_by_sym[sym]["buy_qty"] += qty
            pnl_by_sym[sym]["buy_amt"] += amt
        elif ttype == "SELL":
            pnl_by_sym[sym]["sell_qty"] += qty
            pnl_by_sym[sym]["sell_amt"] += amt

print("\n" + "="*50)
print("     DERIVATIVES (F&O) NET DEMAT AUDIT")
print("="*50)

total_realized_pnl = 0.0

for sym, d in sorted(pnl_by_sym.items()):
    net_qty = d["buy_qty"] - d["sell_qty"]
    pnl = d["sell_amt"] - d["buy_amt"]
    total_realized_pnl += pnl
    avg_buy = (d["buy_amt"] / d["buy_qty"]) if d["buy_qty"] > 0 else 0.0
    avg_sell = (d["sell_amt"] / d["sell_qty"]) if d["sell_qty"] > 0 else 0.0
    print(f"\nSymbol: {sym}")
    print(f"  Total Bought: {d['buy_qty']} Qty @ avg Rs {avg_buy:.2f} (Amt: Rs {d['buy_amt']:,.2f})")
    print(f"  Total Sold:   {d['sell_qty']} Qty @ avg Rs {avg_sell:.2f} (Amt: Rs {d['sell_amt']:,.2f})")
    print(f"  Net Qty:      {net_qty} Shares (Must be 0!)")
    print(f"  Realized PnL: Rs {pnl:+,.2f}")

print("\n" + "="*50)
print(f"TOTAL REALIZED F&O PNL TODAY: Rs {total_realized_pnl:+,.2f}")
print("="*50)
