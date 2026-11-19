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

print(f"Connecting Kotak Neo for UCC: {ucc}...")
client = NeoAPI(consumer_key=consumer_key, environment='prod')
totp = pyotp.TOTP(totp_sec).now()
client.totp_login(mobile_number=mobile, ucc=ucc, totp=totp)
client.totp_validate(mpin=mpin)

# Verify position before exiting
pos = client.positions()
positions_data = pos.get('data', []) if isinstance(pos, dict) else []
extra_leg = None
for p in positions_data:
    if p.get('trdSym') == 'NIFTY26NOV25000PE':
        net_qty = int(p.get('flBuyQty', 0)) - int(p.get('flSellQty', 0))
        if net_qty > 0:
            extra_leg = p
            print(f"Found open extra leg: {p.get('trdSym')} Net Qty = {net_qty}")

if not extra_leg:
    print("No open extra leg found for NIFTY26NOV25000PE or already squared off!")
    sys.exit(0)

# Execute Market Sell
print("Placing EXIT order: SELL 65 Qty NIFTY26NOV25000PE at MKT...")
order_res = client.place_order(
    exchange_segment='nse_fo',
    product='NRML',
    price='0',
    order_type='MKT',
    quantity='65',
    validity='DAY',
    trading_symbol='NIFTY26NOV25000PE',
    transaction_type='S'
)
print("Order Response:", json.dumps(order_res, indent=2))

time.sleep(1.5)

# Verify order status
orders = client.order_report()
if isinstance(orders, dict) and 'data' in orders:
    print("\n--- Recent Orders ---")
    for o in orders['data'][:3]:
        print(f"Order #{o.get('nOrdNo')}: {o.get('trdSym')} {o.get('trnsTp')} {o.get('qty')} @ avg {o.get('avgPrc')} - Status: {o.get('ordSt')} (Rej: {o.get('rejRsn', 'None')})")

# Verify positions after exit
pos_after = client.positions()
print("\n--- Current Open Positions After Exit ---")
if isinstance(pos_after, dict) and 'data' in pos_after:
    for p in pos_after['data']:
        net_qty = int(p.get('flBuyQty', 0)) - int(p.get('flSellQty', 0))
        print(f"Symbol: {p.get('trdSym')} | Net Qty: {net_qty} | Buy Qty: {p.get('flBuyQty')} | Sell Qty: {p.get('flSellQty')}")
