import os
import math
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
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
# Paper=True for testing; set to False only when ready for Live
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
stock_data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)


@app.post("/webhook")
async def tradingview_webhook(request: Request):
    # 1. Security Check
    data = await request.json()
    if data.get("passphrase") != PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    ticker = data.get("ticker", "DOGEUSD").replace("/", "").upper()
    side_input = data.get("side", "").lower()

    # Asset Type Detection
    is_crypto = ticker.endswith("USD") or ticker.endswith("USDT")
    tif = TimeInForce.GTC if is_crypto else TimeInForce.DAY

    try:
        # 2. Position Check
        current_position = None
        try:
            current_position = trading_client.get_open_position(ticker)
        except APIError:
            current_position = None

        # 3. Exit Logic: Close or Cover
        if side_input in ["sell", "buy_to_cover"]:
            if current_position is None:
                print(f"IGNORING: No position found for {ticker}.")
                return {"status": "ignored", "message": "No open position"}

            trading_client.close_position(ticker)
            return {"status": "success", "message": f"Closed {ticker}"}

        # 4. Entry Logic: Buy or Short
        elif side_input in ["buy", "sell_short"]:
            if current_position is not None:
                return {"status": "ignored", "message": "Position already open"}

            if side_input == "sell_short" and is_crypto:
                return {"status": "error", "message": "Shorting crypto is not supported on Alpaca"}

            # Account & Risk Math
            account = trading_client.get_account()
            total_equity = float(account.equity)
            risk_dollars = total_equity * 0.11
            notional_target = max(risk_dollars, 11.00)

            # --- SHORTING FIX: Calculate whole shares ---
            if side_input == "sell_short":
                # Fetch latest price to avoid fractional order error
                price_request = StockLatestTradeRequest(
                    symbol_or_symbols=ticker)
                latest_trade = stock_data_client.get_stock_latest_trade(
                    price_request)
                current_price = latest_trade[ticker].price

                # Calculate integer quantity (Whole shares only)
                share_qty = math.floor(notional_target / current_price)

                if share_qty < 1:
                    return {"status": "error", "message": "Equity too low to short at least 1 whole share"}

                order_request = MarketOrderRequest(
                    symbol=ticker,
                    qty=share_qty,
                    side=OrderSide.SELL,
                    time_in_force=tif
                )
                print(
                    f"SHORTING {ticker}: {share_qty} shares at approx ${current_price}")

            # --- LONG ENTRY: Uses Notional (Supports fractions) ---
            else:
                order_request = MarketOrderRequest(
                    symbol=ticker,
                    notional=round(notional_target, 2),
                    side=OrderSide.BUY,
                    time_in_force=tif
                )
                print(f"BUYING {ticker}: Notional ${notional_target}")

            # Submit Order
            order = trading_client.submit_order(order_request)
            return {"status": "success", "order_id": str(order.id)}

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        return {"status": "error", "message": str(e)}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
