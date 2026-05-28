import time
import requests
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("Signaler")

# Configuration
SERVER_PING_URL = "http://127.0.0.1:8000/api/ping-trade"
COINS = ["btc", "eth", "sol", "xrp", "doge", "bnb", "hype"]
ENTRY_THRESHOLD = 0.55  # Your target virtual probability or signal boundary
TICK_INTERVAL = 2       # Scan underlying price every 2 seconds

def get_live_crypto_prices():
    """Fetches high-accuracy spot prices with multiple fallbacks (CoinGecko/CryptoCompare)."""
    # 1. CoinGecko (Working in this environment)
    try:
        coin_ids = {
            "btc": "bitcoin",
            "eth": "ethereum",
            "sol": "solana",
            "doge": "dogecoin",
            "xrp": "ripple",
            "bnb": "binancecoin",
            "hype": "hyperliquid"
        }
        ids = ",".join([coin_ids.get(c, c) for c in COINS])
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            prices = {}
            for coin in COINS:
                cid = coin_ids.get(coin, coin)
                if cid in data:
                    prices[coin] = float(data[cid]['usd'])
            if prices:
                return prices
    except Exception as e:
        logger.debug(f"CoinGecko fetch failed: {e}")

    # 2. CryptoCompare (Working in this environment)
    try:
        prices = {}
        for coin in COINS:
            url = f"https://min-api.cryptocompare.com/data/price?fsym={coin.upper()}&tsyms=USD"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if 'USD' in data:
                    prices[coin] = float(data['USD'])
        if len(prices) > 0:
            return prices
    except Exception as e:
        logger.debug(f"CryptoCompare fetch failed: {e}")

    logger.error("All verified price feeds failed.")
    return None

def monitor_market_trends():
    logger.info("📡 Continuous index market scanner active...")
    
    # Track simple internal state to simulate momentum
    price_history = {coin: [] for coin in COINS}
    
    while True:
        prices = get_live_crypto_prices()
        if prices:
            for coin in COINS:
                current_price = prices.get(coin)
                if current_price is None:
                    logger.warning(f"Missing {coin.upper()} price from feed; skipping tick.")
                    continue
                history = price_history[coin]
                history.append(current_price)
                if len(history) > 10: history.pop(0)
                
                # Log the detected price clearly
                logger.info(f"Detected {coin.upper()} Index: ${current_price:,.2f}")

                # Simple momentum: check if price is higher than 5 ticks ago
                if len(history) >= 5:
                    change = (current_price - history[0]) / history[0]
                    # Map change to a "strength" score around 0.5
                    strength = 0.5 + (change * 100) # 1% move = 1.0 strength
                    
                    if strength >= ENTRY_THRESHOLD:
                        logger.info(f"🎯 Momentum verified for {coin.upper()} (Strength: {strength:.2f})! Dispatching ping...")
                        
                        payload = {
                            "coin": coin,
                            "direction": "YES",
                            "strategy": "swing_flip"
                        }
                        
                        try:
                            r = requests.post(SERVER_PING_URL, json=payload, timeout=5)
                            logger.info(f"⚡ Ping server response: {r.json()}")
                        except Exception as e:
                            logger.error(f"Failed to transmit atomic ping to broker: {e}")
                        
        time.sleep(TICK_INTERVAL)

if __name__ == "__main__":
    monitor_market_trends()
