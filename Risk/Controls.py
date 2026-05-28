import asyncio
import logging
from config import MAX_ACTIVE_TRADES
from settings import settings_manager

logger = logging.getLogger(__name__)


class RiskControls:
    def __init__(self, pocket, notify=None):
        self.pocket = pocket
        self.notify = notify
        self.active_trades = {}
        self.killed = False

    def can_open_trade(self, market_id: str) -> bool:
        if self.killed:
            logger.warning("Kill switch active. No new trades.")
            return False
        if market_id in self.active_trades:
            logger.info(f"Already trading {market_id}.")
            return False
        max_trades = int(settings_manager.get("maxTrades", MAX_ACTIVE_TRADES))
        if len(self.active_trades) >= max_trades:
            logger.warning(
                f"Max active trades reached "
                f"({max_trades}). Skipping {market_id}."
            )
            return False
        if not self.pocket.is_ready():
            logger.warning("Pocket not ready. Skipping trade.")
            return False
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
        self.active_trades.clear()
        logger.critical("All positions closed.")

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
