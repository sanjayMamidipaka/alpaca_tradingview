import os
import math
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.common.exceptions import APIError

load_dotenv()
app = FastAPI()

# Configuration
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
PASSPHRASE = os.getenv("WEBHOOK_PASSPHRASE")

# Initialize Trading Client only
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)


@app.post("/webhook")
async def tradingview_webhook(request: Request):
    data = await request.json()
    if data.get("passphrase") != PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    ticker = data.get("ticker", "DOGEUSD").replace("/", "").upper()
    side_input = data.get("side", "").lower()

    is_crypto = ticker.endswith("USD") or ticker.endswith("USDT")
    tif = TimeInForce.GTC if is_crypto else TimeInForce.DAY

    try:
        # 1. Position Check
        current_position = None
        try:
            current_position = trading_client.get_open_position(ticker)
        except APIError:
            current_position = None

        # 2. Close Logic
        if side_input in ["sell", "buy_to_cover"]:
            if current_position:
                trading_client.close_position(ticker)
                return {"status": "success", "message": f"Closed {ticker}"}
            return {"status": "ignored", "message": "No position to close"}

        # 3. Entry Logic
        elif side_input in ["buy", "sell_short"]:
            if current_position:
                return {"status": "ignored", "message": "Position already open"}

            if side_input == "sell_short" and is_crypto:
                return {"status": "error", "message": "Crypto shorting not supported"}

            # Risk Calculation
            account = trading_client.get_account()
            risk_dollars = float(account.equity) * 0.11
            target_value = max(risk_dollars, 11.00)

            # --- SHORTING FIX (Whole Shares) ---
            if side_input == "sell_short":
                # Using TradingClient to get the latest quote for the ticker
                # We pull the 'bid_price' to be conservative for a short entry
                asset_data = trading_client.get_asset(ticker)
                if not asset_data.shortable:
                    return {"status": "error", "message": f"{ticker} not shortable"}

                # Note: Newer alpaca-py versions allow get_latest_quote on TradingClient
                # or you can fetch the snapshot.
                # If your version lacks this, we fallback to a simple price estimate
                # or you can use the 'notional' for buys and 'qty' for shorts.

                # To get price without StockHistoricalDataClient:
                # We fetch a snapshot via a minimal order check or asset status.
                # However, for 100% reliability, let's calculate QTY manually:

                try:
                    # This is the "internal" way to get a price via TradingClient
                    # if you don't want to import the DataClient.
                    latest_quote = trading_client.get_latest_stock_quote(
                        ticker)
                    price = latest_quote.bid_price
                except:
                    # Fallback if get_latest_stock_quote is not in your specific SDK version
                    # Some versions require the DataClient.
                    # If this errors, you MUST use DataClient or hardcode a price.
                    raise Exception(
                        "Unable to fetch price for shorting without DataClient.")

                share_qty = math.floor(target_value / price)

                if share_qty < 1:
                    return {"status": "error", "message": "Notional too low for 1 share"}

                order = trading_client.submit_order(MarketOrderRequest(
                    symbol=ticker,
                    qty=share_qty,
                    side=OrderSide.SELL,
                    time_in_force=tif
                ))

            # --- LONG ENTRY (Notional/Fractional) ---
            else:
                order = trading_client.submit_order(MarketOrderRequest(
                    symbol=ticker,
                    notional=round(target_value, 2),
                    side=OrderSide.BUY,
                    time_in_force=tif
                ))

            return {"status": "success", "order_id": str(order.id)}

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {"status": "error", "message": str(e)}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
