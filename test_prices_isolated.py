import requests
import time

COINS = ["BTC", "ETH", "SOL", "DOGE"]

def test_binance():
    print("\n--- Testing Binance ---")
    try:
        url = "https://api.binance.com/api/v3/ticker/price"
        start = time.time()
        resp = requests.get(url, timeout=10)
        end = time.time()
        print(f"Status: {resp.status_code} | Time: {end-start:.2f}s")
        if resp.status_code == 200:
            data = resp.json()
            prices = {}
            for item in data:
                symbol = item['symbol']
                for coin in COINS:
                    if symbol == f"{coin}USDT":
                        prices[coin] = item['price']
            print(f"Prices: {prices}")
            return True
    except Exception as e:
        print(f"Error: {e}")
    return False

def test_coinbase():
    print("\n--- Testing Coinbase ---")
    try:
        prices = {}
        start = time.time()
        for coin in COINS:
            pair = f"{coin}-USD"
            url = f"https://api.exchange.coinbase.com/products/{pair}/ticker"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                prices[coin] = resp.json().get('price')
        end = time.time()
        print(f"Time: {end-start:.2f}s")
        print(f"Prices: {prices}")
        return len(prices) == len(COINS)
    except Exception as e:
        print(f"Error: {e}")
    return False

def test_kraken():
    print("\n--- Testing Kraken ---")
    try:
        pairs = ",".join([f"X{c}ZUSD" if c in ["BTC", "ETH"] else f"{c}USD" for c in COINS])
        url = f"https://api.kraken.com/0/public/Ticker?pair={pairs}"
        start = time.time()
        resp = requests.get(url, timeout=10)
        end = time.time()
        print(f"Status: {resp.status_code} | Time: {end-start:.2f}s")
        if resp.status_code == 200:
            data = resp.json()
            if not data.get("error"):
                res = data.get("result", {})
                prices = {k: v["c"][0] for k, v in res.items()}
                print(f"Prices: {prices}")
                return True
    except Exception as e:
        print(f"Error: {e}")
    return False

def test_polymarket():
    print("\n--- Testing Polymarket Gamma ---")
    try:
        # Try to get the BTC price from Polymarket's own data
        url = "https://gamma-api.polymarket.com/markets?slug=bitcoin-above-68000-at-11am" # Example slug
        start = time.time()
        resp = requests.get(url, timeout=10)
        end = time.time()
        print(f"Status: {resp.status_code} | Time: {end-start:.2f}s")
        if resp.status_code == 200:
            print("Successfully reached Polymarket Gamma!")
            return True
    except Exception as e:
        print(f"Error: {e}")
    return False

def test_polymarket_index():
    print("\n--- Testing Polymarket Index ---")
    try:
        # Fetch active BTC Up/Down market
        url = "https://gamma-api.polymarket.com/markets?query=Bitcoin%20Up%20or%20Down&active=true&limit=1"
        resp = requests.get(url, timeout=10).json()
        if resp:
            market = resp[0]
            print(f"Market: {market.get('question')}")
            # Look for index price in description or custom fields
            # Polymarket often puts the "Target" or "Current" price in the question or description
            print(f"Description: {market.get('description')[:100]}...")
            return True
    except Exception as e:
        print(f"Error: {e}")
    return False

def test_others():
    print("\n--- Testing Others ---")
    urls = [
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd",
        "https://api.kraken.com/0/public/Time",
        "https://min-api.cryptocompare.com/data/price?fsym=BTC&tsyms=USD"
    ]
    for url in urls:
        try:
            start = time.time()
            resp = requests.get(url, timeout=5)
            print(f"URL: {url[:30]}... | Status: {resp.status_code} | Time: {time.time()-start:.2f}s")
        except Exception as e:
            print(f"URL: {url[:30]}... | Error: {type(e).__name__}")

if __name__ == "__main__":
    test_others()
