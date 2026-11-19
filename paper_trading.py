import yfinance as yf
import pandas as pd
import json
import os
import time

from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

print("PAPER TRADING BOT V4 STARTED")

# =========================
# SETTINGS
# =========================

CAPITAL = 10000
RISK = 50
MAX_TRADES = 2
TRADE_AMOUNT = 2000

SCAN_INTERVAL = 30

BROKERAGE_RATE = 0.0005
MAX_BROKERAGE = 10

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
# BROKERAGE
# =========================

def calculate_brokerage(amount):

    return round(
        min(MAX_BROKERAGE, amount * BROKERAGE_RATE),
        2
    )


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

    df["EMA9"] = df["Close"].ewm(
        span=9,
        adjust=False
    ).mean()

    df["EMA20"] = df["Close"].ewm(
        span=20,
        adjust=False
    ).mean()

    delta = df["Close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))

    df["RSI"] = 100 - (100 / (1 + rs))

    return df.dropna()


# =========================
# CLOSE POSITION
# =========================

def close_position(state, stock, exit_price, reason):

    if stock not in state["positions"]:
        return

    pos = state["positions"][stock]

    qty = int(pos["qty"])

    entry = float(pos["entry"])

    buy_value = entry * qty

    sell_value = exit_price * qty

    buy_brokerage = float(
        pos.get("buy_brokerage", 0)
    )

    sell_brokerage = calculate_brokerage(sell_value)

    gross_pnl = (exit_price - entry) * qty

    total_brokerage = (
        buy_brokerage + sell_brokerage
    )

    net_pnl = gross_pnl - total_brokerage

    # Return sale proceeds after sell brokerage

    state["cash"] += (
        sell_value - sell_brokerage
    )

    # Save trade history

    state["history"].append({

        "Stock": stock,

        "Entry": round(entry, 2),

        "Exit": round(exit_price, 2),

        "Quantity": qty,

        "Buy Value": round(buy_value, 2),

        "Sell Value": round(sell_value, 2),

        "Buy Brokerage": round(buy_brokerage, 2),

        "Sell Brokerage": round(sell_brokerage, 2),

        "Total Brokerage": round(total_brokerage, 2),

        "Gross P&L": round(gross_pnl, 2),

        "P&L": round(net_pnl, 2),

        "Exit Reason": reason,

        "Time": datetime.now(IST).isoformat()

    })

    print("\n===== POSITION CLOSED =====")

    print("STOCK:", stock)
    print("EXIT:", exit_price)
    print("GROSS P&L:", round(gross_pnl, 2))
    print("BROKERAGE:", round(total_brokerage, 2))
    print("NET P&L:", round(net_pnl, 2))

    del state["positions"][stock]

    save_state(state)


# =========================
# FORCE SQUARE OFF
# =========================

def square_off_all(state):

    print("\n===== FORCE SQUARE OFF =====")

    for stock in list(state["positions"].keys()):

        pos = state["positions"][stock]

        exit_price = None

        try:

            df = get_data(stock)

            if df is not None and not df.empty:

                value = float(df["Close"].iloc[-1])

                if value > 0:
                    exit_price = value

        except Exception as e:

            print("PRICE ERROR:", stock, e)

        # Fallback to saved dashboard price

        if exit_price is None:

            saved_price = state["signals"].get(
                stock, {}
            ).get("price")

            if saved_price and float(saved_price) > 0:

                exit_price = float(saved_price)

        # Final fallback

        if exit_price is None:

            exit_price = float(pos["entry"])

            print("WARNING: ENTRY PRICE FALLBACK:", stock)

        close_position(
            state,
            stock,
            exit_price,
            "FORCE SQUARE OFF"
        )

    save_state(state)

    print("\nALL POSITIONS CLOSED")


# =========================
# STOCK SCANNER
# =========================

def scan_stock(stock, state):

    try:

        df = get_data(stock)

        if df is None or len(df) < 30:

            print("INSUFFICIENT DATA:", stock)
            return

        candle = df.iloc[-1]

        candle_time = str(df.index[-1])

        price = float(candle["Close"])
        low = float(candle["Low"])
        high = float(candle["High"])

        ema9 = float(candle["EMA9"])
        ema20 = float(candle["EMA20"])
        rsi = float(candle["RSI"])

        trend = ema9 > ema20

        rsi_ok = 45 < rsi < 75

        signal = "WAIT"

        if trend and rsi_ok:
            signal = "BUY"

        # DASHBOARD DATA

        state["signals"][stock] = {

            "price": round(price, 2),

            "trend": bool(trend),

            "rsi": round(rsi, 2),

            "signal": signal,

            "updated": datetime.now(IST).isoformat()

        }

        print("\nSTOCK:", stock)
        print("PRICE:", price)
        print("EMA9:", round(ema9, 2))
        print("EMA20:", round(ema20, 2))
        print("RSI:", round(rsi, 2))
        print("SIGNAL:", signal)

        # EXIT CHECK

        if stock in state["positions"]:

            pos = state["positions"][stock]

            exit_price = None

            reason = None

            if low <= pos["stop"]:

                exit_price = float(pos["stop"])
                reason = "STOP LOSS"

            elif high >= pos["target"]:

                exit_price = float(pos["target"])
                reason = "TARGET"

            if exit_price is not None:

                close_position(
                    state,
                    stock,
                    exit_price,
                    reason
                )

            return

        # BUY CHECK

        if state["last_processed"].get(stock) == candle_time:

            return

        state["last_processed"][stock] = candle_time

        if signal != "BUY":
            return

        if len(state["positions"]) >= MAX_TRADES:

            print("MAX TRADES REACHED")
            return

        # STOP LOSS

        stop = float(
            df["Low"].iloc[-6:-1].min()
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

        buy_brokerage = calculate_brokerage(cost)

        total_cost = cost + buy_brokerage

        if total_cost > state["cash"]:

            print("INSUFFICIENT CASH")
            return

        target = price + (1.5 * risk_per_share)

        # EXECUTE BUY

        state["cash"] -= total_cost

        state["positions"][stock] = {

            "entry": price,

            "stop": stop,

            "target": target,

            "qty": qty,

            "buy_brokerage": buy_brokerage

        }

        print("\n===== BUY EXECUTED =====")

        print("STOCK:", stock)
        print("ENTRY:", price)
        print("QUANTITY:", qty)
        print("INVESTMENT:", round(cost, 2))
        print("BUY BROKERAGE:", buy_brokerage)

    except Exception as e:

        print("ERROR:", stock, e)


# =========================
# PORTFOLIO
# =========================

def show_portfolio(state):

    print("\n===== PORTFOLIO =====")

    total_pnl = sum(
        t["P&L"] for t in state["history"]
    )

    total_brokerage = sum(
        t.get("Total Brokerage", 0)
        for t in state["history"]
    )

    print("AVAILABLE CASH:", round(state["cash"], 2))

    print("OPEN TRADES:", len(state["positions"]))

    print("CLOSED TRADES:", len(state["history"]))

    print("TOTAL BROKERAGE:", round(total_brokerage, 2))

    print("NET REALIZED P&L:", round(total_pnl, 2))


# =========================
# MAIN ENGINE
# =========================

def main():

    now = datetime.now(IST)

    market_start = dt_time(9, 15)
    market_split = dt_time(15, 0)

    state = load_state()

    print("\nCURRENT TIME:", now)

    # BEFORE MARKET

    if now.time() < market_start:

        print("MARKET NOT STARTED")
        return

    # 3 PM OR LATER

    if now.time() >= market_split:

        print("SQUARE OFF TIME REACHED")

        square_off_all(state)

        show_portfolio(state)

        return

    # TRADING SESSION

    end_time = datetime.combine(
        now.date(),
        market_split,
        tzinfo=IST
    )

    print("\n===== TRADING STARTED =====")

    while datetime.now(IST) < end_time:

        for stock in STOCKS:

            # Check 3 PM before every stock

            if datetime.now(IST) >= end_time:
                break

            scan_stock(stock, state)

            save_state(state)

        show_portfolio(state)

        remaining = (
            end_time - datetime.now(IST)
        ).total_seconds()

        if remaining > 0:

            time.sleep(
                min(SCAN_INTERVAL, remaining)
            )

    # FINAL SQUARE OFF

    print("\n===== 3 PM SQUARE OFF =====")

    square_off_all(state)

    show_portfolio(state)

    print("\nTRADING SESSION COMPLETED")


if __name__ == "__main__":

    main()