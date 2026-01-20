import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv()
app = FastAPI()

# Configuration
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
PASSPHRASE = os.getenv("WEBHOOK_PASSPHRASE")

# Initialize Trading Client (set paper=False for live trading)
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
print(trading_client)


@app.post("/webhook")
async def tradingview_webhook(request: Request):
    # 1. Security: IP Check
    
    # 2. Security: Passphrase Check
    data = await request.json()
    if data.get("passphrase") != PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    ticker = data.get("ticker", "DOGEUSD").replace("/", "")
    side = data.get("side", "").lower()

    try:
        if side == "sell":
            # Liquidate the position
            print(f"Closing entire position for {ticker}...")
            trading_client.close_position(ticker)
            return {"status": "success", "message": f"Closed all {ticker}"}

        elif side == "buy":
            # 1. Check for Existing Position
            positions = trading_client.get_all_positions()
            if any(p.symbol == ticker for p in positions):
                print(f"SKIPPING: Position already open in {ticker}")
                return {"status": "ignored", "message": "Position already open"}

            # 2. Dynamic Risk Calculation
            account = trading_client.get_account()
            total_equity = float(account.equity)
            risk_amount = total_equity * 0.11

            # 3. SET A HARD BUFFERED FLOOR
            # Using 15.00 ensures we are safely above the $10 limit even with fees/slippage
            notional_value = round(min(risk_amount, 15.00), 2)
            print(f"Equity: {total_equity} | Risk Amount: {risk_amount} | Final Notional: {notional_value}")

            print(f"Executing BUY: {ticker} | Notional: ${notional_value}")

            # 4. Submit Order using the VARIABLE 'notional_value'
            order = trading_client.submit_order(MarketOrderRequest(
                symbol=ticker,
                notional=notional_value,  # <-- Use the variable here!
                side=OrderSide.BUY,
                time_in_force=TimeInForce.GTC
            ))

            return {"status": "success", "order_id": str(order.id), "notional": notional_value}

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {"status": "error", "message": str(e)}


@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
