"""
Expanded Discovery & Pipeline Integration Test Core
"""

import asyncio
import aiohttp
import requests
import logging
import sys

# Try importing the local strategy to check for syntax/V2 integration stability
try:
    from strategy.swing_flip import SwingFlip
    STRATEGY_IMPORTED = True
except ModuleNotFoundError:
    # Handle direct directory inclusion pathing if required
    sys.path.append(".")
    try:
        from strategy.swing_flip import SwingFlip
        STRATEGY_IMPORTED = True
    except Exception as e:
        STRATEGY_IMPORTED = False
        strategy_import_error = e
except Exception as e:
    STRATEGY_IMPORTED = False
    strategy_import_error = e

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("DiscoveryTest")

COINS = ["btc", "eth", "sol", "doge"]
TIMEFRAMES = ["5m", "15m", "1h"]
ENTRY_THRESHOLD = 0.55
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://polymarket.com/',
}

# Dummy container class to pass safety validations in our offline test
class MockPocket:
    def calculate_size(self, price, num_shares):
        return num_shares * 2

# ─────────────────────────────────────────────
# TEST 1: Polymarket Up/Down market discovery
# ─────────────────────────────────────────────
async def test_market_discovery():
    logger.info("\n" + "="*50)
    logger.info("TEST 1: Polymarket Up/Down Market Discovery")
    logger.info("="*50)

    found = []
    async with aiohttp.ClientSession(headers=BROWSER_HEADERS) as session:
        for coin in COINS:
            slug = f"{coin}-updown"
            url = f"https://gamma-api.polymarket.com/events?slug={slug}&active=true&limit=10"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        event = data[0]
                        markets = event.get("markets", [])
                        active = [m for m in markets if not m.get("closed", False) and m.get("enableOrderBook")]
                        logger.info(f"✅ {coin.upper()}: event found → '{event.get('title')}' | active sub-markets: {len(active)}")
                        for m in active[:3]:
                            logger.info(f"   → {m.get('question')} | tokens: {m.get('clobTokenIds', 'N/A')[:45]}...")
                        found.append(coin)
                    else:
                        logger.warning(f"❌ {coin.upper()}: no active event found at slug '{slug}'")
                else:
                    logger.error(f"⚠️  {coin.upper()}: HTTP {resp.status}")

    logger.info("\n--- Direct query search (as ingestion.py does it) ---")
    async with aiohttp.ClientSession(headers=BROWSER_HEADERS) as session:
        for coin in ["Bitcoin", "Ethereum"]:
            for tf in TIMEFRAMES:
                url = f"https://gamma-api.polymarket.com/markets?query={coin}+Price+{tf}&active=true&closed=false&enableOrderBook=true&limit=5"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        matches = [
                            m for m in data
                            if coin.lower() in m.get("question", "").lower()
                            and tf.lower() in (m.get("question", "").lower() + m.get("slug", "").lower())
                        ]
                        if matches:
                            logger.info(f"✅ Query match: {coin} {tf} → '{matches[0].get('question')}'")
                            logger.info(f"   └─ Active Condition Tokens: {matches[0].get('clobTokenIds')}")
                        else:
                            logger.warning(f"❌ No query match for {coin} {tf} (got {len(data)} API variations)")

    logger.info(f"\nSummary: Found Up/Down events for {len(found)}/{len(COINS)} coins: {found}")
    return found

# ─────────────────────────────────────────────
# TEST 2: Binance spot price feed (signaler)
# ─────────────────────────────────────────────
def test_binance_feed():
    logger.info("\n" + "="*50)
    logger.info("TEST 2: Binance Spot Price Feed (Signaler)")
    logger.info("="*50)

    try:
        url = "https://api.binance.com/api/v3/ticker/price"
        resp = requests.get(url, timeout=5).json()
        prices = {}
        for item in resp:
            for coin in COINS:
                if item["symbol"] == f"{coin.upper()}USDT":
                    prices[coin] = float(item["price"])

        if prices:
            logger.info("✅ Binance feed reachable. Live prices:")
            for coin, price in prices.items():
                logger.info(f"   {coin.upper()}: ${price:,.2f}")
        else:
            logger.error("❌ No prices parsed from Binance response")
        return prices
    except Exception as e:
        logger.error(f"❌ Binance feed failed: {e}")
        return {}

# ─────────────────────────────────────────────
# TEST 3: Signaler threshold logic simulation
# ─────────────────────────────────────────────
def test_signaler_logic(prices):
    logger.info("\n" + "="*50)
    logger.info("TEST 3: Signaler Threshold Logic Simulation")
    logger.info("="*50)

    if not prices:
        logger.error("❌ No prices available — skipping signaler logic test")
        return None

    simulated_strength = 0.58
    logger.info(f"Entry threshold: {ENTRY_THRESHOLD} | Simulated signal strength: {simulated_strength}")

    payloads = []
    for coin in COINS:
        if coin not in prices:
            continue
        if simulated_strength >= ENTRY_THRESHOLD:
            logger.info(f"🎯 Signal threshold crossed for {coin.upper()} | direction: YES")
            payload = {
                "market_id": f"0xMOCK_DISCOVERY_{coin.upper()}_V2",
                "coin": coin,
                "direction": "YES",
                "strategy": "swing_flip",
                "simulated_price": prices[coin]
            }
            logger.info(f"   Generated payload: {payload}")
            payloads.append(payload)
    return payloads

# ─────────────────────────────────────────────
# NEW TEST 4: Direct Strategy Interception Test
# ─────────────────────────────────────────────
async def test_strategy_interception(payloads):
    logger.info("\n" + "="*50)
    logger.info("TEST 4: Strategy Processing Engine Check")
    logger.info("="*50)

    if not STRATEGY_IMPORTED:
        logger.error(f"❌ Strategy module skip failed: {strategy_import_error}")
        return

    if not payloads:
        logger.warning("⏸ No payloads generated from Test 3. Skipping routing tests.")
        return

    logger.info("Initializing Sandbox Strategy Loop Instance (Isolated Sandbox)...")
    
    mock_tokens = {"yes": "0xMOCK_YES_TOKEN", "no": "0xMOCK_NO_TOKEN"}
    mock_pocket = MockPocket()

    # Pass clob_client=None so it runs calculations without making real web3 network calls
    strategy_instance = SwingFlip(
        market_id=payloads[0]["market_id"],
        tokens=mock_tokens,
        pocket=mock_pocket,
        clob_client=None,
        tick_size=0.01
    )

    logger.info("Injecting simulated rapid upward trade event...")
    # Feed an incremental price update into the class logic method
    await strategy_instance.on_price_update(0.72)
    
    current_state = strategy_instance.get_state()
    logger.info(f"🎯 Post-Signal Sandbox State Matrix: Direction Tracker={current_state.get('entry_direction')} | Inside Cooldown={current_state.get('in_cooldown')}")
    logger.info("✅ Strategy calculation pipelines processed without engine level attribute crashes.")

# ─────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────
async def main():
    logger.info("🔬 Starting Poly-Robin Comprehensive Discovery Framework\n")

    found_coins = await test_market_discovery()
    prices = test_binance_feed()
    payloads = test_signaler_logic(prices)
    
    await test_strategy_interception(payloads)

    logger.info("\n" + "="*50)
    logger.info("DIAGNOSTIC COMPLETE")
    logger.info("="*50)
    if STRATEGY_IMPORTED:
        logger.info("Strategy Integration:  ✅ IMPORT PASSED (Ready to trade)")
    else:
        logger.error("Strategy Integration: ❌ IMPORT CRASHED (Check file imports)")

if __name__ == "__main__":
    asyncio.run(main())