import asyncio
import json
import logging
import websockets
import aiohttp
import os
from config import CLOB_API_URL

logger = logging.getLogger(__name__)

POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

COIN_SLUGS = {
    "btc":  "btc-updown",
    "eth":  "eth-updown",
    "sol":  "sol-updown",
    "xrp":  "xrp-updown",
    "doge": "doge-updown",
    "bnb":  "bnb-updown",
    "hype": "hype-updown",
}

VALID_TIMEFRAMES = ["5m", "15m", "1h", "4h"]

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://polymarket.com/',
    'Origin': 'https://polymarket.com'
}


class MarketIngestion:
    def __init__(self, on_price_update):
        self.on_price_update = on_price_update
        self.active_markets = {}
        self.market_metadata = {}
        self.running = False

    async def resolve_active_contract(self, coin: str, timeframe: str):
        if timeframe not in VALID_TIMEFRAMES:
            logger.warning(f"Invalid timeframe: {timeframe}")
            return None, None

        base_slug = COIN_SLUGS.get(coin.lower())
        if not base_slug:
            logger.warning(f"Unknown coin: {coin}")
            return None, None

        search_slug = f"{base_slug}-{timeframe}"
        logger.info(f"Searching for active contract: {search_slug}")

        try:
            async with aiohttp.ClientSession() as session:
                gamma_url = f"https://gamma-api.polymarket.com/markets?slug={search_slug}&active=true&limit=20"
                async with session.get(gamma_url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        if not isinstance(markets, list):
                            markets = markets.get("data", [])

                        markets = [
                            m for m in markets
                            if search_slug in m.get("slug", "").lower()
                        ]

                        if not markets:
                            logger.warning(f"No active market found for {search_slug}")
                            return None, None

                        markets = sorted(
                            markets,
                            key=lambda m: m.get("endDate") or m.get("end_date_iso") or "",
                            reverse=True
                        )
                        market = markets[0]

                        condition_id = market.get("conditionId") or market.get("condition_id")
                        clob_token_ids = market.get("clobTokenIds", [])
                        token_id = clob_token_ids[0] if clob_token_ids else None
                        question = market.get("question", search_slug)

                        if condition_id and token_id:
                            logger.info(f"Resolved: {question} → {condition_id}")
                            return condition_id, token_id

        except Exception as e:
            logger.error(f"Contract resolution failed for {search_slug}: {e}")

        return None, None

    async def add_coin_market(self, coin: str, timeframe: str):
        market_id, token_id = await self.resolve_active_contract(coin, timeframe)
        if market_id and token_id:
            self.active_markets[market_id] = token_id
            self.market_metadata[market_id] = {
                "coin": coin,
                "timeframe": timeframe,
            }
            logger.info(f"Added {coin.upper()} {timeframe} → {market_id}")
            return market_id, token_id
        return None, None

    def remove_coin_market(self, coin: str, timeframe: str):
        to_remove = [
            mid for mid, meta in self.market_metadata.items()
            if meta.get("coin") == coin and meta.get("timeframe") == timeframe
        ]
        for mid in to_remove:
            self.active_markets.pop(mid, None)
            self.market_metadata.pop(mid, None)
            logger.info(f"Removed {coin.upper()} {timeframe} market.")

    async def add_market_from_url(self, url: str):
        try:
            slug = url.rstrip("/").split("/event/")[1].split("/")[0].split("#")[0] if "/event/" in url else url.rstrip("/").split("/")[-1].split("#")[0]
            token_id = await self._resolve_token_id(slug)
            if token_id:
                self.active_markets[slug] = token_id
                logger.info(f"Manual market added: {slug}")
                return slug, token_id
            else:
                logger.warning(f"Could not resolve token_id for: {slug}")
                return None, None
        except Exception as e:
            logger.error(f"URL parse error: {e}")
            return None, None

    async def _resolve_token_id(self, slug: str):
        clob_url = f"{CLOB_API_URL}/markets/{slug}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(clob_url, headers=BROWSER_HEADERS) as resp:
                    logger.info(f"CLOB API [{resp.status}] for slug: {slug}")
                    if resp.status == 200:
                        data = await resp.json()
                        tokens = data.get("tokens", [])
                        for token in tokens:
                            if token.get("outcome") == "Yes":
                                return token.get("token_id")
                    else:
                        raw = await resp.text()
                        logger.error(f"CLOB error {resp.status}: {raw[:200]}")
        except Exception as e:
            logger.error(f"CLOB request failed: {e}")

        gamma_url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(gamma_url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        markets = data if isinstance(data, list) else data.get("data", [])
                        if markets:
                            clob_ids = markets[0].get("clobTokenIds", [])
                            if clob_ids:
                                return clob_ids[0]
        except Exception as e:
            logger.error(f"Gamma fallback failed: {e}")

        return None

    async def fetch_event_submarkets(self, url: str):
        try:
            slug = url.rstrip("/").split("/event/")[1].split("/")[0].split("#")[0] if "/event/" in url else url.rstrip("/").split("/")[-1].split("#")[0]
            logger.info(f"Fetching sub-markets for slug: {slug}")

            async with aiohttp.ClientSession() as session:
                gamma_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                async with session.get(gamma_url, headers=BROWSER_HEADERS) as resp:
                    logger.info(f"Gamma API status: {resp.status} for slug: {slug}")
                    raw = await resp.text()
                    logger.info(f"Gamma response preview: {raw[:300]}")

                    if resp.status == 200:
                        data = json.loads(raw)
                        if not data:
                            return [], None

                        event = data[0]
                        event_title = event.get("title", slug)
                        markets = event.get("markets", [])
                        logger.info(f"Found {len(markets)} sub-markets for: {event_title}")

                        if not markets:
                            return [], event_title

                        result = []
                        for m in markets:
                            clob_ids = m.get("clobTokenIds", [])
                            result.append({
                                "market_id": m.get("conditionId"),
                                "question": m.get("question"),
                                "slug": m.get("slug"),
                                "liquidity": float(m.get("liquidity", 0)),
                                "end_date": m.get("endDate"),
                                "active": not m.get("closed", False),
                                "token_id": clob_ids[0] if clob_ids else None,
                                "yes_price": 0.5,
                                "no_price": 0.5,
                            })

                        result.sort(key=lambda x: x["liquidity"], reverse=True)
                        logger.info(f"Returning {len(result)} sub-markets")
                        return result, event_title
                    else:
                        logger.error(f"Gamma API error {resp.status}: {raw[:200]}")

        except Exception as e:
            logger.error(f"Sub-market fetch failed: {e}")

        return [], None

    async def scan_all_crypto_markets(self):
        logger.info("Scanning all crypto Up/Down markets...")
        tasks = []
        for coin in COIN_SLUGS:
            for tf in VALID_TIMEFRAMES:
                tasks.append(self.add_coin_market(coin, tf))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        loaded = sum(1 for r in results if r and r[0] is not None)
        logger.info(f"Bulk scan complete. Loaded {loaded} markets.")

    async def start_feed(self):
        self.running = True
        while self.running:
            token_ids = list(self.active_markets.values())
            if not token_ids:
                logger.warning("No markets loaded.")
                await asyncio.sleep(5)
                continue
            try:
                async with websockets.connect(POLYMARKET_WS_URL) as ws:
                    subscribe_msg = {
                        "type": "subscribe",
                        "channel": "market",
                        "assets_ids": token_ids
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"WebSocket subscribed to {len(token_ids)} markets.")
                    while self.running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                            await self._handle_message(raw)
                        except asyncio.TimeoutError:
                            await ws.ping()
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _handle_message(self, raw: str):
        try:
            data = json.loads(raw)
            for event in data if isinstance(data, list) else [data]:
                token_id = event.get("asset_id")
                price = float(event.get("price", 0))
                market_id = self._token_to_market(token_id)
                if market_id and price > 0:
                    await self.on_price_update(market_id, price)
        except Exception as e:
            logger.error(f"Message parse error: {e}")

    def _token_to_market(self, token_id: str):
        for market_id, tid in self.active_markets.items():
            if tid == token_id:
                return market_id
        return None

    def stop_feed(self):
        self.running = False
        logger.info("Market feed stopped.")

    def get_active_markets(self):
        return list(self.active_markets.keys())

    def get_metadata(self):
        return self.market_metadata