import asyncio
import logging
import json
import aiohttp
from wallet.pocket import Pocket
from market.ingestion import MarketIngestion
from strategy.swing_flip import SwingFlip
from risk.controls import RiskControls

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("TestBot")

async def test_market_detection():
    print("\n--- Testing Market Detection ---")
    pocket = Pocket()
    
    # Stub callback for price updates
    async def on_price_update(market_id, price):
        print(f"[CALLBACK] Price update for {market_id}: {price}")

    ingestion = MarketIngestion(on_price_update=on_price_update)
    ingestion.set_client(pocket.get_client())
    
    print("Scanning for live up/down crypto markets...")
    await ingestion.scan_all_crypto_markets()
    
    active_markets = ingestion.get_active_markets()
    metadata = ingestion.get_metadata()
    
    if not active_markets:
        print("No active markets detected. This might be due to timing or API changes.")
    else:
        print(f"Detected {len(active_markets)} active markets:")
        for mid in active_markets:
            meta = metadata.get(mid, {})
            question = meta.get("question", "Unknown Question")
            print(f" - ID: {mid}")
            print(f"   Question: {question}")
            print(f"   Tokens: {ingestion.active_markets[mid]}")
    
    return ingestion, pocket

async def test_strategy_interaction(ingestion, pocket):
    print("\n--- Testing Strategy Interaction ---")
    active_markets = ingestion.get_active_markets()
    if not active_markets:
        print("Skipping strategy test: No active markets to interact with.")
        return

    target_market = active_markets[0]
    tokens = ingestion.active_markets[target_market]
    meta = ingestion.get_metadata().get(target_market, {})
    
    print(f"Testing strategy on market: {target_market}")
    
    # Initialize strategy
    swing = SwingFlip(
        market_id=target_market,
        tokens=tokens,
        pocket=pocket,
        clob_client=pocket.get_client(),
        tick_size=meta.get("tick_size", 0.01),
        neg_risk=meta.get("neg_risk", False)
    )
    
    # Override thresholds for testing (simulation)
    swing.entry_threshold = 0.50
    swing.flip_threshold = 0.40
    
    print(f"Strategy initialized for {target_market}. Simulated entry threshold: {swing.entry_threshold}")
    
    # Simulate a price update that should trigger an entry
    simulated_price = 0.55
    print(f"Simulating price update: YES = {simulated_price}")
    
    # We won't actually post orders in this test unless you want to, 
    # but we can see if it attempts to call _buy
    # To avoid actual trading, we can monkeypatch _buy and _sell
    async def mock_buy(side, price, size):
        print(f"[MOCK BUY] Side: {side}, Price: {price}, Size: {size}")
        swing.position = side
        swing.entry_price = price
        swing.shares = size
        
    async def mock_sell(side, price):
        print(f"[MOCK SELL] Side: {side}, Price: {price}")
        swing.position = None
        swing.shares = 0.0

    swing._buy = mock_buy
    swing._sell = mock_sell
    
    await swing.on_price_update(simulated_price)
    
    if swing.position:
        print(f"SUCCESS: Strategy detected entry and 'bought' {swing.position}")
    else:
        print("FAILURE: Strategy did not enter as expected.")

async def main():
    ingestion, pocket = await test_market_detection()
    await test_strategy_interaction(ingestion, pocket)

if __name__ == "__main__":
    asyncio.run(main())
