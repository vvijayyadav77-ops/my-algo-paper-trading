import yfinance as yf
import pandas as pd
import json
import os
import time
from datetime import datetime, timedelta

print("CONTINUOUS PAPER TRADING BOT STARTED")

CAPITAL = 10000
RISK = 50
MAX_TRADES = 2
TRADE_AMOUNT = 2000

SCAN_INTERVAL = 30
RUN_HOURS = 5.75

STOCKS = [
"RELIANCE.NS",
"HDFCBANK.NS",
"ICICIBANK.NS",
"SBIN.NS",
"TCS.NS",
"INFY.NS",
"TMPV.NS",
"TMCV.NS"
]

FILE = "paper_state.json"

def load_state():
if os.path.exists(FILE):
with open(FILE) as f:
return json.load(f)

return {
    "cash": CAPITAL,
    "positions": {},
    "history": [],
    "last_processed": {}
}

def save_state(state):
with open(FILE, "w") as f:
json.dump(state, f, indent=4)

def get_data(stock):
df = yf.download(
stock,
period="5d",
interval="5m",
progress=False
)

if df.empty:
    return None

if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

df["EMA20"] = df["Close"].ewm(span=20).mean()
df["EMA50"] = df["Close"].ewm(span=50).mean()

delta = df["Close"].diff()

gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

avg_gain = gain.rolling(14).mean()
avg_loss = loss.rolling(14).mean()

rs = avg_gain / avg_loss.replace(0, float("nan"))

df["RSI"] = 100 - (100 / (1 + rs))

df["AVG_VOLUME"] = df["Volume"].rolling(20).mean()

return df.dropna()

def scan_stock(stock, state):

try:
    df = get_data(stock)

    if df is None or len(df) < 50:
        return

    candle = df.iloc[-1]
    previous = df.iloc[-21:-1]

    candle_time = str(df.index[-1])

    if state["last_processed"].get(stock) == candle_time:
        return

    state["last_processed"][stock] = candle_time

    price = float(candle["Close"])
    low = float(candle["Low"])
    high = float(candle["High"])

    print("\nStock:", stock)
    print("Price:", price)

    # EXIT CHECK

    if stock in state["positions"]:

        pos = state["positions"][stock]

        exit_price = None

        if low <= pos["stop"]:
            exit_price = pos["stop"]

        elif high >= pos["target"]:
            exit_price = pos["target"]

        if exit_price is not None:

            pnl = (
                exit_price - pos["entry"]
            ) * pos["qty"]

            state["cash"] += (
                exit_price * pos["qty"]
            )

            state["history"].append({
                "Stock": stock,
                "Entry": pos["entry"],
                "Exit": exit_price,
                "Quantity": pos["qty"],
                "P&L": round(pnl, 2),
                "Time": datetime.now().isoformat()
            })

            print("SELL:", stock)
            print("P&L:", round(pnl, 2))

            del state["positions"][stock]

        return

    # BUY CONDITIONS

    breakout = price > float(previous["High"].max())

    volume_spike = (
        candle["Volume"] >
        candle["AVG_VOLUME"] * 1.5
    )

    trend = candle["EMA20"] > candle["EMA50"]

    rsi_ok = 50 < candle["RSI"] < 70

    if breakout and volume_spike and trend and rsi_ok:

        if len(state["positions"]) >= MAX_TRADES:
            return

        stop = float(previous["Low"].tail(5).min())

        if stop <= 0 or stop >= price:
            return

        risk_per_share = price - stop

        qty = min(
            int(RISK / risk_per_share),
            int(TRADE_AMOUNT / price)
        )

        if qty <= 0:
            return

        cost = qty * price

        if cost > state["cash"]:
            return

        target = price + (2 * risk_per_share)

        state["cash"] -= cost

        state["positions"][stock] = {
            "entry": price,
            "stop": stop,
            "target": target,
            "qty": qty
        }

        print("BUY:", stock)
        print("Entry:", price)
        print("Stop:", stop)
        print("Target:", target)

except Exception as e:
    print("ERROR:", stock, e)

def main():

state = load_state()

start_time = datetime.now()
end_time = start_time + timedelta(hours=RUN_HOURS)

print("\n===== CONTINUOUS PAPER TRADING =====")
print("Start:", start_time)
print("End:", end_time)

while datetime.now() < end_ti