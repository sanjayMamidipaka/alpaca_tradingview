import os
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

# Initialize Trading Client (set paper=False for live trading)
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)


@app.post("/webhook")
async def tradingview_webhook(request: Request):
    # 1. Security: Passphrase Check
    data = await request.json()
    if data.get("passphrase") != PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    ticker = data.get("ticker", "DOGEUSD").replace("/", "")
    side_input = data.get("side", "").lower()

    # Asset Check: Crypto vs Equities
    is_crypto = ticker.endswith("USD") or ticker.endswith("USDT")
    tif = TimeInForce.GTC if is_crypto else TimeInForce.DAY

    try:
        # 2. POSITION CHECK: Get current position status
        current_position = None
        try:
            current_position = trading_client.get_open_position(ticker)
        except APIError:
            current_position = None

        # 3. SAFETY LOGIC: Close/Cover orders
        if side_input in ["sell", "buy_to_cover"]:
            if current_position is None:
                print(
                    f"IGNORING: No position found for {ticker} to close/cover.")
                return {"status": "ignored", "message": "No open position to close"}

            # Liquidate the position
            print(f"Closing position for {ticker}...")
            trading_client.close_position(ticker)
            return {"status": "success", "message": f"Closed {ticker}"}

        # 4. ENTRY LOGIC: Long (buy) or Short (sell_short)
        elif side_input in ["buy", "sell_short"]:
            # PREVENT DOUBLE ENTRIES
            if current_position is not None:
                print(f"SKIPPING: Position already open in {ticker}")
                return {"status": "ignored", "message": "Position already open"}

            # REJECT CRYPTO SHORTS: Alpaca does not support shorting crypto
            if side_input == "sell_short" and is_crypto:
                return {"status": "error", "message": "Alpaca does not support shorting crypto"}

            # Dynamic Risk Calculation
            account = trading_client.get_account()
            total_equity = float(account.equity)
            risk_amount = total_equity * 0.11
            notional_value = round(max(risk_amount, 11.00), 2)

            # Map signal to Alpaca OrderSide
            alpaca_side = OrderSide.BUY if side_input == "buy" else OrderSide.SELL

            print(
                f"Equity: {total_equity} | Notional: ${notional_value} | Side: {alpaca_side}")

            # Submit Order
            order = trading_client.submit_order(MarketOrderRequest(
                symbol=ticker,
                notional=notional_value,
                side=alpaca_side,
                time_in_force=tif
            ))

            return {"status": "success", "order_id": str(order.id), "side": str(alpaca_side)}

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {"status": "error", "message": str(e)}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)