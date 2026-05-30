import asyncio
import json
import logging
import websockets
import aiohttp
import os
from config import CLOB_API_URL

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=3)

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
        self.clob_client = None
        self._polling = set()  

    def set_client(self, client):
        self.clob_client = client

    async def resolve_active_contract(self, coin: str, timeframe: str):
        coin_names = {
            "btc": "Bitcoin", "eth": "Ethereum", "sol": "Solana",
            "xrp": "XRP", "doge": "Dogecoin", "bnb": "BNB", "hype": "Hype"
        }
        name = coin_names.get(coin.lower(), coin.upper())
        
        # Determine current UTC time and match interval for slug matching
        import time
        now = int(time.time())
        
        # Map timeframe to seconds
        tf_map = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        seconds = tf_map.get(timeframe.lower(), 300)
        window_ts = now - (now % seconds) 
        
        # Try deterministic slug first (most reliable for up/down)
        deterministic_slug = f"{coin.lower()}-updown-{timeframe.lower()}-{window_ts}"
        logger.info(f"Trying deterministic slug: {deterministic_slug}")
        
        cond_id, tokens, tick_size = await self._resolve_token_id_with_tick(deterministic_slug)
        if tokens and cond_id:
            return cond_id, tokens, tick_size, False
        
        # Fallback to search (strict filtering to avoid wrong markets)
        search_query = f"{name} Up or Down"
        logger.info(f"Searching for active {name} via Gamma Query...")
        
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                query_url = f"https://gamma-api.polymarket.com/markets?query={search_query}&active=true&closed=false&limit=10"
                async with session.get(query_url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        clob_markets = [m for m in markets if m.get("enableOrderBook") and m.get("clobTokenIds")]
                        
                        if clob_markets:
                            prefix = f"{coin.lower()}-updown"
                            valid = []
                            for m in clob_markets:
                                slug = (m.get("slug") or "").lower()
                                q = (m.get("question") or "").lower()
                                if prefix not in (slug + q):
                                    continue
                                if timeframe.lower() not in (slug + q):
                                    continue
                                valid.append(m)
                            
                            if not valid:
                                valid = []
                            
                            valid.sort(key=lambda x: x.get("endDate") or "")
                            if not valid:
                                return None, None, 0.01, False

                            market = valid[0]
                            
                            condition_id = market.get("conditionId")
                            tick_size = float(market.get("minimumTickSize") or market.get("tickSize") or 0.01)
                            neg_risk = market.get("negRisk", False)
                            
                            clob_ids = market.get("clobTokenIds", [])
                            if isinstance(clob_ids, str):
                                clob_ids = json.loads(clob_ids)
                            
                            yes_token = str(clob_ids[0]) if len(clob_ids) > 0 and clob_ids[0] is not None else None
                            no_token = str(clob_ids[1]) if len(clob_ids) > 1 and clob_ids[1] is not None else None
                            
                            if condition_id and yes_token:
                                logger.info(f"✅ Resolved: {market.get('question')} → {condition_id}")
                                return condition_id, {"yes": yes_token, "no": no_token}, tick_size, neg_risk

            slug = COIN_SLUGS.get(coin.lower())
            if slug:
                logger.info(f"Fallback: Searching via Event Slug: {slug}...")
                async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                    gamma_url = f"https://gamma-api.polymarket.com/events?slug={slug}&active=true"
                    async with session.get(gamma_url, headers=BROWSER_HEADERS) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data:
                                event = data[0]
                                markets = event.get("markets", [])
                                for m in markets:
                                    q = m.get("question", "").lower()
                                    if (timeframe.lower() in q or timeframe.lower() in m.get("slug", "")) and \
                                       not m.get("closed", False) and \
                                       m.get("enableOrderBook", False):
                                        
                                        clob_ids = m.get("clobTokenIds", [])
                                        if isinstance(clob_ids, str):
                                            clob_ids = json.loads(clob_ids)
                                        
                                        tick_size = float(m.get("minimumTickSize", 0.01))
                                        neg_risk = m.get("negRisk", False)
                                        
                                        if clob_ids:
                                            yes_token = str(clob_ids[0]) if len(clob_ids) > 0 and clob_ids[0] is not None else None
                                            no_token = str(clob_ids[1]) if len(clob_ids) > 1 and clob_ids[1] is not None else None
                                            logger.info(f"✅ Resolved via Event: {m.get('question')} → {m.get('conditionId')} (Tick: {tick_size})")
                                            return m.get("conditionId"), {"yes": yes_token, "no": no_token}, tick_size, neg_risk

        except Exception as e:
            logger.error(f"Market resolution failed: {e}")

        logger.warning(f"❌ No active {coin.upper()} {timeframe} market found.")
        return None, None, 0.01, False
    
    async def find_coin_markets(self, coin: str, timeframe: str):
        """Finds all active contracts for a coin and timeframe for manual selection."""
        results = []
        
        # 1. Try deterministic slug
        import time
        now = int(time.time())
        tf_map = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        seconds = tf_map.get(timeframe.lower(), 300)
        window_ts = now - (now % seconds) 
        deterministic_slug = f"{coin.lower()}-updown-{timeframe.lower()}-{window_ts}"
        
        condition_id, tokens, tick_size, neg_risk = await self.resolve_active_contract(coin, timeframe)
        if condition_id and tokens:
            # We already have a resolved one, let's get its details
            gamma_url = f"https://gamma-api.polymarket.com/markets?conditionId={condition_id}"
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(gamma_url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data:
                            m = data[0]
                            results.append({
                                "market_id": condition_id,
                                "question": m.get("question"),
                                "slug": m.get("slug"),
                                "liquidity": float(m.get("liquidity", 0)),
                                "active": not m.get("closed", False),
                                "token_id": tokens.get("yes"),
                                "yes_price": float(m.get("lastTradePrice", 0.5)),
                                "no_price": float(1 - m.get("lastTradePrice", 0.5))
                            })
        
        return results

    async def add_coin_market(self, coin: str, timeframe: str):
        for mid, meta in list(self.market_metadata.items()):
            if meta.get("coin") == coin and meta.get("timeframe") == timeframe and mid in self.active_markets:
                return mid, self.active_markets.get(mid)

        import time
        tf_map = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        seconds = tf_map.get(timeframe.lower(), 300)
        now = int(time.time())
        window_ts = now - (now % seconds)
        window_end_ts = window_ts + seconds
        try:
            market_id, tokens, tick_size, neg_risk = await asyncio.wait_for(
                self.resolve_active_contract(coin, timeframe),
                timeout=8,
            )
        except Exception:
            market_id, tokens, tick_size, neg_risk = None, None, 0.01, False
        if market_id and tokens:
            self.active_markets[market_id] = tokens
            self.market_metadata[market_id] = {
                "coin": coin,
                "timeframe": timeframe,
                "tick_size": tick_size,
                "neg_risk": neg_risk,
                "window_start_ts": int(window_ts),
                "window_end_ts": int(window_end_ts),
            }
            logger.info(f"Added {coin.upper()} {timeframe} → {market_id}")
            yes_token = tokens.get("yes")
            if yes_token and self.clob_client:
                try:
                    price_data = await asyncio.to_thread(self.clob_client.get_price, str(yes_token))
                    current_price = float(price_data.get("price", 0) or 0)
                    if current_price > 0:
                        self.market_metadata[market_id]["last_price"] = current_price
                        asyncio.create_task(self.on_price_update(market_id, current_price))
                except Exception:
                    pass
            return market_id, tokens
        else:
            key = (coin.lower(), timeframe.lower())
            if key not in self._polling:
                self._polling.add(key)
                logger.info(f"No active {coin.upper()} {timeframe} market yet. Starting poller...")
                asyncio.create_task(self._poll_for_market(coin, timeframe))
            return None, None

    def remove_coin_market(self, coin: str, timeframe: str):
        to_remove = []
        for mid, meta in self.market_metadata.items():
            if meta.get("coin") == coin and meta.get("timeframe") == timeframe:
                to_remove.append(mid)
        
        for mid in to_remove:
            self.active_markets.pop(mid, None)
            self.market_metadata.pop(mid, None)
            logger.info(f"Removed market: {mid} ({coin} {timeframe})")

    async def _poll_for_market(self, coin: str, timeframe: str, interval: int = 10):
        key = (coin.lower(), timeframe.lower())
        logger.info(f"Polling for {coin.upper()} {timeframe} market every {interval}s...")
        import time
        tf_map = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        seconds = tf_map.get(timeframe.lower(), 300)
        try:
            while self.running:
                await asyncio.sleep(interval)
                now = int(time.time())
                window_ts = now - (now % seconds)
                window_end_ts = window_ts + seconds
                market_id, tokens, tick_size, neg_risk = await self.resolve_active_contract(coin, timeframe)
                if market_id and tokens:
                    if market_id not in self.active_markets:
                        self.active_markets[market_id] = tokens
                        self.market_metadata[market_id] = {
                            "coin": coin,
                            "timeframe": timeframe,
                            "tick_size": tick_size,
                            "neg_risk": neg_risk,
                            "window_start_ts": int(window_ts),
                            "window_end_ts": int(window_end_ts),
                        }
                        logger.info(f"✅ Market opened! Auto-added {coin.upper()} {timeframe} → {market_id}")
                    break
                else:
                    logger.info(f"Still waiting for {coin.upper()} {timeframe} market...")
        finally:
            self._polling.discard(key)

    async def watch_market_expiry(self):
        while True:
            await asyncio.sleep(60)
            expired = []
            for market_id, meta in list(self.market_metadata.items()):
                try:
                    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                        url = f"https://gamma-api.polymarket.com/markets?conditionId={market_id}"
                        async with session.get(url, headers=BROWSER_HEADERS) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data and data[0].get("closed", False):
                                    expired.append((market_id, meta))
                except Exception as e:
                    logger.error(f"Expiry check failed for {market_id}: {e}")

            for market_id, meta in expired:
                coin = meta.get("coin")
                timeframe = meta.get("timeframe")
                logger.info(f"Market {market_id} resolved. Finding next {coin.upper()} {timeframe}...")
                self.active_markets.pop(market_id, None)
                self.market_metadata.pop(market_id, None)
                asyncio.create_task(self._poll_for_market(coin, timeframe, interval=10))

    async def start_feed(self):
        self.running = True
        
        while self.running:
            token_ids = []
            for tokens in self.active_markets.values():
                if isinstance(tokens, dict):
                    if tokens.get("yes"): token_ids.append(tokens["yes"])
                    if tokens.get("no"): token_ids.append(tokens["no"])

            if not token_ids:
                await asyncio.sleep(2)
                continue
            
            try:
                logger.info(f"🔌 Connecting to Polymarket WebSocket for {len(token_ids)} assets...")
                
                ws_headers = {
                    'User-Agent': BROWSER_HEADERS['User-Agent'],
                    'Origin': BROWSER_HEADERS['Origin'],
                    'Referer': BROWSER_HEADERS['Referer']
                }
                
                async with websockets.connect(POLYMARKET_WS_URL, extra_headers=ws_headers) as ws:
                    subscribe_msg = {
                        "assets_ids": token_ids,
                        "type": "market",
                        "custom_feature_enabled": True
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("✅ WebSocket subscribed successfully.")
                    
                    while self.running:
                        current_ids = []
                        for tokens in self.active_markets.values():
                            if isinstance(tokens, dict):
                                if tokens.get("yes"): current_ids.append(tokens["yes"])
                                if tokens.get("no"): current_ids.append(tokens["no"])

                        if set(current_ids) != set(token_ids):
                            logger.info("🔄 Markets updated, reconnecting WebSocket...")
                            break
                            
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                            logger.debug(f"WS RAW: {raw[:100]}...")
                            await self._handle_message(raw)
                        except asyncio.TimeoutError:
                            logger.debug("WS Heartbeat...")
                            await ws.ping()
            except Exception as e:
                logger.error(f"❌ WebSocket error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def add_market_from_url(self, url: str):
        try:
            clean_url = url.split("?")[0].split("#")[0].rstrip("/")
            if "/event/" in clean_url:
                slug = clean_url.split("/event/")[1].split("/")[0]
            elif "/market/" in clean_url:
                slug = clean_url.split("/market/")[1].split("/")[0]
            else:
                slug = clean_url.split("/")[-1]

            condition_id, tokens, tick_size = await self._resolve_token_id_with_tick(slug)
            if condition_id and tokens and tokens.get("yes"):
                self.active_markets[condition_id] = tokens
                self.market_metadata[condition_id] = {
                    "slug": slug,
                    "tick_size": tick_size
                }
                logger.info(f"Manual market added: {condition_id} (Slug: {slug}, Tick: {tick_size})")
                return condition_id, tokens
            else:
                logger.warning(f"Could not resolve tokens for: {slug}")
                return None, None
        except Exception as e:
            logger.error(f"URL parse error: {e}")
            return None, None

    async def _resolve_token_id_with_tick(self, slug: str):
        gamma_url = f"https://gamma-api.polymarket.com/markets?slug={slug}&active=true"
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(gamma_url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        markets = data if isinstance(data, list) else data.get("data", [])
                        if markets:
                            market = markets[0]
                            condition_id = market.get("conditionId")
                            if not market.get("enableOrderBook", False):
                                logger.warning(f"Market {slug} is NOT a CLOB market (AMM only).")
                                return None, None, 0.01

                            clob_ids = market.get("clobTokenIds", [])
                            if isinstance(clob_ids, str):
                                clob_ids = json.loads(clob_ids)
                            
                            yes_token = str(clob_ids[0]) if len(clob_ids) > 0 and clob_ids[0] is not None else None
                            no_token = str(clob_ids[1]) if len(clob_ids) > 1 and clob_ids[1] is not None else None
                            tokens = {"yes": yes_token, "no": no_token}
                            
                            tick_size = float(market.get("minimumTickSize", 0.01))
                            if yes_token and condition_id:
                                if self.clob_client:
                                    try:
                                        price_data = self.clob_client.get_price(str(yes_token))
                                        current_price = float(price_data.get("price", 0))
                                        if current_price > 0:
                                            asyncio.create_task(self.on_price_update(condition_id, current_price))
                                    except: pass
                                return condition_id, tokens, tick_size
        except Exception as e:
            logger.error(f"Gamma slug resolution failed: {e}")

        clob_url = f"{CLOB_API_URL}/markets/{slug}"
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(clob_url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("accepting_orders"):
                            condition_id = data.get("condition_id")
                            clob_tokens = data.get("tokens", [])
                            tick_size = float(data.get("minimum_tick_size", 0.01))
                            yes_token = next((t.get("token_id") for t in clob_tokens if t.get("outcome") == "Yes"), None)
                            no_token = next((t.get("token_id") for t in clob_tokens if t.get("outcome") == "No"), None)
                            yes_token = str(yes_token) if yes_token is not None else None
                            no_token = str(no_token) if no_token is not None else None
                            return condition_id, {"yes": yes_token, "no": no_token}, tick_size
        except Exception as e:
            logger.error(f"CLOB slug resolution failed: {e}")

        return None, None, 0.01

    async def _resolve_token_id(self, slug: str):
        condition_id, tokens, tick = await self._resolve_token_id_with_tick(slug)
        return tokens.get("yes") if tokens else None

    async def fetch_event_submarkets(self, url: str):
        try:
            # Clean URL and extract slug accurately
            clean_url = url.split("?")[0].split("#")[0].rstrip("/")
            if "/event/" in clean_url:
                slug = clean_url.split("/event/")[1].split("/")[0]
            elif "/market/" in clean_url:
                slug = clean_url.split("/market/")[1].split("/")[0]
            else:
                slug = clean_url.split("/")[-1]
            
            logger.info(f"Fetching sub-markets for slug: {slug}")

            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                # 1. Try Gamma Event API
                gamma_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                async with session.get(gamma_url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list):
                            event = data[0]
                            event_title = event.get("title", slug)
                            markets = event.get("markets", [])
                            if markets:
                                result = []
                                for m in markets:
                                    clob_ids = m.get("clobTokenIds", [])
                                    result.append({
                                        "market_id": m.get("conditionId"),
                                        "question": m.get("question"),
                                        "slug": m.get("slug"),
                                        "liquidity": float(m.get("liquidity", 0)),
                                        "active": not m.get("closed", False),
                                        "token_id": clob_ids[0] if clob_ids else None,
                                        "yes_price": float(m.get("lastTradePrice", 0.5)),
                                        "no_price": float(1 - m.get("lastTradePrice", 0.5)),
                                    })
                                result.sort(key=lambda x: x["liquidity"], reverse=True)
                                return result, event_title

                # 2. Try Gamma Market API (Direct Market URL)
                market_url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
                async with session.get(market_url, headers=BROWSER_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and isinstance(data, list):
                            m = data[0]
                            clob_ids = m.get("clobTokenIds", [])
                            result = [{
                                "market_id": m.get("conditionId"),
                                "question": m.get("question"),
                                "slug": m.get("slug"),
                                "liquidity": float(m.get("liquidity", 0)),
                                "active": not m.get("closed", False),
                                "token_id": clob_ids[0] if clob_ids else None,
                                "yes_price": float(m.get("lastTradePrice", 0.5)),
                                "no_price": float(1 - m.get("lastTradePrice", 0.5)),
                            }]
                            return result, m.get("question", slug)

        except Exception as e:
            logger.error(f"Sub-market fetch failed: {e}")

        return [], None

    async def scan_all_crypto_markets(self, timeframe: str = "5m"):
        """
        Manually triggered scan from UI to find markets. 
        Does NOT automatically start trading them.
        """
        import time
        logger.info(f"⚡ Manually Scanning Polymarket Events ({timeframe})...")
        
        now = int(time.time())
        tf_map = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        seconds = tf_map.get(timeframe.lower(), 300)
        window_ts = now - (now % seconds) 
        window_end_ts = window_ts + seconds
        
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            for coin, base_slug in COIN_SLUGS.items():
                deterministic_slug = f"{coin}-updown-{timeframe.lower()}-{window_ts}"
                url = f"https://gamma-api.polymarket.com/events?slug={deterministic_slug}"
                
                try:
                    async with session.get(url, headers=BROWSER_HEADERS) as resp:
                        if resp.status != 200:
                            url = f"https://gamma-api.polymarket.com/events?slug={base_slug}"
                            async with session.get(url, headers=BROWSER_HEADERS) as fallback_resp:
                                if fallback_resp.status != 200:
                                    continue
                                events = await fallback_resp.json()
                        else:
                            events = await resp.json()

                        if not events or not isinstance(events, list):
                            continue
                        
                        markets = events[0].get("markets", [])
                        
                        for market in markets:
                            if not market.get("closed", False) and market.get("enableOrderBook", True):
                                market_id = market.get("conditionId")
                                token_ids_str = market.get("clobTokenIds")
                                
                                if isinstance(token_ids_str, str):
                                    token_ids = json.loads(token_ids_str)
                                else:
                                    token_ids = token_ids_str
                                
                                if market_id and token_ids and len(token_ids) >= 2:
                                    self.active_markets[market_id] = {
                                        "yes": str(token_ids[0]),
                                        "no": str(token_ids[1])
                                    }
                                    
                                    # Fetch current price immediately for UI
                                    last_price = float(market.get("lastTradePrice") or 0.5)
                                    
                                    self.market_metadata[market_id] = {
                                        "coin": coin,
                                        "slug": market.get("slug"),
                                        "tick_size": float(market.get("tickSize", 0.01)),
                                        "neg_risk": market.get("negRisk", False),
                                        "question": market.get("question"),
                                        "last_price": last_price,
                                        "timeframe": timeframe,
                                        "window_start_ts": int(window_ts),
                                        "window_end_ts": int(window_end_ts),
                                    }
                                    logger.info(f"🔍 MARKET DISCOVERED ({coin.upper()}): {market.get('question')} at ${last_price:.4f}")
                                    # We do NOT trigger trades here. User must toggle them in UI.
                                    break
                except Exception as e:
                    logger.error(f"❌ Exception in market scanner for {coin}: {e}")

    async def _handle_message(self, raw: str):
        try:
            data = json.loads(raw)
            for event in data if isinstance(data, list) else [data]:
                event_type = event.get("event_type")

                if event_type == "price_change":
                    for change in event.get("price_changes", []):
                        token_id = change.get("asset_id")
                        price = float(change.get("price", 0))
                        market_id, side = self._resolve_market_and_side(token_id)
                        if market_id and price > 0:
                            yes_price = float(price if side == "yes" else round(1 - price, 4))
                            # Update metadata with last price for UI
                            if market_id in self.market_metadata:
                                self.market_metadata[market_id]["last_price"] = yes_price
                            # Restored layout signature tracking back to Orchestration layers
                            await self.on_price_update(market_id, yes_price)

                elif event_type in ("last_trade_price", "book"):
                    token_id = event.get("asset_id")
                    price = float(event.get("price", 0))
                    market_id, side = self._resolve_market_and_side(token_id)
                    if market_id and price > 0:
                        yes_price = float(price if side == "yes" else round(1 - price, 4))
                        if market_id in self.market_metadata:
                            self.market_metadata[market_id]["last_price"] = yes_price
                        await self.on_price_update(market_id, yes_price)

                elif event_type == "best_bid_ask":
                    token_id = event.get("asset_id")
                    best_bid = float(event.get("best_bid", 0))
                    best_ask = float(event.get("best_ask", 0))
                    mid = round((best_bid + best_ask) / 2, 4) if best_bid and best_ask else 0
                    market_id, side = self._resolve_market_and_side(token_id)
                    if market_id and mid > 0:
                        yes_price = float(mid if side == "yes" else round(1 - mid, 4))
                        await self.on_price_update(market_id, yes_price)

        except Exception as e:
            logger.error(f"Message parse error: {e}")

    def _resolve_market_and_side(self, token_id: str):
        token_id = str(token_id) if token_id is not None else ""
        for market_id, tokens in self.active_markets.items():
            if isinstance(tokens, dict):
                if str(tokens.get("yes")) == token_id:
                    return market_id, "yes"
                if str(tokens.get("no")) == token_id:
                    return market_id, "no"
            elif tokens == token_id:
                return market_id, "yes"
        return None, None

    def _token_to_market(self, token_id: str):
        mid, side = self._resolve_market_and_side(token_id)
        return mid

    def stop_feed(self):
        self.running = False
        logger.info("Market feed stopped.")

    def get_active_markets(self):
        return list(self.active_markets.keys())

    def get_metadata(self):
        return self.market_metadata
