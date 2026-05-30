import logging
import asyncio
import time
from collections import deque
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from settings import settings_manager

logger = logging.getLogger(__name__)

# #region debug-point single-trade-stall:web-setup
import json as _dbg_json, urllib.request as _dbg_ur, time as _dbg_time
_dbg_env_path = ".dbg/single-trade-stall.env"
_dbg_url = "http://127.0.0.1:7777/event"
_dbg_session = "single-trade-stall"
try:
    with open(_dbg_env_path, "r", encoding="utf-8") as _f:
        _c = _f.read().splitlines()
    for _line in _c:
        if _line.startswith("DEBUG_SERVER_URL="):
            _dbg_url = _line.split("=", 1)[1].strip() or _dbg_url
        elif _line.startswith("DEBUG_SESSION_ID="):
            _dbg_session = _line.split("=", 1)[1].strip() or _dbg_session
except Exception:
    pass

def _dbg_emit(hypothesis_id: str, location: str, msg: str, data=None, run_id: str = "pre"):
    try:
        _payload = {
            "sessionId": _dbg_session,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "msg": msg,
            "data": data or {},
            "ts": int(_dbg_time.time() * 1000),
        }
        _dbg_ur.urlopen(
            _dbg_ur.Request(
                _dbg_url,
                data=_dbg_json.dumps(_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=0.25,
        ).read()
    except Exception:
        pass
# #endregion

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
_pending_autostarts = {}
_trade_events = deque(maxlen=250)
_trade_events_lock = asyncio.Lock()
_trade_event_id = 0


async def _record_trade_event(market_id: str, message: str, level: str = "info"):
    global _trade_event_id
    mid = str(market_id or "")
    msg = str(message or "")
    async with _trade_events_lock:
        _trade_event_id += 1
        _trade_events.append(
            {
                "id": int(_trade_event_id),
                "ts": float(time.time()),
                "market_id": mid,
                "level": str(level or "info"),
                "message": msg,
            }
        )
        return int(_trade_event_id)


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


@app.get("/api/holdings")
async def get_holdings(timeframe: str = "5m"):
    client_v2 = _pocket.get_client_v2()
    recent_trades = []
    if client_v2:
        try:
            recent_trades = await asyncio.to_thread(client_v2.get_trades, None, True, None)
        except Exception:
            recent_trades = []

    tokens_by_market = {}

    for mid, tokens in _ingestion.active_markets.items():
        meta = _ingestion.market_metadata.get(mid, {})
        if timeframe and meta.get("timeframe") and meta.get("timeframe") != timeframe:
            continue
        yes_token = tokens.get("yes")
        no_token = tokens.get("no")
        if yes_token:
            tokens_by_market.setdefault(mid, set()).add(str(yes_token))
        if no_token:
            tokens_by_market.setdefault(mid, set()).add(str(no_token))

    for t in recent_trades:
        mid = t.get("market")
        token_id = t.get("asset_id")
        if not mid or not token_id:
            continue
        tokens_by_market.setdefault(str(mid), set()).add(str(token_id))

    rows = []
    for mid, token_ids in tokens_by_market.items():
        meta = _ingestion.market_metadata.get(mid, {})
        yes_token = _ingestion.active_markets.get(mid, {}).get("yes") if mid in _ingestion.active_markets else None
        no_token = _ingestion.active_markets.get(mid, {}).get("no") if mid in _ingestion.active_markets else None

        nonzero = []
        for token_id in token_ids:
            bal = await _pocket.get_token_balance(token_id)
            if bal >= 0.01:
                nonzero.append((token_id, bal))

        if not nonzero:
            continue

        rows.append(
            {
                "market_id": mid,
                "coin": meta.get("coin"),
                "question": meta.get("question"),
                "last_price": meta.get("last_price"),
                "yes_token": yes_token,
                "no_token": no_token,
                "tokens": [
                    {"token_id": tid, "balance": round(float(b), 6)}
                    for tid, b in sorted(nonzero, key=lambda x: -x[1])
                ],
            }
        )

    return {"holdings": rows}


@app.post("/api/liquidate")
async def liquidate(data: dict):
    all_markets = bool(data.get("all", True))
    market_id = data.get("market_id")
    token_id = data.get("token_id")

    if not all_markets and not market_id and not token_id:
        return {"error": "Provide token_id, market_id, or set all=true"}

    client_v2 = _pocket.get_client_v2()
    if not client_v2:
        return {"error": "CLOB client not ready"}

    client_v1 = _pocket.get_client()

    from py_clob_client_v2.clob_types import PartialCreateOrderOptions, OrderType, OrderArgs
    from py_clob_client_v2.clob_types import OrderPayload
    from py_clob_client_v2.order_builder.constants import SELL

    targets = set()
    if token_id:
        targets.add((None, str(token_id)))
    else:
        if all_markets:
            holdings = (await get_holdings(timeframe="")).get("holdings", [])
            for row in holdings:
                mid = row.get("market_id")
                for t in row.get("tokens", []):
                    tid = t.get("token_id")
                    if tid:
                        targets.add((mid, str(tid)))
        else:
            holdings = (await get_holdings(timeframe="")).get("holdings", [])
            for row in holdings:
                if row.get("market_id") != market_id:
                    continue
                mid = row.get("market_id")
                for t in row.get("tokens", []):
                    tid = t.get("token_id")
                    if tid:
                        targets.add((mid, str(tid)))

    results = []
    for mid, tid in sorted(list(targets), key=lambda x: (x[0] or "", x[1])):
        bal = await _pocket.get_token_balance(tid)
        if bal < 0.01:
            continue

        size = round(float(bal), 2)
        if size < 0.01:
            continue

        order_args = OrderArgs(
            token_id=tid,
            price=0.01,
            size=float(size),
            side=SELL,
        )
        neg_risk = False
        if mid and mid in _ingestion.market_metadata:
            neg_risk = bool(_ingestion.market_metadata.get(mid, {}).get("neg_risk", False))
        elif client_v1:
            try:
                neg_risk = bool(await asyncio.to_thread(client_v1.get_neg_risk, tid))
            except Exception:
                neg_risk = False

        options = PartialCreateOrderOptions(neg_risk=neg_risk)
        resp = {}
        order_id = None
        matched = 0.0
        error = None
        try:
            signed_order = await asyncio.to_thread(client_v2.create_order, order_args, options)
            order_type = getattr(OrderType, "IOC", OrderType.GTC)
            resp = await asyncio.to_thread(client_v2.post_order, signed_order, order_type)

            order_id = resp.get("orderID") or resp.get("orderId") or resp.get("id")
            if resp.get("success") and order_id:
                try:
                    order = await asyncio.to_thread(client_v2.get_order, order_id)
                    matched = float(order.get("size_matched", 0) or 0)
                    await asyncio.to_thread(client_v2.cancel_order, OrderPayload(orderID=order_id))
                except Exception:
                    pass
        except Exception as e:
            error = f"{type(e).__name__}: {e}"

        results.append(
            {
                "market_id": mid,
                "token_id": tid,
                "balance": round(float(bal), 6),
                "attempted": float(size),
                "success": bool(resp.get("success")),
                "order_id": order_id,
                "matched": round(float(matched), 6),
                "neg_risk": bool(neg_risk),
                "error": error,
            }
        )

    await _pocket.refresh_balances(force=True)
    return {"status": "ok", "results": results, "balance": _pocket.get_summary()}


@app.get("/api/status")
async def get_status():
    return _risk.get_status()

@app.get("/api/trade-events")
async def get_trade_events(after: int = 0, limit: int = 100):
    try:
        after_id = int(after or 0)
    except Exception:
        after_id = 0
    try:
        lim = int(limit or 100)
    except Exception:
        lim = 100
    lim = max(1, min(200, lim))

    async with _trade_events_lock:
        latest_id = int(_trade_event_id)
        events = [e for e in list(_trade_events) if int(e.get("id", 0) or 0) > after_id]
    if len(events) > lim:
        events = events[-lim:]
    return {"events": events, "latest_id": latest_id}


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
    _dbg_emit("E", "bot/webapp_server.py:coin_toggle", "[DEBUG] coin-toggle", {"coin": coin, "enabled": bool(enabled), "timeframe": timeframe})

    param_keys = {"entry", "flip", "tp", "stopLoss", "buffer", "shares", "maxTrades", "cooldown", "autoRedeem"}
    to_save = {k: data[k] for k in param_keys if k in data}
    if to_save:
        settings_manager.save(to_save)

    if enabled:
        market_id, token_id = await _ingestion.add_coin_market(coin, timeframe)
        if not market_id:
            _dbg_emit("D", "bot/webapp_server.py:coin_toggle", "[DEBUG] add-coin-market failed", {"coin": coin, "timeframe": timeframe})
            return {
                "error": f"Could not find active {coin.upper()} {timeframe} market"
            }

        meta = _ingestion.market_metadata.get(market_id, {})
        window_end_ts = meta.get("window_end_ts")
        if window_end_ts and time.time() >= (float(window_end_ts) - 45.0):
            _ingestion.remove_coin_market(coin, timeframe)
            key = f"{coin}:{timeframe}"
            if key not in _pending_autostarts or _pending_autostarts.get(key) is None or _pending_autostarts[key].done():
                async def _autostart():
                    deadline = time.time() + 120.0
                    while time.time() < deadline:
                        mid, _ = await _ingestion.add_coin_market(coin, timeframe)
                        if mid:
                            m = _ingestion.market_metadata.get(mid, {})
                            end_ts = m.get("window_end_ts")
                            if end_ts and time.time() >= (float(end_ts) - 45.0):
                                await asyncio.sleep(2.0)
                                continue
                            await _record_trade_event(mid, f"{coin.upper()} {timeframe} auto-started on new market window")
                            await start_trade({"market_id": mid, **to_save})
                            return
                        await asyncio.sleep(2.0)
                _pending_autostarts[key] = asyncio.create_task(_autostart())
            await _record_trade_event(market_id, f"{coin.upper()} {timeframe} near expiry. Queued for next market window.")
            return {"status": "ok", "coin": coin, "enabled": True, "pending": True, "message": f"{coin.upper()} {timeframe} near expiry. Queued for next market."}

        trade_resp = await start_trade({"market_id": market_id, **to_save})
        if trade_resp.get("error"):
            _dbg_emit("A", "bot/webapp_server.py:coin_toggle", "[DEBUG] trade-start error", {"coin": coin, "market_id": market_id, "error": trade_resp.get("error")})
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
        await _record_trade_event("", f"{coin.upper()} {timeframe} stopped")
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
    _dbg_emit("E", "bot/webapp_server.py:start_trade", "[DEBUG] trade-request", {"market_id": market_id, "has_url": bool(data.get("url")), "profile": settings_manager.get_active_profile()})
    if not market_id:
        url = data.get("url")
        market_id, token_id = await _ingestion.add_market_from_url(url)
    
    if not market_id:
        _dbg_emit("D", "bot/webapp_server.py:start_trade", "[DEBUG] resolve-market failed", {})
        return {"error": "Could not resolve market"}
        
    if not _risk.can_open_trade(market_id):
        _dbg_emit("A", "bot/webapp_server.py:start_trade", "[DEBUG] blocked-by-risk", {"market_id": market_id})
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

    async def _ui_notify(message: str):
        await _record_trade_event(market_id, message)

    swing = SwingFlip(
        market_id=market_id,
        tokens=tokens,
        pocket=_pocket,
        notify=_ui_notify,
        on_done=_risk.close_trade_async,
        tick_size=tick_size,
        neg_risk=meta.get("neg_risk", False),
        window_end_ts=meta.get("window_end_ts"),
    )
    
    # Apply settings (merging any one-off overrides from the UI)
    apply_settings(swing, data)
    _dbg_emit(
        "E",
        "bot/webapp_server.py:start_trade",
        "[DEBUG] trade-created",
        {
            "market_id": market_id,
            "entry": float(swing.entry_threshold),
            "tp": float(swing.take_profit_price) if swing.take_profit_price is not None else None,
            "sl": float(swing.stop_loss_price) if swing.stop_loss_price is not None else None,
            "flip": float(swing.flip_threshold),
            "buffer": float(swing.buy_buffer),
            "shares": float(swing.num_shares),
        },
    )
    
    _risk.register_trade(market_id, swing)
    await _record_trade_event(market_id, "Trade started (watching for entry)")
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
    s = settings_manager.get_all()
    param_keys = {"entry", "flip", "tp", "stopLoss", "buffer", "shares", "maxTrades", "cooldown", "autoRedeem", "master"}
    return {k: s.get(k) for k in param_keys if k in s}

@app.get("/api/profile")
async def get_profile():
    return {"active_profile": settings_manager.get_active_profile()}

@app.post("/api/profile")
async def set_profile(data: dict):
    profile = data.get("profile")
    settings_manager.set_active_profile(profile)
    for swing in _risk.active_trades.values():
        apply_settings(swing)
        logger.info(f"[{swing.market_id}] Profile switched -> {settings_manager.get_active_profile()}")
    return {"status": "ok", "active_profile": settings_manager.get_active_profile(), "params": settings_manager.get_all()}

@app.post("/api/params")
async def save_params(data: dict):
    param_keys = {"entry", "flip", "tp", "stopLoss", "buffer", "shares", "maxTrades", "cooldown", "autoRedeem", "master"}
    filtered = {k: data[k] for k in param_keys if k in data}
    if filtered:
        settings_manager.save(filtered)
    # Update ALL active trades immediately, even those currently in a position
    for swing in _risk.active_trades.values():
        apply_settings(swing)
        logger.info(f"[{swing.market_id}] Settings synchronized live: Entry={swing.entry_threshold:.2f}, TP={swing.take_profit_price:.2f}")
    return {"status": "saved", "params": await get_params()}


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
