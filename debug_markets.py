import asyncio
import os
import logging
import aiohttp
from wallet.pocket import Pocket
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def debug_markets():
    load_dotenv()
    pocket = Pocket()
    client = pocket.get_client()
    
    if not client:
        print("❌ Failed to initialize CLOB client")
        return

    print("--- Testing specific slugs via Gamma ---")
    async with aiohttp.ClientSession() as session:
        # Use headers to avoid being blocked
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        for slug in ["btc-updown", "eth-updown", "sol-updown"]:
            gamma_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            async with session.get(gamma_url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        event = data[0]
                        print(f"✅ Slug {slug} found: {event.get('title')}")
                        markets = event.get("markets", [])
                        print(f"   Markets found: {len(markets)}")
                        active_count = 0
                        for m in markets:
                            if not m.get("closed", False):
                                active_count += 1
                                if active_count <= 5:
                                    print(f"   - {m.get('question')} | ConditionID: {m.get('conditionId')}")
                        print(f"   Total active markets: {active_count}")
                    else:
                        print(f"❌ Slug {slug} NOT found")
                else:
                    print(f"⚠️ Gamma API error for {slug}: {resp.status}")

if __name__ == "__main__":
    asyncio.run(debug_markets())
