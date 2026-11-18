
import yfinance as yf
import pandas as pd
import os
from datetime import datetime

CAPITAL = 10000
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


def scan_market():
    signals = []

    for stock in STOCKS:
        try:
            df = get_data(stock)

            if df is None or len(df) < 50:
                continue

            last = df.iloc[-1]

            if (
                last["EMA20"] > last["EMA50"]
                and last["RSI"] > 50
                and last["RSI"] < 70
                and last["Volume"] > last["AVG_VOLUME"] * 1.5
            ):
                signals.append({
                    "Stock": stock,
                    "Price": float(last["Close"]),
                    "Signal": "BUY"
                })

        except Exception as e:
            print(stock, e)

    return signals


def main():
    print("ALGO PAPER TRADING")
    print(datetime.now())

    signals = scan_market()

    for signal in signals:
        print(signal)

    print("Total BUY Signals:", len(signals))


if __name__ == "__main__":
    main()
