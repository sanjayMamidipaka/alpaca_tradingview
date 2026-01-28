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


@app.post("/webhook")
async def tradingview_webhook(request: Request):
    # 1. Security: Passphrase Check
    data = await request.json()
    if data.get("passphrase") != PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    ticker = data.get("ticker", "DOGEUSD").replace("/", "")
    side = data.get("side", "").lower()

    try:
        if side == "sell":
            # Liquidate the position
            print(f"Closing entire position for {ticker}...")
            try:
                trading_client.close_position(ticker)
                return {"status": "success", "message": f"Closed all {ticker}"}
            except Exception as e:
                return {"status": "ignored", "message": f"No open position for {ticker}"}

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

            # 3. Dynamic Floor and Asset-Based TIF
            # Fractional/Notional floor must be at least $1.00, but $10.00 is safer for Alpaca
            notional_value = round(max(risk_amount, 11.00), 2)

            # ASSET CHECK: Stocks/ETFs (GLD) require DAY. Crypto supports GTC.
            # We check if the symbol is a known crypto pair or use Alpaca's asset check.
            is_crypto = ticker.endswith("USD") or ticker.endswith("USDT")
            tif = TimeInForce.GTC if is_crypto else TimeInForce.DAY

            print(
                f"Equity: {total_equity} | Notional: ${notional_value} | TIF: {tif}")

            # 4. Submit Order
            order = trading_client.submit_order(MarketOrderRequest(
                symbol=ticker,
                notional=notional_value,
                side=OrderSide.BUY,
                time_in_force=tif
            ))

            return {"status": "success", "order_id": str(order.id), "notional": notional_value}

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {"status": "error", "message": str(e)}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check(response):
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
