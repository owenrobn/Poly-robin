import asyncio
import json
import logging
import websockets
import aiohttp
from config import CLOB_API_URL

logger = logging.getLogger(__name__)

POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Coin slug mapping for Up/Down markets
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


class MarketIngestion:
    def __init__(self, on_price_update):
        self.on_price_update = on_price_update
        self.active_markets = {}        # market_id: token_id
        self.market_metadata = {}       # market_id: { coin, timeframe, slug }
        self.running = False

    # --- Dynamic Slug Resolution ---
    async def resolve_active_contract(self, coin: str, timeframe: str):
        """
        Find the latest active Up/Down contract for a coin+timeframe.
        Searches CLOB API for active markets matching the slug pattern.
        """
        if timeframe not in VALID_TIMEFRAMES:
            logger.warning(f"Invalid timeframe: {timeframe}. Use {VALID_TIMEFRAMES}")
            return None, None

        base_slug = COIN_SLUGS.get(coin.lower())
        if not base_slug:
            logger.warning(f"Unknown coin: {coin}")
            return None, None

        search_slug = f"{base_slug}-{timeframe}"
        logger.info(f"Searching for active contract: {search_slug}")

        try:
            async with aiohttp.ClientSession() as session:
                # Search by slug keyword
                url = f"{CLOB_API_URL}/markets?slug={search_slug}&active=true"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        markets = data if isinstance(data, list) else data.get("data", [])

                        if not markets:
                            # Fallback: search all active markets
                            url2 = f"{CLOB_API_URL}/markets?active=true&limit=100"
                            async with session.get(url2) as resp2:
                                if resp2.status == 200:
                                    all_data = await resp2.json()
                                    all_markets = all_data if isinstance(all_data, list) else all_data.get("data", [])
                                    markets = [
                                        m for m in all_markets
                                        if search_slug in m.get("market_slug", "").lower()
                                        or search_slug in m.get("question", "").lower()
                                    ]

                        if not markets:
                            logger.warning(f"No active market found for {search_slug}")
                            return None, None

                        # Pick most recent active market
                        market = sorted(
                                markets,
                                key=lambda m: m.get("end_date_iso") or "",
                                reverse=True
                            )[0]

                        slug = market.get("condition_id") or market.get("market_slug")
                        tokens = market.get("tokens", [])
                        token_id = next(
                            (t.get("token_id") for t in tokens if t.get("outcome") == "Yes"),
                            None
                        )

                        if token_id:
                            logger.info(f"Resolved {search_slug} → {slug}")
                            return slug, token_id

        except Exception as e:
            logger.error(f"Contract resolution failed for {search_slug}: {e}")

        return None, None

    # --- Coin Toggle (from Mini App) ---
    async def add_coin_market(self, coin: str, timeframe: str):
        """Add a coin+timeframe market from the dashboard toggle."""
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
        """Remove a coin+timeframe market when toggled off."""
        to_remove = [
            mid for mid, meta in self.market_metadata.items()
            if meta.get("coin") == coin and meta.get("timeframe") == timeframe
        ]
        for mid in to_remove:
            self.active_markets.pop(mid, None)
            self.market_metadata.pop(mid, None)
            logger.info(f"Removed {coin.upper()} {timeframe} market.")

    # --- URL Parser (for manual markets) ---
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
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        url = f"https://clob.polymarket.com/markets/{slug}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tokens = data.get("tokens", [])
                    for token in tokens:
                        if token.get("outcome") == "Yes":
                            return token.get("token_id")
                else:
                    logger.error(f"API error {resp.status} for slug: {slug}")
        return None
        
    async def fetch_event_submarkets(self, url: str):
        """
        Fetch all sub-markets from a Polymarket event URL using Gamma API.
        """
        try:
            slug = url.rstrip("/").split("/event/")[1].split("/")[0].split("#")[0] if "/event/" in url else url.rstrip("/").split("/")[-1].split("#")[0]
            logger.info(f"Fetching sub-markets for slug: {slug}")

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://polymarket.com/',
                'Origin': 'https://polymarket.com'
            }

            async with aiohttp.ClientSession() as session:
                gamma_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                async with session.get(gamma_url, headers=headers) as resp:
                    logger.info(f"Gamma API status: {resp.status} for slug: {slug}")
                    raw = await resp.text()
                    logger.info(f"Gamma response preview: {raw[:300]}")

                    if resp.status == 200:
                        data = json.loads(raw)
                        if not data:
                            logger.warning(f"Empty response for slug: {slug}")
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

    async def _resolve_token_id(self, slug: str):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://polymarket.com/',
            'Origin': 'https://polymarket.com'
        }
        url = f"https://clob.polymarket.com/markets/{slug}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tokens = data.get("tokens", [])
                    for token in tokens:
                        if token.get("outcome") == "Yes":
                            return token.get("token_id")
                else:
                    logger.error(f"API error {resp.status} for slug: {slug}")
        return None

    # --- Bulk Scanner ---
    async def scan_all_crypto_markets(self):
        """Scan all active Up/Down markets for all coins and valid timeframes."""
        logger.info("Scanning all crypto Up/Down markets...")
        tasks = []
        for coin in COIN_SLUGS:
            for tf in VALID_TIMEFRAMES:
                tasks.append(self.add_coin_market(coin, tf))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        loaded = sum(1 for r in results if r and r[0] is not None)
        logger.info(f"Bulk scan complete. Loaded {loaded} markets.")

    # --- WebSocket Feed ---
    async def start_feed(self):
        """Open WebSocket and stream price updates for all active markets."""
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