import asyncio
import logging
from config import MAX_ACTIVE_TRADES
from settings import settings_manager

logger = logging.getLogger(__name__)

# #region debug-point single-trade-stall:risk-setup
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


class RiskControls:
    def __init__(self, pocket, notify=None):
        self.pocket = pocket
        self.notify = notify
        self.active_trades = {}
        self.killed = False

    def can_open_trade(self, market_id: str) -> bool:
        if self.killed:
            logger.warning("Kill switch active. No new trades.")
            _dbg_emit("A", "risk/controls.py:can_open_trade", "[DEBUG] blocked killed", {"market_id": market_id})
            return False
        if market_id in self.active_trades:
            logger.info(f"Already trading {market_id}.")
            _dbg_emit("A", "risk/controls.py:can_open_trade", "[DEBUG] blocked already-active", {"market_id": market_id})
            return False
        max_trades = int(settings_manager.get("maxTrades", MAX_ACTIVE_TRADES))
        if len(self.active_trades) >= max_trades:
            logger.warning(
                f"Max active trades reached "
                f"({max_trades}). Skipping {market_id}."
            )
            _dbg_emit(
                "A",
                "risk/controls.py:can_open_trade",
                "[DEBUG] blocked max-trades",
                {"market_id": market_id, "active": len(self.active_trades), "max": int(max_trades)},
            )
            return False
        if not self.pocket.is_ready():
            logger.warning("Pocket not ready. Skipping trade.")
            _dbg_emit("C", "risk/controls.py:can_open_trade", "[DEBUG] blocked pocket-not-ready", {"market_id": market_id})
            return False
        _dbg_emit(
            "A",
            "risk/controls.py:can_open_trade",
            "[DEBUG] allowed",
            {"market_id": market_id, "active": len(self.active_trades), "max": int(max_trades)},
        )
        return True

    def register_trade(self, market_id: str, swing_flip):
        self.active_trades[market_id] = swing_flip
        logger.info(
            f"Trade registered: {market_id}. "
            f"Active: {len(self.active_trades)}/{MAX_ACTIVE_TRADES}"
        )

    def close_trade(self, market_id: str):
        if market_id in self.active_trades:
            del self.active_trades[market_id]
            logger.info(
                f"Trade closed: {market_id}. "
                f"Active: {len(self.active_trades)}/{MAX_ACTIVE_TRADES}"
            )

    async def close_trade_async(self, market_id: str):
        self.close_trade(market_id)

    async def kill_all(self):
        self.killed = True
        logger.critical("KILL SWITCH ACTIVATED — closing all positions.")
        if self.notify:
            await self.notify(
                "🚨 KILL SWITCH ACTIVATED\n"
                "Closing all open positions."
            )
        tasks = []
        for market_id, swing in list(self.active_trades.items()):
            if swing.position is not None:
                tasks.append(
                    swing._close_position(
                        price=0.0,
                        reason="Kill Switch"
                    )
                )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.critical("Kill switch close initiated for all tracked positions.")

    def reset_kill_switch(self):
        self.killed = False
        logger.info("Kill switch reset. Trading re-enabled.")

    def get_status(self) -> dict:
        return {
            "active_trades": len(self.active_trades),
            "max_trades": MAX_ACTIVE_TRADES,
            "killed": self.killed,
            "markets": [
                {
                    "market_id": mid,
                    "state": s.get_state()
                }
                for mid, s in self.active_trades.items()
            ]
        }
