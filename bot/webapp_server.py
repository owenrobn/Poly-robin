import logging
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from settings import settings_manager

logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global references to bot objects
_pocket = None
_risk = None
_ingestion = None


def start_server(pocket, risk, ingestion):
    global _pocket, _risk, _ingestion
    _pocket = pocket
    _risk = risk
    _ingestion = ingestion
    logger.info("Starting Mini App server on port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


@app.get("/", response_class=HTMLResponse)
async def serve_miniapp():
    with open("bot/miniapp.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/balance")
async def get_balance():
    await _pocket.refresh_balances()
    summary = _pocket.get_summary()
    
    # Calculate additional value from active positions
    position_value = 0.0
    for mid, swing in _risk.active_trades.items():
        state = swing.get_state()
        if state.get("position") and state.get("shares"):
            # Estimate current value based on last price seen by ingestion
            meta = _ingestion.market_metadata.get(mid, {})
            last_price = meta.get("last_price", state.get("entry_price", 0.5))
            
            # Value = shares * current_price (if YES, last_price; if NO, 1-last_price)
            if state["position"] == "YES":
                position_value += state["shares"] * last_price
            else:
                position_value += state["shares"] * (1 - last_price)
                
    summary["position_value"] = round(position_value, 2)
    summary["total_portfolio"] = round(summary["usdc_balance"] + position_value, 2)
    
    return summary


@app.get("/api/status")
async def get_status():
    return _risk.get_status()


@app.post("/api/master")
async def toggle_master(data: dict):
    enabled = bool(data.get("enabled"))
    settings_manager.save({"master": enabled})
    if enabled:
        await _ingestion.start_feed()
    else:
        _ingestion.stop_feed()
    return {"status": "ok", "enabled": enabled}


@app.get("/api/find-markets")
async def find_markets(coin: str, timeframe: str = "5m"):
    markets = await _ingestion.find_coin_markets(coin, timeframe)
    return {"markets": markets}


@app.post("/api/coin-toggle")
async def coin_toggle(data: dict):
    coin = data.get("coin")
    enabled = data.get("enabled")
    timeframe = data.get("timeframe", "5m")
    logger.info(f"UI coin-toggle: {coin} {timeframe} -> {'ON' if enabled else 'OFF'}")
    if enabled:
        market_id, token_id = await _ingestion.add_coin_market(coin, timeframe)
        if not market_id:
            return {
                "error": f"Could not find active {coin.upper()} {timeframe} market"
            }
        trade_resp = await start_trade({"market_id": market_id})
        if trade_resp.get("error"):
            return {"error": trade_resp["error"]}
        return {
            "status": "ok",
            "coin": coin,
            "enabled": True,
            "market_id": market_id,
            "trade": trade_resp
        }
    else:
        _ingestion.remove_coin_market(coin, timeframe)
        return {"status": "ok", "coin": coin, "enabled": False}


@app.post("/api/auto")
async def toggle_auto(data: dict):
    if data.get("enabled"):
        timeframe = data.get("timeframe", "5m")
        await _ingestion.scan_all_crypto_markets(timeframe)
        return {
            "status": "on",
            "timeframe": timeframe,
            "markets": _ingestion.get_active_markets()
        }
    else:
        _ingestion.stop_feed()
        return {"status": "off"}


def apply_settings(swing, data=None):
    """Apply settings from settings_manager or provided data to a swing instance."""
    s = settings_manager.get_all()
    if data:
        s.update(data)
    
    swing.entry_threshold = float(s.get("entry", 70)) / 100
    swing.flip_threshold = float(s.get("flip", 60)) / 100
    swing.take_profit_price = float(s.get("tp", 95)) / 100
    swing.stop_loss_price = float(s.get("stopLoss", 45)) / 100
    swing.buy_buffer = float(s.get("buffer", 2)) / 100
    swing.num_shares = float(s.get("shares", 10))
    swing.cooldown_seconds = float(s.get("cooldown", 30))
    swing.auto_redeem = s.get("autoRedeem", True)

@app.post("/api/trade")
async def start_trade(data: dict):
    from strategy.swing_flip import SwingFlip
    market_id = data.get("market_id")
    if not market_id:
        url = data.get("url")
        market_id, token_id = await _ingestion.add_market_from_url(url)
    
    if not market_id:
        return {"error": "Could not resolve market"}
        
    if not _risk.can_open_trade(market_id):
        return {"error": "Trade blocked by risk controls"}
    
    tokens = _ingestion.active_markets.get(market_id, {})
    if not tokens:
        # Re-resolve if not in active_markets (e.g. from manual find)
        _, tokens, _, _ = await _ingestion.resolve_active_contract(market_id.split("-")[0], "5m")
        if tokens:
            _ingestion.active_markets[market_id] = tokens
            _ingestion.market_metadata[market_id] = {"tick_size": 0.01}
            
    if not tokens:
        return {"error": "Tokens not resolved for market"}

    meta = _ingestion.market_metadata.get(market_id, {})
    tick_size = meta.get("tick_size", 0.01)
    swing = SwingFlip(
        market_id=market_id,
        tokens=tokens,
        pocket=_pocket,
        notify=None,
        on_done=_risk.close_trade_async,
        tick_size=tick_size,
        neg_risk=meta.get("neg_risk", False)
    )
    
    # Apply settings (merging any one-off overrides from the UI)
    apply_settings(swing, data)
    
    _risk.register_trade(market_id, swing)
    return {"status": "started", "market_id": market_id}


@app.get("/api/prices")
async def get_all_prices(timeframe: str = "5m"):
    # 1. Get Polymarket prices from ingestion state
    poly_prices = {}
    for mid, tokens in _ingestion.active_markets.items():
        meta = _ingestion.market_metadata.get(mid, {})
        
        # Filter by timeframe if provided
        if timeframe and meta.get("timeframe") != timeframe:
            # If we don't have a timeframe in meta (manual URL), we include it anyway
            if "timeframe" in meta:
                continue
                
        # Use explicit 'coin' key if available, else derive from slug
        coin = meta.get("coin")
        if not coin:
            slug = meta.get("slug", "")
            coin = slug.split("-")[0].lower() if "-" in slug else "unknown"
        
        poly_prices[coin] = meta.get("last_price", 0.0)

    # 2. Get Index prices (Verified sources: CoinGecko/CryptoCompare)
    from signaler import get_live_crypto_prices
    try:
        index_prices = await asyncio.wait_for(asyncio.to_thread(get_live_crypto_prices), timeout=2.5)
    except Exception:
        index_prices = {}
    
    return {
        "polymarket": poly_prices,
        "index": index_prices or {}
    }


@app.post("/api/ping-trade")
async def ping_trade(data: dict):
    # This endpoint is called by the signaler.
    # We NO LONGER auto-initiate trades from external signals.
    # We just log it for the user to see in the terminal.
    coin = data.get("coin", "unknown").upper()
    direction = data.get("direction", "YES")
    logger.info(f"📡 SIGNAL RECEIVED: {coin} looks {direction}. (Manual initiation required via UI)")
    return {"status": "signal_received_manual_only"}

@app.get("/api/params")
async def get_params():
    return settings_manager.get_all()

@app.post("/api/params")
async def save_params(data: dict):
    settings_manager.save(data)
    # Update ALL active trades immediately, even those currently in a position
    for swing in _risk.active_trades.values():
        apply_settings(swing)
        logger.info(f"[{swing.market_id}] Settings synchronized live: Entry={swing.entry_threshold:.2f}, TP={swing.take_profit_price:.2f}")
    return {"status": "saved", "params": settings_manager.get_all()}


@app.post("/api/kill")
async def kill_all():
    await _risk.kill_all()
    return {"status": "killed"}


@app.post("/api/reset")
async def reset():
    _risk.reset_kill_switch()
    return {"status": "reset"}


@app.get("/api/submarkets")
async def get_submarkets(url: str):
    markets, event_title = await _ingestion.fetch_event_submarkets(url)
    return {"markets": markets, "event_title": event_title}

@app.post("/api/auto-redeem")
async def toggle_auto_redeem(data: dict):
    enabled = data.get("enabled", True)
    # Store setting — applied per swing instance
    for swing in _risk.active_trades.values():
        swing.auto_redeem = enabled
    return {"status": "ok", "auto_redeem": enabled}

@app.post("/api/claim")
async def claim_all():
    redeemed = 0
    for market_id, swing in _risk.active_trades.items():
        try:
            await swing._redeem_profits()
            redeemed += 1
        except Exception as e:
            logger.error(f"Claim failed for {market_id}: {e}")
    return {"status": "ok", "redeemed": redeemed}


@app.get("/api/debug-url")
async def debug_url(url: str):
    import aiohttp
    slug = url.rstrip("/").split("/event/")[1].split("/")[0].split("#")[0] if "/event/" in url else url.rstrip("/").split("/")[-1].split("#")[0]
    results = {}
    async with aiohttp.ClientSession() as session:
        # Try Gamma API with different endpoint formats
        for endpoint in [
            f"https://gamma-api.polymarket.com/events?slug={slug}&limit=1",
            f"https://gamma-api.polymarket.com/events?_slug={slug}&limit=1",
            f"https://gamma-api.polymarket.com/markets?event_slug={slug}&limit=5",
            f"https://clob.polymarket.com/markets/{slug}",
        ]:
            key = endpoint.split("?")[0].split("/")[-1] + "_" + endpoint.split("?")[-1][:30]
            async with session.get(endpoint) as r:
                try:
                    results[key] = await r.json()
                except:
                    results[key] = f"status {r.status}"
    return results
