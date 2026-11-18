import yfinance as yf
import pandas as pd
import json
import os
from datetime import datetime

CAPITAL = 10000
RISK = 50
MAX_TRADES = 2
TRADE_AMOUNT = 2000

STOCKS = [
    "RELIANCE.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "TCS.NS",
    "INFY.NS",
    "ITC.NS",
    "TATAMOTORS.NS"
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


def main():

    state = load_state()

    print("\n===== PAPER TRADING ENGINE =====")
    print("Time:", datetime.now())

    for stock in STOCKS:

        try:
            df = get_data(stock)

            if df is None or len(df) < 50:
                continue

            candle = df.iloc[-1]
            previous = df.iloc[-21:-1]

            candle_time = str(df.index[-1])

            if state["last_processed"].get(stock) == candle_time:
                continue

            state["last_processed"][stock] = candle_time

            price = float(candle["Close"])
            low = float(candle["Low"])
            high = float(candle["High"])

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

                    print("SELL:", stock, "P&L:", pnl)

                    del state["positions"][stock]

                continue

            # BUY CONDITIONS

            breakout = price > float(previous["High"].max())

            volume_spike = (
                candle["Volume"] >
                candle["AVG_VOLUME"] * 1.5
            )

            trend = candle["EMA20"] > candle["EMA50"]

            rsi_ok = 50 < candle["RSI"] < 70

            if (
                breakout
                and volume_spike
                and trend
                and rsi_ok
            ):

                if len(state["positions"]) >= MAX_TRADES:
                    continue

                stop = float(previous["Low"].tail(5).min())

                if stop <= 0 or stop >= price:
                    continue

                risk_per_share = price - stop

                qty = min(
                    int(RISK / risk_per_share),
                    int(TRADE_AMOUNT / price)
                )

                if qty <= 0:
                    continue

                cost = qty * price

                if cost > state["cash"]:
                    continue

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

    save_state(state)

    print("\n===== PORTFOLIO =====")
    print("Cash:", round(state["cash"], 2))
    print("Open Trades:", len(state["positions"]))
    print("Closed Trades:", len(state["history"]))

    total_pnl = sum(
        t["P&L"] for t in state["history"]
    )

    print("Total Realized P&L:", round(total_pnl, 2))


if __name__ == "__main__":
    main()
