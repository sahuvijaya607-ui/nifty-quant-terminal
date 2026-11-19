import os, sys, json, time
from dotenv import load_dotenv
load_dotenv()
from neo_api_client import NeoAPI
import pyotp

consumer_key = os.getenv('KOTAK_CONSUMER_KEY')
mobile = os.getenv('KOTAK_MOBILE_NUMBER')
if mobile and not mobile.startswith('+91'):
    mobile = f'+91{mobile}'
ucc = os.getenv('KOTAK_UCC')
totp_sec = os.getenv('KOTAK_TOTP_SECRET')
mpin = os.getenv('KOTAK_MPIN', '031995')

print(f"Connecting Kotak Neo API for UCC: {ucc} to execute 100% SQUARE-OFF...")
client = NeoAPI(consumer_key=consumer_key, environment='prod')
totp = pyotp.TOTP(totp_sec).now()
client.totp_login(mobile_number=mobile, ucc=ucc, totp=totp)
client.totp_validate(mpin=mpin)

# 1. Fetch current live positions
pos = client.positions()
positions_data = pos.get('data', []) if isinstance(pos, dict) else []

short_legs = []
long_legs = []

for p in positions_data:
    buy_q = int(p.get('flBuyQty', 0))
    sell_q = int(p.get('flSellQty', 0))
    net_q = buy_q - sell_q
    sym = p.get('trdSym')
    if net_q < 0:
        short_legs.append((sym, abs(net_q), p))
    elif net_q > 0:
        long_legs.append((sym, abs(net_q), p))

print(f"Found {len(short_legs)} Short legs to BUY back and {len(long_legs)} Long legs to SELL back.")

# 2. Square-off Short Legs FIRST (Market Buy)
for sym, qty, p in short_legs:
    print(f"--> Exiting SHORT leg: BUY {qty} of {sym} at MKT...")
    res = client.place_order(
        exchange_segment='nse_fo',
        product='NRML',
        price='0',
        order_type='MKT',
        quantity=str(qty),
        validity='DAY',
        trading_symbol=sym,
        transaction_type='B'
    )
    print("    Order Response:", res)

time.sleep(0.5)

# 3. Square-off Long Legs SECOND (Market Sell)
for sym, qty, p in long_legs:
    print(f"--> Exiting LONG leg: SELL {qty} of {sym} at MKT...")
    res = client.place_order(
        exchange_segment='nse_fo',
        product='NRML',
        price='0',
        order_type='MKT',
        quantity=str(qty),
        validity='DAY',
        trading_symbol=sym,
        transaction_type='S'
    )
    print("    Order Response:", res)

time.sleep(1.5)

# 4. Verify Final Positions
pos_final = client.positions()
final_data = pos_final.get('data', []) if isinstance(pos_final, dict) else []
all_closed = True
print("\n=== FINAL DEMAT POSITIONS AUDIT ===")
for p in final_data:
    buy_q = int(p.get('flBuyQty', 0))
    sell_q = int(p.get('flSellQty', 0))
    net_q = buy_q - sell_q
    sym = p.get('trdSym')
    buy_amt = float(p.get('buyAmt', 0))
    sell_amt = float(p.get('sellAmt', 0))
    realized_pnl = sell_amt - buy_amt
    print(f"Symbol: {sym} | Net Qty: {net_q} | Buy: {buy_q} (Amt {buy_amt}) | Sell: {sell_q} (Amt {sell_amt}) | Realized PnL: Rs {realized_pnl:+.2f}")
    if net_q != 0:
        all_closed = False

if all_closed:
    print("\nSUCCESS: ALL POSITIONS ARE 100% SQUARED OFF (NET QTY: 0)!")
else:
    print("\nWARNING: SOME POSITIONS STILL REMAIN OPEN!")

# 5. Fetch Recent Orders
orders = client.order_report()
if isinstance(orders, dict) and 'data' in orders:
    print("\n=== LATEST ORDERS EXECUTED ===")
    for o in orders['data'][:4]:
        print(f"Order #{o.get('nOrdNo')}: {o.get('trdSym')} {o.get('trnsTp')} {o.get('qty')} @ avg {o.get('avgPrc')} - Status: {o.get('ordSt')}")
