import asyncio
import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException
import uvicorn

# Import your existing, fixed modules
from wallet.pocket import Pocket
from strategy.swing_flip import SwingFlip
from market.ingestion import MarketIngestion  # Assuming it contains a lookup system

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("TradeServer")

app = FastAPI(title="Atomic Ping Router")

# Global instances initialized on startup
pocket_wallet = None
ingestion_manager = None

@app.on_event("startup")
async def setup_bot_state():
    global pocket_wallet, ingestion_manager
    logger.info("Initializing trade engine context modules...")
    pocket_wallet = Pocket()
    
    # Stub dummy update callback to satisfy class signatures
    async def empty_callback(market_id, yes_price): pass
    ingestion_manager = MarketIngestion(on_price_update=empty_callback)

@app.post("/api/ping-trade")
async def handle_incoming_ping(payload: dict, background_tasks: BackgroundTasks):
    coin = payload.get("coin")
    direction = payload.get("direction")
    strategy_name = payload.get("strategy")
    
    if not coin or not direction:
        raise HTTPException(status_code=400, detail="Invalid metadata properties.")
        
    logger.info(f"📥 Instant Signal Received: [{coin.upper()}] -> Target Side: {direction}")
    
    # Offload the Polymarket network call into a non-blocking background worker task
    background_tasks.add_task(execute_automated_trade, coin, direction)
    
    return {"status": "processing", "message": "Signal intercepted successfully."}

async def execute_automated_trade(coin: str, direction: str):
    try:
        logger.info(f"🔍 Performing real-time Polymarket token resolution for live {coin.upper()} 5m contract...")
        
        # 1. Use the unified resolution logic from MarketIngestion
        market_id, tokens, tick_size, neg_risk = await ingestion_manager.resolve_active_contract(coin, "5m")
        
        if not market_id or not tokens:
            logger.error(f"❌ Failed to locate any open active betting pairs on Polymarket for {coin.upper()}. Aborting transaction.")
            return

        logger.info(f"🎯 Target confirmed: {market_id}. Instantiating strategic executor...")

        # 2. Spin up strategic executor and dispatch trade instructions
        runner = SwingFlip(
            market_id=market_id,
            tokens=tokens,
            pocket=pocket_wallet,
            tick_size=tick_size,
            neg_risk=neg_risk
        )
        
        # Hardcode execution prices or retrieve raw token quotes inside your target limits
        execution_price = 0.65  
        target_shares = 5.0
        
        logger.info(f"🚀 Launching trade order to exchange books...")
        await runner._buy(side=direction, price=execution_price, size=target_shares)
        
    except Exception as e:
        logger.error(f"Critical execution error processing signal response handler: {e}")

# Removed redundant resolve_polymarket_contract_on_demand function

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)