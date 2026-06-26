import os
from datetime import datetime, timezone

import yfinance as yf
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route


def _current_price(ticker: yf.Ticker):
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


async def health(request):
    return PlainTextResponse(
        "Stock data server is running. Try /api/quote?symbol=MU"
    )


async def quote(request):
    symbol = (request.query_params.get("symbol") or "").upper().strip()
    if not symbol:
        return JSONResponse({"s": "error", "errmsg": "missing symbol"}, status_code=400)
    t = yf.Ticker(symbol)
    price = _current_price(t)
    if price is None:
        return JSONResponse({"s": "no_data", "symbol": symbol})
    return JSONResponse({
        "s": "ok",
        "symbol": symbol,
        "price": price,
        "updated": int(datetime.now(timezone.utc).timestamp()),
    })


async def expirations(request):
    symbol = (request.query_params.get("symbol") or "").upper().strip()
    if not symbol:
        return JSONResponse({"s": "error", "errmsg": "missing symbol"}, status_code=400)
    t = yf.Ticker(symbol)
    exps = list(t.options)
    if not exps:
        return JSONResponse({"s": "no_data", "symbol": symbol})
    return JSONResponse({"s": "ok", "expirations": exps})


async def chain(request):
    symbol = (request.query_params.get("symbol") or "").upper().strip()
    expiration = request.query_params.get("expiration")
    strike_limit = int(request.query_params.get("strikeLimit", 40))
    if not symbol or not expiration:
        return JSONResponse({"s": "error", "errmsg": "missing symbol or expiration"}, status_code=400)

    t = yf.Ticker(symbol)
    price = _current_price(t)

    try:
        ch = t.option_chain(expiration)
    except Exception as e:
        return JSONResponse({"s": "error", "errmsg": str(e)}, status_code=400)

    calls, puts = ch.calls.copy(), ch.puts.copy()
    for df in (calls, puts):
        df["mid"] = (df["bid"].fillna(0) + df["ask"].fillna(0)) / 2
        df.loc[df["mid"] == 0, "mid"] = df.loc[df["mid"] == 0, "lastPrice"]

    if price is not None:
        calls = calls.iloc[(calls["strike"] - price).abs().argsort()[:strike_limit]]
        puts = puts.iloc[(puts["strike"] - price).abs().argsort()[:strike_limit]]

    strikes, sides, mids = [], [], []
    for df, side in ((calls, "call"), (puts, "put")):
        for _, row in df.iterrows():
            strikes.append(float(row["strike"]))
            sides.append(side)
            mids.append(float(row["mid"]) if row["mid"] == row["mid"] else None)  # NaN check

    return JSONResponse({"s": "ok", "strike": strikes, "side": sides, "mid": mids, "price": price})


app = Starlette(
    routes=[
        Route("/", health),
        Route("/api/quote", quote),
        Route("/api/expirations", expirations),
        Route("/api/chain", chain),
    ],
    middleware=[Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
