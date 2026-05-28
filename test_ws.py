import asyncio
import logging
import websockets
import json
import aiohttp

logging.basicConfig(level=logging.INFO)

POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://polymarket.com/',
    'Origin': 'https://polymarket.com'
}

async def get_live_token():
    """Fetches a guaranteed active market token ID from the Gamma API."""
    url = "https://gamma-api.polymarket.com/markets?query=Bitcoin&active=true&closed=false&limit=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=BROWSER_HEADERS) as resp:
            if resp.status == 200:
                markets = await resp.json()
                if markets and "clobTokenIds" in markets[0]:
                    clob_ids = markets[0]["clobTokenIds"]
                    if isinstance(clob_ids, str):
                        clob_ids = json.loads(clob_ids)
                    if clob_ids:
                        print(f"🎯 Target Market: {markets[0].get('question')}")
                        print(f"🔑 Using Live Asset ID: {clob_ids[0]}")
                        return clob_ids[0]
    return None

async def test_connection():
    # 1. Dynamically get a token that is active right now
    live_token = await get_live_token()
    if not live_token:
        print("❌ Could not retrieve an active live token from Gamma API.")
        return

    print("\nConnecting to Polymarket WebSocket...")
    ws_headers = {
        'User-Agent': BROWSER_HEADERS['User-Agent'],
        'Origin': BROWSER_HEADERS['Origin'],
        'Referer': BROWSER_HEADERS['Referer']
    }
    
    try:
        async with websockets.connect(POLYMARKET_WS_URL, extra_headers=ws_headers) as ws:
            print("✅ Successfully Handshaked and Connected!")
            
            # 2. Subscribe using the dynamic live token ID
            subscribe_msg = {
                "assets_ids": [live_token],
                "type": "market"
            }
            
            await ws.send(json.dumps(subscribe_msg))
            print("Subscription message sent. Watching for live traffic...")
            
            # Listen to see actual real-time pricing dictionaries coming through
            for i in range(5):
                msg = await ws.recv()
                print(f"📥 [Frame {i+1}] Received: {msg[:300]}")
                
    except Exception as e:
        print(f"❌ Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())