import os
from datetime import datetime, timezone

import yfinance as yf
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stock-data-mcp")


def _current_price(ticker: yf.Ticker) -> float | None:
    try:
        fi = ticker.fast_info
        price = fi.get("lastPrice") or fi.get("last_price") or fi.get("regularMarketPrice")
        if price:
            return float(price)
    except Exception:
        pass
    try:
        hist = ticker.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


@mcp.tool()
def get_stock_quote(symbol: str) -> dict:
    """Get the current price for a stock ticker (Yahoo Finance, may be delayed a few minutes)."""
    symbol = symbol.upper().strip()
    t = yf.Ticker(symbol)
    price = _current_price(t)
    if price is None:
        return {"s": "no_data", "symbol": symbol}
    return {
        "s": "ok",
        "symbol": [symbol],
        "mid": [price],
        "last": [price],
        "updated": [int(datetime.now(timezone.utc).timestamp())],
    }


@mcp.tool()
def get_option_expirations(symbol: str) -> dict:
    """List available option expiration dates (YYYY-MM-DD) for a ticker."""
    symbol = symbol.upper().strip()
    t = yf.Ticker(symbol)
    expirations = list(t.options)
    if not expirations:
        return {"s": "no_data", "symbol": symbol}
    return {"s": "ok", "expirations": expirations}


@mcp.tool()
def get_option_chain(symbol: str, expiration: str, strike_limit: int = 40) -> dict:
    """
    Get call/put strikes and mid prices nearest the money for a ticker and expiration.
    strike_limit caps how many strikes per side come back, closest to the current price.
    """
    symbol = symbol.upper().strip()
    t = yf.Ticker(symbol)
    price = _current_price(t)

    try:
        chain = t.option_chain(expiration)
    except Exception as e:
        return {"s": "error", "errmsg": str(e)}

    calls, puts = chain.calls.copy(), chain.puts.copy()
    for df in (calls, puts):
        df["mid"] = (df["bid"].fillna(0) + df["ask"].fillna(0)) / 2
        # fall back to lastPrice if bid/ask are both missing (illiquid strikes)
        df.loc[df["mid"] == 0, "mid"] = df.loc[df["mid"] == 0, "lastPrice"]

    if price is not None:
        calls = calls.iloc[(calls["strike"] - price).abs().argsort()[:strike_limit]]
        puts = puts.iloc[(puts["strike"] - price).abs().argsort()[:strike_limit]]

    strikes, sides, mids, bids, asks = [], [], [], [], []
    for df, side in ((calls, "call"), (puts, "put")):
        for _, row in df.iterrows():
            strikes.append(float(row["strike"]))
            sides.append(side)
            mids.append(float(row["mid"]) if row["mid"] == row["mid"] else None)  # NaN check
            bids.append(float(row["bid"]) if row["bid"] == row["bid"] else None)
            asks.append(float(row["ask"]) if row["ask"] == row["ask"] else None)

    return {"s": "ok", "strike": strikes, "side": sides, "mid": mids, "bid": bids, "ask": asks}


# ASGI app for uvicorn (Render/Railway/Fly all expect to run an ASGI app on $PORT)
app = mcp.streamable_http_app()


# Simple health-check page at the root address, so you can confirm the server
# itself is alive just by visiting it in a browser. The actual tool endpoint
# that Claude connects to is at /mcp, not /.
async def _health(scope, receive, send):
    body = b"Stock data MCP server is running. The tool endpoint is at /mcp."
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


_original_app = app


async def app(scope, receive, send):  # noqa: F811 (intentional wrap)
    if scope["type"] == "http" and scope["path"] == "/":
        await _health(scope, receive, send)
    else:
        await _original_app(scope, receive, send)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
