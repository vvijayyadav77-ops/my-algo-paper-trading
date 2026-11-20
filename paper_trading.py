import yfinance as yf
import pandas as pd
import json
import os
import time

from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

print("PAPER TRADING BOT V7 STARTED")

# =========================================================
# SETTINGS
# =========================================================

CAPITAL = 10000.0
RISK_PER_TRADE = 50.0
MAX_TRADES = 2
TRADE_AMOUNT = 2000.0

SCAN_INTERVAL = 30

BROKERAGE_RATE = 0.0005
MAX_BROKERAGE = 10.0

VOLUME_MULTIPLIER = 1.5
REWARD_RISK = 1.5

MAX_DATA_AGE = 180

# MOMENTUM EXHAUSTION SETTINGS

SPIKE_LOOKBACK = 5
SPIKE_MIN_MOVE = 2.0

SPIKE_VOLUME_MULTIPLIER = 1.5

PEAK_PULLBACK_PERCENT = 0.5

DECLINING_VOLUME_CANDLES = 2

IST = ZoneInfo("Asia/Kolkata")

MARKET_START = dt_time(9, 15)
SQUARE_OFF_TIME = dt_time(15, 0)

STOCKS = [
    "RELIANCE.NS", "HDFCBANK.NS",
    "ICICIBANK.NS", "SBIN.NS",
    "TCS.NS", "INFY.NS",
    "TMPV.NS", "TMCV.NS",
    "AXISBANK.NS", "KOTAKBANK.NS",
    "BAJFINANCE.NS", "LT.NS",
    "BHARTIARTL.NS", "ITC.NS",
    "SUNPHARMA.NS", "HINDUNILVR.NS"
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

    return money(
        min(MAX_BROKERAGE, amount * BROKERAGE_RATE)
    )


def empty_state():

    return {

        "capital": CAPITAL,
        "cash": CAPITAL,

        "positions": {},
        "history": [],

        "last_processed": {},
        "signals": {},
        "data_health": {},

        "updated": None,

        "bot_status": "INITIALIZED",

        "square_off_status": "NOT DUE"

    }


# =========================================================
# STATE MANAGEMENT
# =========================================================

def load_state():

    if not os.path.exists(FILE):
        return empty_state()

    try:

        with open(FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            raise ValueError("Invalid state format")

        required = [
            "capital",
            "cash",
            "positions",
            "history"
        ]

        if not all(key in state for key in required):
            raise ValueError("Incomplete state file")

        state.setdefault("last_processed", {})
        state.setdefault("signals", {})
        state.setdefault("data_health", {})

        state.setdefault("bot_status", "RUNNING")

        state.setdefault(
            "square_off_status",
            "NOT DUE"
        )

        return state

    except Exception as exc:

        raise RuntimeError(
            "STATE FILE ERROR. TRADING HALTED: "
            + str(exc)
        )


def save_state(state):

    state["updated"] = now_ist().isoformat()

    temp_file = FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:

        json.dump(state, f, indent=2)

    os.replace(temp_file, FILE)


# =========================================================
# DATA HEALTH
# =========================================================

def update_data_health(
    state,
    stock,
    status,
    message,
    candle_time=None,
    age=None
):

    state["data_health"][stock] = {

        "source": "Yahoo Finance",

        "status": status,

        "message": message,

        "candle_time": (
            str(candle_time)
            if candle_time else None
        ),

        "age_seconds": (
            round(age, 1)
            if age is not None else None
        ),

        "checked_at": now_ist().isoformat()

    }


# =========================================================
# SMART DATA MANAGEMENT
# =========================================================

def get_data(stock):

    try:

        df = yf.download(

            stock,

            period="5d",

            interval="1m",

            progress=False,

            threads=False,

            auto_adjust=False,

            timeout=15

        )

        if df is None or df.empty:
            return None, "DATA UNAVAILABLE"

        if isinstance(df.columns, pd.MultiIndex):

            df.columns = df.columns.get_level_values(0)

        required = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        if not all(col in df.columns for col in required):

            return None, "INVALID DATA FORMAT"

        df = df[required].copy()

        for col in required:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = df.dropna()

        if df.empty:
            return None, "EMPTY DATA"

        df = df[
            ~df.index.duplicated(keep="last")
        ]

        df = df.sort_index()

        # TIMEZONE

        if df.index.tz is None:

            df.index = df.index.tz_localize(IST)

        else:

            df.index = df.index.tz_convert(IST)

        # OHLC VALIDATION

        valid = (

            (df["Open"] > 0) &
            (df["High"] > 0) &
            (df["Low"] > 0) &
            (df["Close"] > 0) &
            (df["Volume"] >= 0) &
            (df["High"] >= df["Low"]) &
            (df["High"] >= df["Open"]) &
            (df["High"] >= df["Close"]) &
            (df["Low"] <= df["Open"]) &
            (df["Low"] <= df["Close"])

        )

        df = df[valid]

        if df.empty:
            return None, "INVALID OHLC DATA"

        # TODAY DATA ONLY

        today = now_ist().date()

        df = df[
            df.index.date == today
        ].copy()

        if df.empty:
            return None, "TODAY DATA UNAVAILABLE"

        # COMPLETED CANDLES ONLY

        cutoff = (

            now_ist()
            .replace(second=0, microsecond=0)
            - pd.Timedelta(minutes=1)

        )

        df = df[
            df.index <= cutoff
        ]

        if df.empty:

            return None, "WAITING FOR COMPLETED CANDLE"

        latest_time = df.index[-1]

        age = (

            now_ist()
            - latest_time.to_pydatetime()

        ).total_seconds()

        if age < 0:
            return None, "FUTURE TIMESTAMP"

        if age > MAX_DATA_AGE:
            return None, "STALE DATA"

        # EMA

        df["EMA9"] = df["Close"].ewm(
            span=9,
            adjust=False
        ).mean()

        df["EMA21"] = df["Close"].ewm(
            span=21,
            adjust=False
        ).mean()

        # VWAP

        typical_price = (

            df["High"]
            + df["Low"]
            + df["Close"]

        ) / 3

        volume_sum = (
            df["Volume"]
            .cumsum()
            .replace(0, float("nan"))
        )

        df["VWAP"] = (

            (typical_price * df["Volume"]).cumsum()
            / volume_sum

        )

        # VOLUME AVERAGE

        df["VOL_AVG20"] = (
            df["Volume"].rolling(20).mean()
        )

        # RSI

        delta = df["Close"].diff()

        gain = delta.clip(lower=0)

        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(14).mean()

        avg_loss = loss.rolling(14).mean()

        rs = avg_gain / avg_loss.replace(
            0,
            float("nan")
        )

        df["RSI"] = 100 - (
            100 / (1 + rs)
        )

        # RSI SPECIAL CASES

        df.loc[
            (avg_loss == 0) & (avg_gain > 0),
            "RSI"
        ] = 100

        df.loc[
            (avg_loss == 0) & (avg_gain == 0),
            "RSI"
        ] = 50

        df = df.dropna()

        if len(df) < 25:

            return None, "INSUFFICIENT INDICATOR DATA"

        return df, "FRESH DATA"

    except Exception as exc:

        print(
            "DATA ERROR:",
            stock,
            str(exc)
        )

        return None, "DATA ERROR"


# =========================================================
# MOMENTUM EXHAUSTION DETECTOR
# =========================================================

def detect_momentum_exhaustion(df, pos):

    try:

        if len(df) < 25:

            return False, "INSUFFICIENT DATA"

        close = df["Close"]

        volume = df["Volume"]

        ema9 = df["EMA9"]

        rsi = df["RSI"]

        # CURRENT CANDLE

        current_close = float(close.iloc[-1])

        current_volume = float(volume.iloc[-1])

        current_ema9 = float(ema9.iloc[-1])

        current_rsi = float(rsi.iloc[-1])

        previous_close = float(close.iloc[-2])

        previous_rsi = float(rsi.iloc[-2])

        # UPDATE PEAK

        peak_price = float(
            pos.get("peak_price", current_close)
        )

        current_high = float(
            df["High"].iloc[-1]
        )

        if current_high > peak_price:

            peak_price = current_high

            pos["peak_price"] = money(peak_price)

            pos["peak_time"] = now_ist().isoformat()

        # CHECK WHETHER A STRONG SPIKE OCCURRED

        if not pos.get("momentum_watch", False):

            spike_found = False

            start = max(
                SPIKE_LOOKBACK,
                len(df) - SPIKE_LOOKBACK
            )

            for i in range(start, len(df)):

                old_close = float(
                    close.iloc[i - SPIKE_LOOKBACK]
                )

                new_close = float(
                    close.iloc[i]
                )

                spike_volume = float(
                    volume.iloc[i]
                )

                previous_average = float(

                    volume.iloc[
                        max(0, i - 20):i
                    ].mean()

                )

                if old_close <= 0 or previous_average <= 0:

                    continue

                price_move = (

                    (new_close - old_close)
                    / old_close

                ) * 100

                volume_ratio = (
                    spike_volume / previous_average
                )

                if (

                    price_move >= SPIKE_MIN_MOVE

                    and volume_ratio >= SPIKE_VOLUME_MULTIPLIER

                ):

                    spike_found = True

                    break

            if spike_found:

                pos["momentum_watch"] = True

                pos["momentum_watch_time"] = (
                    now_ist().isoformat()
                )

                print(
                    "MOMENTUM WATCH ACTIVATED:",
                    pos.get("stock", "POSITION")
                )

        if not pos.get("momentum_watch", False):

            return False, "NO STRONG SPIKE DETECTED"

        # PEAK PULLBACK

        peak_price = float(
            pos.get("peak_price", current_close)
        )

        if peak_price <= 0:
            return False, "INVALID PEAK"

        pullback = (

            (peak_price - current_close)
            / peak_price

        ) * 100

        # DECLINING VOLUME

        declining_volume = True

        for n in range(
            1,
            DECLINING_VOLUME_CANDLES + 1
        ):

            if (
                float(volume.iloc[-n])
                >= float(volume.iloc[-n - 1])
            ):

                declining_volume = False

                break

        # PRICE WEAKNESS

        price_weak = (

            current_close < current_ema9

            and current_close < previous_close

        )

        # RSI WEAKNESS

        rsi_weak = (
            current_rsi < previous_rsi
        )

        # FINAL EXIT CONFIRMATION

        if (

            pullback >= PEAK_PULLBACK_PERCENT

            and declining_volume

            and price_weak

            and rsi_weak

        ):

            return True, (

                "SPIKE + PEAK PULLBACK + "
                "DECLINING VOLUME + WEAK RSI"

            )

        return False, (

            f"WATCHING | "
            f"PULLBACK={pullback:.2f}% | "
            f"VOLUME_WEAK={declining_volume} | "
            f"PRICE_WEAK={price_weak} | "
            f"RSI_WEAK={rsi_weak}"

        )

    except Exception as exc:

        print(
            "MOMENTUM DETECTOR ERROR:",
            str(exc)
        )

        return False, "DETECTOR ERROR"


# =========================================================
# PORTFOLIO METRICS
# =========================================================

def portfolio_metrics(state):

    realized = sum(

        float(t.get("P&L", 0))

        for t in state["history"]

    )

    closed_brokerage = sum(

        float(t.get("Total Brokerage", 0))

        for t in state["history"]

    )

    market_value = 0

    open_cost = 0

    open_charges = 0

    unrealized = 0

    for stock, pos in state["positions"].items():

        qty = int(pos["qty"])

        entry = float(pos["entry"])

        signal = state["signals"].get(
            stock,
            {}
        )

        health = state["data_health"].get(
            stock,
            {}
        )

        if health.get("status") == "FRESH DATA":

            ltp = float(
                signal.get("price", entry)
            )

        else:

            ltp = entry

        market_value += ltp * qty

        open_cost += entry * qty

        open_charges += (

            float(pos.get("buy_brokerage", 0))

            + calculate_brokerage(ltp * qty)

        )

        unrealized += (ltp - entry) * qty

    unrealized_net = (
        unrealized - open_charges
    )

    equity = (
        float(state["cash"]) + market_value
    )

    return {

        "realized_pnl": money(realized),

        "unrealized_gross": money(unrealized),

        "unrealized_net": money(unrealized_net),

        "closed_brokerage": money(closed_brokerage),

        "open_estimated_charges": money(open_charges),

        "market_value": money(market_value),

        "invested_amount": money(open_cost),

        "equity": money(equity),

        "total_net_pnl": money(
            realized + unrealized_net
        )

    }


# =========================================================
# CLOSE POSITION
# =========================================================

def close_position(
    state,
    stock,
    exit_price,
    reason
):

    if stock not in state["positions"]:
        return False

    pos = state["positions"][stock]

    qty = int(pos["qty"])

    entry = float(pos["entry"])

    if qty <= 0 or exit_price <= 0:
        return False

    buy_value = entry * qty

    sell_value = exit_price * qty

    buy_brokerage = float(
        pos.get("buy_brokerage", 0)
    )

    sell_brokerage = calculate_brokerage(
        sell_value
    )

    gross_pnl = (
        (exit_price - entry) * qty
    )

    total_brokerage = (
        buy_brokerage + sell_brokerage
    )

    net_pnl = (
        gross_pnl - total_brokerage
    )

    state["cash"] += (
        sell_value - sell_brokerage
    )

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

        f"CLOSED {stock} | "

        f"NET P&L={net_pnl:.2f} | "

        f"REASON={reason}"

    )

    del state["positions"][stock]

    save_state(state)

    return True


# =========================================================
# SAFE SQUARE OFF
# =========================================================

def square_off_all(state):

    print("\n===== SAFE SQUARE OFF =====")

    state["square_off_status"] = "IN PROGRESS"

    save_state(state)

    for stock in list(state["positions"].keys()):

        df, status = get_data(stock)

        if df is None or status != "FRESH DATA":

            update_data_health(

                state,

                stock,

                status,

                "SQUARE OFF PRICE NOT AVAILABLE"

            )

            print(

                "POSITION KEPT OPEN:",

                stock,

                status

            )

            continue

        exit_price = float(
            df["Close"].iloc[-1]
        )

        if exit_price <= 0:
            continue

        close_position(

            state,

            stock,

            exit_price,

            "FORCE SQUARE OFF"

        )

    if state["positions"]:

        state["square_off_status"] = (
            "PENDING - FRESH PRICE REQUIRED"
        )

        state["bot_status"] = (
            "SQUARE OFF NEEDS RETRY"
        )

    else:

        state["square_off_status"] = "COMPLETED"

        state["bot_status"] = "SESSION COMPLETED"

    save_state(state)

    print(

        "REMAINING POSITIONS:",

        len(state["positions"])

    )


# =========================================================
# ENTRY AND EXIT SIGNAL
# =========================================================

def scan_stock(stock, state):

    try:

        df, status = get_data(stock)

        if df is None:

            update_data_health(

                state,

                stock,

                status,

                "TRADING BLOCKED"

            )

            print(

                "NO TRADE:",

                stock,

                status

            )

            return

        update_data_health(

            state,

            stock,

            "FRESH DATA",

            "DATA VERIFIED",

            df.index[-1],

            (

                now_ist()
                - df.index[-1].to_pydatetime()

            ).total_seconds()

        )

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

        if price <= 0 or volume_avg <= 0:
            return

        # TREND

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

        # PULLBACK

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

        # VOLUME

        volume_ok = (

            volume > volume_avg * VOLUME_MULTIPLIER

        )

        signal = "WAIT"

        if (

            bullish_trend

            and bullish_pullback

            and volume_ok

            and 50 <= rsi <= 75

        ):

            signal = "BUY"

        elif (

            bearish_trend

            and bearish_pullback

            and volume_ok

            and 25 <= rsi <= 50

        ):

            signal = "SELL"

        # SAVE SIGNAL

        state["signals"][stock] = {

            "price": money(price),

            "trend": bool(ema9 > ema21),

            "rsi": money(rsi),

            "ema9": money(ema9),

            "ema21": money(ema21),

            "vwap": money(vwap),

            "volume": int(volume),

            "volume_avg20": money(volume_avg),

            "volume_ratio": round(
                volume / volume_avg,
                2
            ),

            "signal": signal,

            "candle_time": candle_time,

            "updated": now_ist().isoformat(),

            "data_status": "FRESH DATA"

        }

        print(

            f"{stock} | "

            f"PRICE={price:.2f} | "

            f"RSI={rsi:.2f} | "

            f"SIGNAL={signal}"

        )

        # =================================================
        # EXISTING POSITION MANAGEMENT
        # =================================================

        if stock in state["positions"]:

            pos = state["positions"][stock]

            # UPDATE PEAK

            if high > float(
                pos.get("peak_price", price)
            ):

                pos["peak_price"] = money(high)

                pos["peak_time"] = (
                    now_ist().isoformat()
                )

            # STOP LOSS FIRST

            if low <= float(pos["stop"]):

                close_position(

                    state,

                    stock,

                    float(pos["stop"]),

                    "STOP LOSS"

                )

                return

            # TARGET SECOND

            elif high >= float(pos["target"]):

                close_position(

                    state,

                    stock,

                    float(pos["target"]),

                    "TARGET"

                )

                return

            # MOMENTUM EXHAUSTION

            exhausted, reason = (
                detect_momentum_exhaustion(
                    df,
                    pos
                )
            )

            print(

                "MOMENTUM STATUS:",

                stock,

                reason

            )

            if exhausted:

                close_position(

                    state,

                    stock,

                    price,

                    "MOMENTUM EXHAUSTION"

                )

                return

            # NORMAL BEARISH EXIT

            if signal == "SELL":

                close_position(

                    state,

                    stock,

                    price,

                    "BEARISH SIGNAL"

                )

                return

            return

        # =================================================
        # ONE ENTRY PER COMPLETED CANDLE
        # =================================================

        if (

            state["last_processed"].get(stock)

            == candle_time

        ):

            return

        state["last_processed"][stock] = candle_time

        if signal != "BUY":
            return

        # MAX TRADES

        if len(state["positions"]) >= MAX_TRADES:

            print("MAX OPEN TRADES REACHED")

            return

        # STOP LOSS

        stop = float(
            df["Low"].iloc[-6:-1].min()
        )

        if stop <= 0 or stop >= price:
            return

        risk_per_share = (
            price - stop
        )

        qty_by_risk = int(

            RISK_PER_TRADE
            / risk_per_share

        )

        qty_by_amount = int(

            TRADE_AMOUNT
            / price

        )

        qty_by_cash = int(

            state["cash"]
            / (price * (1 + BROKERAGE_RATE))

        )

        qty = min(

            qty_by_risk,

            qty_by_amount,

            qty_by_cash

        )

        if qty <= 0:
            return

        buy_value = qty * price

        buy_brokerage = calculate_brokerage(
            buy_value
        )

        total_cost = (
            buy_value + buy_brokerage
        )

        if total_cost > float(state["cash"]):
            return

        target = (

            price
            + REWARD_RISK * risk_per_share

        )

        state["cash"] -= total_cost

        # SAVE POSITION

        state["positions"][stock] = {

            "entry": money(price),

            "stop": money(stop),

            "target": money(target),

            "qty": int(qty),

            "buy_value": money(buy_value),

            "buy_brokerage": money(buy_brokerage),

            "entry_time": now_ist().isoformat(),

            # MOMENTUM TRACKING

            "peak_price": money(price),

            "peak_time": now_ist().isoformat(),

            "momentum_watch": False,

            "momentum_watch_time": None,

            "stock": stock

        }

        print(

            f"BUY PAPER TRADE: {stock} | "

            f"PRICE={price:.2f} | "

            f"QTY={qty} | "

            f"SL={stop:.2f} | "

            f"TARGET={target:.2f}"

        )

        save_state(state)

    except Exception as exc:

        print(

            "SCAN ERROR:",

            stock,

            str(exc)

        )


# =========================================================
# PORTFOLIO REPORT
# =========================================================

def show_portfolio(state):

    metrics = portfolio_metrics(state)

    print("\n========== V7 PORTFOLIO ==========")

    print("CAPITAL:", state["capital"])

    print("AVAILABLE CASH:", money(state["cash"]))

    print("INVESTED:", metrics["invested_amount"])

    print("MARKET VALUE:", metrics["market_value"])

    print("OPEN TRADES:", len(state["positions"]))

    print("CLOSED TRADES:", len(state["history"]))

    print("REALIZED P&L:", metrics["realized_pnl"])

    print("UNREALIZED NET:", metrics["unrealized_net"])

    print("TOTAL NET P&L:", metrics["total_net_pnl"])

    print("ESTIMATED EQUITY:", metrics["equity"])

    print("==================================\n")


# =========================================================
# DATA HEALTH REPORT
# =========================================================

def show_data_health(state):

    print("\n========== DATA HEALTH ==========")

    for stock in STOCKS:

        health = state["data_health"].get(
            stock,
            {}
        )

        status = health.get(
            "status",
            "NOT CHECKED"
        )

        age = health.get(
            "age_seconds"
        )

        print(

            stock,

            "|",

            status,

            "| AGE:",

            age

        )

    print("=================================\n")


# =========================================================
# MAIN ENGINE
# =========================================================

def main():

    now = now_ist()

    state = load_state()

    print("CURRENT IST:", now.isoformat())

    # WEEKEND

    if now.weekday() >= 5:

        state["bot_status"] = "MARKET CLOSED"

        save_state(state)

        print("WEEKEND - NO TRADING")

        return

    # BEFORE MARKET

    if now.time() < MARKET_START:

        state["bot_status"] = "WAITING FOR MARKET"

        save_state(state)

        print("WAITING FOR MARKET")

        return

    # AFTER SQUARE OFF TIME

    if now.time() >= SQUARE_OFF_TIME:

        square_off_all(state)

        show_portfolio(state)

        show_data_health(state)

        return

    # MAIN LOOP

    end_time = datetime.combine(

        now.date(),

        SQUARE_OFF_TIME,

        tzinfo=IST

    )

    state["bot_status"] = "V7 TRADING"

    save_state(state)

    print("\nSMART DATA PROTECTION ACTIVE")

    print("MAX DATA AGE:", MAX_DATA_AGE, "SECONDS")

    print("SCAN INTERVAL:", SCAN_INTERVAL)

    print("MOMENTUM EXHAUSTION SELL ACTIVE")

    while now_ist() < end_time:

        for stock in STOCKS:

            if now_ist() >= end_time:
                break

            scan_stock(stock, state)

            save_state(state)

        show_portfolio(state)

        show_data_health(state)

        remaining = (

            end_time - now_ist()

        ).total_seconds()

        if remaining > 0:

            time.sleep(

                min(
                    SCAN_INTERVAL,
                    remaining
                )

            )

    # FINAL SQUARE OFF

    square_off_all(state)

    show_portfolio(state)

    show_data_health(state)

    print("\nV7 TRADING SESSION FINISHED")


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()