import logging
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

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
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


@app.get("/", response_class=HTMLResponse)
async def serve_miniapp():
    with open("bot/miniapp.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/balance")
async def get_balance():
    await _pocket.refresh_balances()
    return _pocket.get_summary()


@app.get("/api/status")
async def get_status():
    return _risk.get_status()


@app.post("/api/master")
async def toggle_master(data: dict):
    if data.get("enabled"):
        await _ingestion.start_feed()
    else:
        _ingestion.stop_feed()
    return {"status": "ok"}


@app.post("/api/coin-toggle")
async def coin_toggle(data: dict):
    coin = data.get("coin")
    enabled = data.get("enabled")
    timeframe = data.get("timeframe", "5m")
    if enabled:
        market_id, token_id = await _ingestion.add_coin_market(coin, timeframe)
        if not market_id:
            return {
                "error": f"Could not find active {coin.upper()} {timeframe} market"
            }
        return {
            "status": "ok",
            "coin": coin,
            "enabled": True,
            "market_id": market_id
        }
    else:
        _ingestion.remove_coin_market(coin, timeframe)
        return {"status": "ok", "coin": coin, "enabled": False}


@app.post("/api/auto")
async def toggle_auto(data: dict):
    if data.get("enabled"):
        await _ingestion.scan_all_crypto_markets()
        return {
            "status": "on",
            "markets": _ingestion.get_active_markets()
        }
    else:
        _ingestion.stop_feed()
        return {"status": "off"}


@app.post("/api/trade")
async def start_trade(data: dict):
    from strategy.swing_flip import SwingFlip
    url = data.get("url")
    market_id, token_id = await _ingestion.add_market_from_url(url)
    if not market_id:
        return {"error": "Could not resolve market"}
    if not _risk.can_open_trade(market_id):
        return {"error": "Trade blocked by risk controls"}
    swing = SwingFlip(
        market_id=market_id,
        pocket=_pocket,
        clob_client=None,
        notify=None
    )
    swing.entry_threshold = data.get("entry", 70) / 100
    swing.flip_threshold = data.get("flip", 60) / 100
    swing.take_profit = data.get("tp", 95) / 100
    swing.stop_loss = data.get("stopLoss", 45) / 100
    swing.buy_buffer = data.get("buffer", 2) / 100
    _risk.register_trade(market_id, swing)
    return {"status": "started", "market_id": market_id}


@app.post("/api/params")
async def save_params(data: dict):
    return {"status": "saved", "params": data}


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