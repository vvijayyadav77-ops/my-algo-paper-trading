import yfinance as yf
import pandas as pd
import json
import os
import time

from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

print("PAPER TRADING BOT V5 STARTED")

# =========================================================
# SETTINGS
# =========================================================
CAPITAL = 10000.0
RISK_PER_TRADE = 50.0
MAX_TRADES = 2
TRADE_AMOUNT = 2000.0
SCAN_INTERVAL = 30

# Estimate only: 0.05% per order, capped at Rs 10 per order.
# This is NOT a complete Kotak Neo/Groww charge schedule.
BROKERAGE_RATE = 0.0005
MAX_BROKERAGE = 10.0

VOLUME_MULTIPLIER = 1.5
REWARD_RISK = 1.5
IST = ZoneInfo("Asia/Kolkata")

MARKET_START = dt_time(9, 15)
SQUARE_OFF_TIME = dt_time(15, 0)

STOCKS = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS",
    "TCS.NS", "INFY.NS", "TMPV.NS", "TMCV.NS",
    "AXISBANK.NS", "KOTAKBANK.NS", "BAJFINANCE.NS", "LT.NS",
    "BHARTIARTL.NS", "ITC.NS", "SUNPHARMA.NS", "HINDUNILVR.NS"
]

FILE = "paper_state.json"


# =========================================================
# HELPERS
# =========================================================
def now_ist():
    return datetime.now(IST)


def money(value):
    return round(float(value or 0), 2)


def calculate_brokerage(amount):
    amount = max(0.0, float(amount))
    return money(min(MAX_BROKERAGE, amount * BROKERAGE_RATE))


def empty_state():
    return {
        "capital": CAPITAL,
        "cash": CAPITAL,
        "positions": {},
        "history": [],
        "last_processed": {},
        "signals": {},
        "updated": None,
        "bot_status": "INITIALIZED",
        "square_off_status": "NOT DUE"
    }


def load_state():
    if not os.path.exists(FILE):
        return empty_state()

    try:
        with open(FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        print("WARNING: Could not read state file. Starting a fresh state.")
        return empty_state()

    # Keep existing user's capital, positions and history.
    state.setdefault("capital", CAPITAL)
    state.setdefault("cash", CAPITAL)
    state.setdefault("positions", {})
    state.setdefault("history", [])
    state.setdefault("last_processed", {})
    state.setdefault("signals", {})
    state.setdefault("bot_status", "RUNNING")
    state.setdefault("square_off_status", "NOT DUE")

    return state


def save_state(state):
    state["updated"] = now_ist().isoformat()
    temp_file = FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    os.replace(temp_file, FILE)


# =========================================================
# MARKET DATA + INDICATORS
# =========================================================
def get_data(stock):
    try:
        df = yf.download(
            stock,
            period="5d",
            interval="1m",
            progress=False,
            threads=False,
            auto_adjust=False
        )

        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(col in df.columns for col in required):
            return None

        df = df[required].copy()
        for col in required:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna().sort_index()
        if df.empty:
            return None

        # Yahoo timestamps are normally timezone-aware. Normalize to IST.
        if df.index.tz is None:
            df.index = df.index.tz_localize(IST)
        else:
            df.index = df.index.tz_convert(IST)

        # Indicators use the available intraday history.
        df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
        df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()

        # Session VWAP: reset at the start of each IST trading date.
        session_date = now_ist().date()
        today = df[df.index.date == session_date].copy()

        if today.empty:
            return None

        typical_price = (today["High"] + today["Low"] + today["Close"]) / 3
        volume_sum = today["Volume"].cumsum().replace(0, float("nan"))
        today["VWAP"] = (typical_price * today["Volume"]).cumsum() / volume_sum
        today["VOL_AVG20"] = today["Volume"].rolling(20).mean()

        # RSI(14), retained for dashboard/context only.
        delta = today["Close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        today["RSI"] = 100 - (100 / (1 + rs))

        return today.dropna()

    except Exception as exc:
        print("DATA ERROR:", stock, str(exc))
        return None


# =========================================================
# PORTFOLIO METRICS
# =========================================================
def portfolio_metrics(state):
    realized = sum(float(t.get("P&L", 0)) for t in state["history"])
    closed_brokerage = sum(
        float(t.get("Total Brokerage", 0)) for t in state["history"]
    )

    market_value = 0.0
    open_cost = 0.0
    open_buy_brokerage = 0.0
    open_estimated_sell_brokerage = 0.0
    unrealized_gross = 0.0

    for stock, pos in state["positions"].items():
        qty = int(pos.get("qty", 0))
        entry = float(pos.get("entry", 0))
        ltp = float(state["signals"].get(stock, {}).get("price", entry))

        market_value += ltp * qty
        open_cost += entry * qty
        open_buy_brokerage += float(pos.get("buy_brokerage", 0))
        open_estimated_sell_brokerage += calculate_brokerage(ltp * qty)
        unrealized_gross += (ltp - entry) * qty

    open_estimated_charges = open_buy_brokerage + open_estimated_sell_brokerage
    unrealized_net = unrealized_gross - open_estimated_charges
    total_brokerage = closed_brokerage + open_buy_brokerage
    equity = float(state["cash"]) + market_value
    total_net_pnl = realized + unrealized_net

    return {
        "realized_pnl": money(realized),
        "unrealized_gross": money(unrealized_gross),
        "unrealized_net": money(unrealized_net),
        "closed_brokerage": money(closed_brokerage),
        "open_estimated_charges": money(open_estimated_charges),
        "total_brokerage_paid": money(total_brokerage),
        "total_brokerage_including_estimated_exit": money(
            closed_brokerage + open_estimated_charges
        ),
        "market_value": money(market_value),
        "invested_amount": money(open_cost),
        "equity": money(equity),
        "total_net_pnl": money(total_net_pnl)
    }


# =========================================================
# CLOSE POSITION
# =========================================================
def close_position(state, stock, exit_price, reason):
    if stock not in state["positions"]:
        return False

    pos = state["positions"][stock]
    qty = int(pos["qty"])
    entry = float(pos["entry"])
    exit_price = float(exit_price)

    if qty <= 0 or exit_price <= 0:
        print("INVALID EXIT; POSITION KEPT OPEN:", stock)
        return False

    buy_value = entry * qty
    sell_value = exit_price * qty
    buy_brokerage = float(pos.get("buy_brokerage", 0))
    sell_brokerage = calculate_brokerage(sell_value)
    gross_pnl = (exit_price - entry) * qty
    total_brokerage = buy_brokerage + sell_brokerage
    net_pnl = gross_pnl - total_brokerage

    # Buy amount + buy brokerage was deducted at entry.
    # On exit, add sale proceeds after estimated sell brokerage.
    state["cash"] += sell_value - sell_brokerage

    state["history"].append({
        "Stock": stock,
        "Entry": money(entry),
        "Exit": money(exit_price),
        "Quantity": qty,
        "Buy Value": money(buy_value),
        "Sell Value": money(sell_value),
        "Buy Brokerage": money(buy_brokerage),
        "Sell Brokerage": money(sell_brokerage),
        "Total Brokerage": money(total_brokerage),
        "Gross P&L": money(gross_pnl),
        "P&L": money(net_pnl),
        "Exit Reason": reason,
        "Time": now_ist().isoformat()
    })

    print(
        f"CLOSED {stock} | qty={qty} | exit={exit_price:.2f} "
        f"| brokerage={total_brokerage:.2f} | net={net_pnl:.2f} "
        f"| reason={reason}"
    )

    del state["positions"][stock]
    save_state(state)
    return True


# =========================================================
# SQUARE OFF ALL OPEN POSITIONS
# =========================================================
def square_off_all(state):
    print("\n===== FORCE SQUARE OFF REQUESTED =====")
    state["square_off_status"] = "IN PROGRESS"
    save_state(state)

    for stock in list(state["positions"].keys()):
        exit_price = None

        # Try a fresh Yahoo Finance price first.
        df = get_data(stock)
        if df is not None and not df.empty:
            candidate = float(df["Close"].iloc[-1])
            if candidate > 0:
                exit_price = candidate

        # Use last scanner price only if it is from today and valid.
        if exit_price is None:
            signal = state["signals"].get(stock, {})
            saved_price = signal.get("price")
            saved_time = signal.get("updated")

            if saved_price and saved_time:
                try:
                    saved_dt = datetime.fromisoformat(saved_time)
                    if saved_dt.tzinfo is None:
                        saved_dt = saved_dt.replace(tzinfo=IST)
                    age_seconds = (now_ist() - saved_dt.astimezone(IST)).total_seconds()
                    if 0 <= age_seconds <= 300 and float(saved_price) > 0:
                        exit_price = float(saved_price)
                except (ValueError, TypeError):
                    pass

        # Never invent an exit price or silently close at entry.
        if exit_price is None:
            print("SQUARE OFF PRICE UNAVAILABLE; POSITION LEFT OPEN:", stock)
            continue

        close_position(state, stock, exit_price, "FORCE SQUARE OFF")

    if state["positions"]:
        state["square_off_status"] = "PENDING - PRICE UNAVAILABLE"
        state["bot_status"] = "SQUARE OFF NEEDS RETRY"
    else:
        state["square_off_status"] = "COMPLETED"
        state["bot_status"] = "SESSION COMPLETED"

    save_state(state)
    print("REMAINING OPEN POSITIONS:", len(state["positions"]))


# =========================================================
# ENTRY / EXIT SIGNAL
# =========================================================
def scan_stock(stock, state):
    try:
        df = get_data(stock)
        if df is None or len(df) < 25:
            print("INSUFFICIENT DATA:", stock)
            return

        candle = df.iloc[-1]
        previous = df.iloc[-2]

        price = float(candle["Close"])
        low = float(candle["Low"])
        high = float(candle["High"])
        open_price = float(candle["Open"])
        ema9 = float(candle["EMA9"])
        ema21 = float(candle["EMA21"])
        vwap = float(candle["VWAP"])
        rsi = float(candle["RSI"])
        volume = float(candle["Volume"])
        volume_avg = float(candle["VOL_AVG20"])
        candle_time = str(df.index[-1])

        bullish_trend = (
            ema9 > ema21
            and ema9 > float(previous["EMA9"])
            and price > vwap
        )
        bearish_trend = (
            ema9 < ema21
            and ema9 < float(previous["EMA9"])
            and price < vwap
        )

        # Pullback-and-reclaim confirmation for long entries.
        bullish_pullback = (
            low <= ema9 * 1.002
            and price > ema9
            and price > open_price
        )
        bearish_pullback = (
            high >= ema9 * 0.998
            and price < ema9
            and price < open_price
        )
        volume_ok = volume > volume_avg * VOLUME_MULTIPLIER

        signal = "WAIT"
        if bullish_trend and bullish_pullback and volume_ok and 50 <= rsi <= 75:
            signal = "BUY"
        elif bearish_trend and bearish_pullback and volume_ok and 25 <= rsi <= 50:
            signal = "SELL"

        state["signals"][stock] = {
            "price": money(price),
            "trend": bool(ema9 > ema21),
            "rsi": money(rsi),
            "ema9": money(ema9),
            "ema21": money(ema21),
            "vwap": money(vwap),
            "volume": int(volume),
            "volume_avg20": money(volume_avg),
            "volume_ratio": round(volume / volume_avg, 2) if volume_avg else 0,
            "signal": signal,
            "candle_time": candle_time,
            "updated": now_ist().isoformat()
        }

        print(
            f"{stock} price={price:.2f} EMA9={ema9:.2f} "
            f"EMA21={ema21:.2f} VWAP={vwap:.2f} "
            f"VolRatio={(volume / volume_avg):.2f} signal={signal}"
        )

        # Manage an existing long position first.
        if stock in state["positions"]:
            pos = state["positions"][stock]

            if low <= float(pos["stop"]):
                close_position(state, stock, float(pos["stop"]), "STOP LOSS")
            elif high >= float(pos["target"]):
                close_position(state, stock, float(pos["target"]), "TARGET")
            elif signal == "SELL":
                close_position(state, stock, price, "BEARISH SIGNAL")
            return

        # One entry decision per Yahoo 1-minute candle.
        if state["last_processed"].get(stock) == candle_time:
            return
        state["last_processed"][stock] = candle_time

        if signal != "BUY":
            return

        if len(state["positions"]) >= MAX_TRADES:
            print("MAX OPEN TRADES REACHED")
            return

        # Use the preceding five candles' lows for stop calculation.
        stop = float(df["Low"].iloc[-6:-1].min())
        if stop <= 0 or stop >= price:
            print("INVALID STOP:", stock)
            return

        risk_per_share = price - stop
        qty_by_risk = int(RISK_PER_TRADE / risk_per_share)
        qty_by_amount = int(TRADE_AMOUNT / price)
        qty_by_cash = int(state["cash"] / (price * (1 + BROKERAGE_RATE)))
        qty = min(qty_by_risk, qty_by_amount, qty_by_cash)

        if qty <= 0:
            print("QUANTITY ZERO:", stock)
            return

        buy_value = qty * price
        buy_brokerage = calculate_brokerage(buy_value)
        total_cost = buy_value + buy_brokerage

        if total_cost > float(state["cash"]):
            print("INSUFFICIENT CASH:", stock)
            return

        target = price + (REWARD_RISK * risk_per_share)

        state["cash"] -= total_cost
        state["positions"][stock] = {
            "entry": money(price),
            "stop": money(stop),
            "target": money(target),
            "qty": int(qty),
            "buy_value": money(buy_value),
            "buy_brokerage": money(buy_brokerage),
            "entry_time": now_ist().isoformat()
        }

        print(
            f"BUY PAPER TRADE: {stock} | entry={price:.2f} "
            f"| qty={qty} | buy brokerage={buy_brokerage:.2f} "
            f"| SL={stop:.2f} | target={target:.2f}"
        )
        save_state(state)

    except Exception as exc:
        print("SCAN ERROR:", stock, str(exc))


# =========================================================
# PORTFOLIO SUMMARY
# =========================================================
def show_portfolio(state):
    metrics = portfolio_metrics(state)

    print("\n========== PORTFOLIO ==========")
    print("AVAILABLE CASH:", money(state["cash"]))
    print("INVESTED AMOUNT:", metrics["invested_amount"])
    print("MARKET VALUE:", metrics["market_value"])
    print("OPEN TRADES:", len(state["positions"]))
    print("CLOSED TRADES:", len(state["history"]))
    print("CLOSED BROKERAGE:", metrics["closed_brokerage"])
    print("OPEN ESTIMATED CHARGES:", metrics["open_estimated_charges"])
    print("REALIZED NET P&L:", metrics["realized_pnl"])
    print("UNREALIZED NET P&L:", metrics["unrealized_net"])
    print("TOTAL NET P&L:", metrics["total_net_pnl"])
    print("ESTIMATED EQUITY:", metrics["equity"])
    print("================================\n")


# =========================================================
# MAIN ENGINE
# =========================================================
def main():
    now = now_ist()
    state = load_state()

    print("CURRENT IST TIME:", now.isoformat())

    if now.weekday() >= 5:
        state["bot_status"] = "MARKET CLOSED - WEEKEND"
        save_state(state)
        print("Weekend. No trading.")
        return

    if now.time() < MARKET_START:
        state["bot_status"] = "WAITING FOR MARKET"
        save_state(state)
        print("Market has not started.")
        return

    if now.time() >= SQUARE_OFF_TIME:
        square_off_all(state)
        show_portfolio(state)
        return

    end_time = datetime.combine(now.date(), SQUARE_OFF_TIME, tzinfo=IST)
    state["bot_status"] = "TRADING"
    state["square_off_status"] = "NOT DUE"
    save_state(state)

    print("TRADING LOOP STARTED; SCAN INTERVAL:", SCAN_INTERVAL, "seconds")

    while now_ist() < end_time:
        for stock in STOCKS:
            if now_ist() >= end_time:
                break

            scan_stock(stock, state)
            save_state(state)

        show_portfolio(state)

        remaining = (end_time - now_ist()).total_seconds()
        if remaining > 0:
            time.sleep(min(SCAN_INTERVAL, remaining))

    # This runs if the same GitHub Actions job stays alive until 3 PM.
    square_off_all(state)
    show_portfolio(state)
    print("TRADING SESSION FINISHED")


if __name__ == "__main__":
    main()
