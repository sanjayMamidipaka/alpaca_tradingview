import os
import math
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient  # <-- Added for data
# <-- For quick price checks
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
data_client = StockHistoricalDataClient(
    API_KEY, SECRET_KEY)  # <-- Replaces the "internal" check


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
        # 1. Position Check (Prevents double-dipping as discussed)
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

        # 3. Entry Logic (Volatility Washout Entry)
        elif side_input in ["buy", "sell_short"]:
            if current_position:
                return {"status": "ignored", "message": "Position already open"}

            if side_input == "sell_short" and is_crypto:
                return {"status": "error", "message": "Crypto shorting not supported"}

            # Risk Calculation
            account = trading_client.get_account()
            # 11% risk per trade as per your requirement
            risk_dollars = float(account.equity) * 0.11
            target_value = max(risk_dollars, 11.00)

            # --- DATA FETCHING FIX ---
            # Using Snapshot to get the latest price for shorts or precise sizing
            snapshot = data_client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=[ticker]))
            current_price = snapshot[ticker].latest_trade.price

            if side_input == "sell_short":
                asset_data = trading_client.get_asset(ticker)
                if not asset_data.shortable:
                    return {"status": "error", "message": f"{ticker} not shortable"}

                # Shorting requires whole shares in many Alpaca account types
                share_qty = math.floor(target_value / current_price)
                if share_qty < 1:
                    return {"status": "error", "message": "Notional too low for 1 share"}

                order = trading_client.submit_order(MarketOrderRequest(
                    symbol=ticker,
                    qty=share_qty,
                    side=OrderSide.SELL,
                    time_in_force=tif
                ))
            else:
                # Long entry using fractional notional
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
    # Optimized for Koyeb deployment
    uvicorn.run(app, host="0.0.0.0", port=8000)
