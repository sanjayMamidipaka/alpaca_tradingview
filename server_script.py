import os
import math
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest, CryptoSnapshotRequest
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
crypto_data_client = CryptoHistoricalDataClient(API_KEY, SECRET_KEY)


def _crypto_symbol_for_data(ticker: str) -> str:
    """Convert trading symbol (e.g. SOLUSD) to Alpaca crypto data format (SOL/USD)."""
    ticker = ticker.upper()
    if ticker.endswith("USDT"):
        return f"{ticker[:-4]}/USDT"
    if ticker.endswith("USD"):
        return f"{ticker[:-3]}/USD"
    return ticker


@app.post("/webhook")
async def tradingview_webhook(request: Request):
    data = await request.json()
    if data.get("passphrase") != PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    ticker = data.get("ticker", "DOGEUSD").replace("/", "").upper()
    side_input = data.get("side", "").lower()

    # --- WEBHOOK PRICE INTEGRATION ---
    # Get price from webhook; fallback to snapshot only for stocks
    webhook_price = data.get("price")

    is_crypto = ticker.endswith("USD") or ticker.endswith("USDT")

    print(f"Incoming webhook data: {data}")
    print(
        f"Resolved ticker={ticker}, side={side_input}, is_crypto={is_crypto}, webhook_price={webhook_price}"
    )
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
                print("Position already open, ignoring new entry signal")
                return {"status": "ignored", "message": "Position already open"}

            if side_input == "sell_short" and is_crypto:
                print("Crypto shorting not supported")
                return {"status": "error", "message": "Crypto shorting not supported"}

            # Get Account Equity for Risk Management
            account = trading_client.get_account()
            risk_dollars = float(account.equity) * 0.11
            target_value = max(risk_dollars, 11.00)

            # Determine Execution Price — guarantee we always get a price (webhook or Alpaca)
            if webhook_price is not None:
                try:
                    current_price = float(webhook_price)
                    print(f"Using Webhook Price: {current_price}")
                except (TypeError, ValueError):
                    webhook_price = None
            if webhook_price is None:
                if is_crypto:
                    # Guarantee crypto price via Alpaca crypto snapshot (e.g. SOLUSD -> SOL/USD)
                    crypto_symbol = _crypto_symbol_for_data(ticker)
                    try:
                        snap = crypto_data_client.get_crypto_snapshot(
                            CryptoSnapshotRequest(
                                symbol_or_symbols=[crypto_symbol])
                        )
                        # SnapshotSet is dict-like: symbol -> Snapshot
                        snapshot_data = None
                        if hasattr(snap, "get"):
                            snapshot_data = snap.get(
                                crypto_symbol) or snap.get(ticker)
                        if snapshot_data is None and hasattr(snap, "values"):
                            for v in snap.values():
                                snapshot_data = v
                                break
                        if snapshot_data is None:
                            snapshot_data = snap
                        if hasattr(snapshot_data, "latest_trade") and snapshot_data.latest_trade:
                            current_price = float(
                                snapshot_data.latest_trade.price)
                        elif hasattr(snapshot_data, "latest_quote") and snapshot_data.latest_quote:
                            q = snapshot_data.latest_quote
                            ap, bp = float(q.ask_price), float(q.bid_price)
                            current_price = (ap + bp) / \
                                2.0 if (ap and bp) else ap or bp
                        else:
                            raise ValueError(
                                f"No price in crypto snapshot for {crypto_symbol}")
                        print(
                            f"Crypto price from Alpaca snapshot ({crypto_symbol}): {current_price}")
                    except Exception as e:
                        print(
                            f"Crypto snapshot failed for {ticker} ({crypto_symbol}): {e}")
                        return {"status": "error", "message": f"Could not get crypto price for {ticker}: {e}"}
                else:
                    # Stock fallback
                    snapshot = data_client.get_stock_snapshot(
                        StockSnapshotRequest(symbol_or_symbols=[ticker])
                    )
                    current_price = snapshot[ticker].latest_trade.price
                    print(
                        f"Webhook price missing. Using Stock Snapshot Price: {current_price}")

            # Calculate Quantity
            share_qty = math.floor(target_value / current_price)
            market_value = share_qty * current_price
            direction = "LONG" if side_input == "buy" else "SHORT"

            if side_input == "sell_short":
                asset_data = trading_client.get_asset(ticker)
                if not asset_data.shortable:
                    print(f"{ticker} not shortable")
                    return {"status": "error", "message": f"{ticker} not shortable"}

                if share_qty < 1:
                    print("Notional too low for 1 share short")
                    return {
                        "status": "error",
                        "message": "Notional too low for 1 share short",
                    }

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
