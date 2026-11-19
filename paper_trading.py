import yfinance as yf
import pandas as pd
import json
import os
import time

from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

print("CONTINUOUS PAPER TRADING BOT STARTED")

# =========================
# SETTINGS
# =========================

CAPITAL = 10000
RISK = 50
MAX_TRADES = 2
TRADE_AMOUNT = 2000

SCAN_INTERVAL = 30

IST = ZoneInfo("Asia/Kolkata")

STOCKS = [
    "RELIANCE.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "TCS.NS",
    "INFY.NS",
    "TMPV.NS",
    "TMCV.NS",
    "AXISBANK.NS",
    "KOTAKBANK.NS",
    "BAJFINANCE.NS",
    "LT.NS",
    "BHARTIARTL.NS",
    "ITC.NS",
    "SUNPHARMA.NS",
    "HINDUNILVR.NS"
]

FILE = "paper_state.json"


# =========================
# LOAD STATE
# =========================

def load_state():

    if os.path.exists(FILE):

        with open(FILE) as f:
            state = json.load(f)

        state.setdefault("cash", CAPITAL)
        state.setdefault("positions", {})
        state.setdefault("history", [])
        state.setdefault("last_processed", {})
        state.setdefault("signals", {})

        return state

    return {
        "cash": CAPITAL,
        "positions": {},
        "history": [],
        "last_processed": {},
        "signals": {}
    }


# =========================
# SAVE STATE
# =========================

def save_state(state):

    state["updated"] = datetime.now(IST).isoformat()

    temp_file = FILE + ".tmp"

    with open(temp_file, "w") as f:
        json.dump(state, f, indent=4)

    os.replace(temp_file, FILE)


# =========================
# MARKET DATA
# =========================

def get_data(stock):

    df = yf.download(
        stock,
        period="5d",
        interval="1m",
        progress=False,
        threads=False
    )

    if df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.sort_index()

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


# =========================
# STOCK SCANNER
# =========================

def scan_stock(stock, state):

    try:

        df = get_data(stock)

        if df is None or len(df) < 50:

            print("INSUFFICIENT DATA:", stock)
            return

        candle = df.iloc[-1]
        previous = df.iloc[-21:-1]

        candle_time = str(df.index[-1])

        price = float(candle["Close"])

        low = float(candle["Low"])
        high = float(candle["High"])

        breakout = (
            price > float(previous["High"].max())
        )

        volume_spike = (
            candle["Volume"] >
            candle["AVG_VOLUME"] * 1.5
        )

        trend = (
            candle["EMA20"] > candle["EMA50"]
        )

        rsi = float(candle["RSI"])

        rsi_ok = 50 < rsi < 70

        signal = "WAIT"

        if (
            breakout
            and volume_spike
            and trend
            and rsi_ok
        ):
            signal = "BUY"

        # =====================
        # DASHBOARD SIGNAL DATA
        # =====================

        state["signals"][stock] = {

            "price": round(price, 2),

            "trend": bool(trend),

            "rsi": round(rsi, 2),

            "signal": signal,

            "updated": datetime.now(IST).isoformat()

        }

        print("\nSTOCK:", stock)
        print("PRICE:", price)
        print("TREND:", trend)
        print("RSI:", round(rsi, 2))
        print("SIGNAL:", signal)

        # =====================
        # PREVENT DUPLICATE TRADE
        # =====================

        if state["last_processed"].get(stock) == candle_time:
            return

        state["last_processed"][stock] = candle_time

        # =====================
        # EXIT CHECK
        # =====================

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
                    "Time": datetime.now(IST).isoformat()

                })

                print("SELL:", stock)
                print("P&L:", round(pnl, 2))

                del state["positions"][stock]

            return

        # =====================
        # BUY CONDITIONS
        # =====================

        if signal == "BUY":

            if len(state["positions"]) >= MAX_TRADES:
                return

            stop = float(
                previous["Low"].tail(5).min()
            )

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

            print("\n===== BUY SIGNAL =====")
            print("STOCK:", stock)
            print("ENTRY:", price)
            print("STOP LOSS:", stop)
            print("TARGET:", target)
            print("QUANTITY:", qty)

    except Exception as e:

        print("ERROR:", stock, e)


# =========================
# PORTFOLIO
# =========================

def show_portfolio(state):

    print("\n===== PORTFOLIO =====")

    print("CASH:", round(state["cash"], 2))

    print("OPEN TRADES:", len(state["positions"]))

    print("CLOSED TRADES:", len(state["history"]))

    total_pnl = sum(
        t["P&L"] for t in state["history"]
    )

    print("TOTAL REALIZED P&L:", round(total_pnl, 2))


# =========================
# MAIN ENGINE
# =========================

def main():

    now = datetime.now(IST)

    market_start = dt_time(9, 15)
    market_split = dt_time(15, 0)
    market_end = dt_time(15, 30)

    print("\nCURRENT TIME:", now)

    if now.time() < market_start:

        print("MARKET NOT STARTED")
        return

    if now.time() >= market_end:

        print("MARKET CLOSED")
        return

    if now.time() < market_split:

        stop_time = market_split

    else:

        stop_time = market_end

    end_time = datetime.combine(
        now.date(),
        stop_time,
        tzinfo=IST
    )

    state = load_state()

    print("\n===== CONTINUOUS TRADING STARTED =====")

    print("START:", now)
    print("STOP:", end_time)

    # =====================
    # CONTINUOUS LOOP
    # =====================

    while datetime.now(IST) < end_time:

        print("\n-------------------------")

        print("SCAN TIME:", datetime.now(IST))

        for stock in STOCKS:

            if datetime.now(IST) >= end_time:
                break

            scan_stock(stock, state)

            # Save after every stock so dashboard
            # can see the latest available data.

            save_state(state)

        show_portfolio(state)

        remaining = (
            end_time - datetime.now(IST)
        ).total_seconds()

        if remaining > 0:

            time.sleep(min(SCAN_INTERVAL, remaining))

    save_state(state)

    print("\n===== TRADING SESSION COMPLETED =====")

    show_portfolio(state)

    print("BOT STOPPED:", datetime.now(IST))


if __name__ == "__main__":

    main()