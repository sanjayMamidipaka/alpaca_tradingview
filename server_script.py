import os
import math
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.common.exceptions import APIError

load_dotenv()
app = FastAPI()

# Configuration
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
PASSPHRASE = os.getenv("WEBHOOK_PASSPHRASE")

# Initialize Clients
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)


@app.post("/webhook")
async def tradingview_webhook(request: Request):
    data = await request.json()
    if data.get("passphrase") != PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    ticker = data.get("ticker", "DOGEUSD").replace("/", "").upper()
    side_input = data.get("side", "").lower()

    # --- WEBHOOK PRICE INTEGRATION ---
    # Get price from webhook; fallback to snapshot only if missing
    webhook_price = data.get("price")

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
                print(f"Closing position: {ticker}")
                return {"status": "success", "message": f"Closed {ticker}"}
            return {"status": "ignored", "message": "No position to close"}

        # 3. Entry Logic
        elif side_input in ["buy", "sell_short"]:
            if current_position:
                return {"status": "ignored", "message": "Position already open"}

            if side_input == "sell_short" and is_crypto:
                return {"status": "error", "message": "Crypto shorting not supported"}

            # Get Account Equity for Risk Management
            account = trading_client.get_account()
            risk_dollars = float(account.equity) * 0.11
            target_value = max(risk_dollars, 11.00)

            # Determine Execution Price
            if webhook_price:
                current_price = float(webhook_price)
                print(f"Using Webhook Price: {current_price}")
            else:
                # Fallback to snapshot if the webhook price wasn't sent
                snapshot = data_client.get_stock_snapshot(
                    StockSnapshotRequest(symbol_or_symbols=[ticker]))
                current_price = snapshot[ticker].latest_trade.price
                print(
                    f"Webhook price missing. Using Snapshot Price: {current_price}")

            # Calculate Quantity
            share_qty = math.floor(target_value / current_price)
            market_value = share_qty * current_price
            direction = "LONG" if side_input == "buy" else "SHORT"

            if side_input == "sell_short":
                asset_data = trading_client.get_asset(ticker)
                if not asset_data.shortable:
                    return {"status": "error", "message": f"{ticker} not shortable"}

                if share_qty < 1:
                    return {"status": "error", "message": "Notional too low for 1 share short"}

                order = trading_client.submit_order(MarketOrderRequest(
                    symbol=ticker,
                    qty=share_qty,
                    side=OrderSide.SELL,
                    time_in_force=tif
                ))
            else:
                # Long entry using whole shares based on your risk calculation
                # (You can swap back to 'notional=round(target_value, 2)' if you prefer Alpaca's internal fractional handling)
                order = trading_client.submit_order(MarketOrderRequest(
                    symbol=ticker,
                    qty=share_qty,
                    side=OrderSide.BUY,
                    time_in_force=tif
                ))

            print(
                f"Order submitted: ticker={ticker}, direction={direction}, qty={share_qty}, "
                f"market_value={market_value:.2f}, approx_price={current_price:.4f}, order_id={order.id}"
            )
            return {"status": "success", "order_id": str(order.id), "qty": share_qty}

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {"status": "error", "message": str(e)}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
