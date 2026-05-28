import aiohttp
import asyncio
import json

async def check():
    headers = {'User-Agent': 'Mozilla/5.0'}
    async with aiohttp.ClientSession() as session:
        url = "https://gamma-api.polymarket.com/events?slug=btc-updown"
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            if data:
                markets = data[0].get("markets", [])
                for m in markets:
                    print(f"Q: {m.get('question')} | Closed: {m.get('closed')} | CLOB: {m.get('enableOrderBook')}")

if __name__ == "__main__":
    asyncio.run(check())
