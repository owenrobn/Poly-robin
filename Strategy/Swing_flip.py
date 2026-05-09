import asyncio
import logging
from config import (
    ENTRY_THRESHOLD, FLIP_THRESHOLD, TAKE_PROFIT,
    COOLDOWN_SECONDS, SLIPPAGE_MAX, MIN_SHARES
)

logger = logging.getLogger(__name__)


class SwingFlip:
    def __init__(self, market_id, pocket, clob_client, notify=None):
        self.market_id = market_id
        self.pocket = pocket
        self.clob = clob_client
        self.notify = notify
        self.position = None
        self.shares = 0
        self.entry_price = None
        self.in_cooldown = False
        self.trade_count = 0

    async def on_price_update(self, yes_price: float):
        if self.in_cooldown:
            logger.info(f"[{self.market_id}] Cooldown active, skipping tick.")
            return
        if self.position is None:
            await self._check_entry(yes_price)
        elif self.position == "YES":
            await self._check_exit_or_flip(yes_price)
        elif self.position == "NO":
            await self._check_no_exit(yes_price)

    async def _check_entry(self, price: float):
        if price >= ENTRY_THRESHOLD:
            size = self.pocket.calculate_size(price)
            if size < MIN_SHARES:
                logger.warning(f"[{self.market_id}] Order size {size} below minimum.")
                return
            await self._buy("YES", price, size)

    async def _check_exit_or_flip(self, price: float):
        if price >= TAKE_PROFIT:
            await self._close_position(price, reason="Take Profit")
            return
        if price <= FLIP_THRESHOLD:
            slippage = abs(price - FLIP_THRESHOLD) / FLIP_THRESHOLD
            if slippage > SLIPPAGE_MAX:
                logger.warning(f"[{self.market_id}] Slippage {slippage:.2%} exceeds max. Skipping flip.")
                return
            await self._sell("YES", price)
            size = self.pocket.calculate_size(1 - price)
            await self._buy("NO", 1 - price, size)
            await self._trigger_cooldown()

    async def _check_no_exit(self, price: float):
        no_price = 1 - price
        if no_price >= TAKE_PROFIT:
            await self._close_position(price, reason="NO Take Profit")

    async def _buy(self, side: str, price: float, size: float):
        logger.info(f"[{self.market_id}] BUY {side} | Price: {price:.2f} | Size: {size}")
        try:
            order = await self.clob.place_limit_order(
                market_id=self.market_id,
                side=side,
                price=price,
                size=size
            )
            self.position = side
            self.shares = size
            self.entry_price = price
            self.trade_count += 1
            if self.notify:
                await self.notify(
                    f"✅ Entry Executed\nSide: {side}\nPrice: {price:.2f}\nSize: {size}"
                )
        except Exception as e:
            logger.error(f"[{self.market_id}] Buy failed: {e}")

    async def _sell(self, side: str, price: float):
        logger.info(f"[{self.market_id}] SELL {side} | Price: {price:.2f} | Size: {self.shares}")
        try:
            await self.clob.place_limit_order(
                market_id=self.market_id,
                side=side,
                price=price,
                size=self.shares,
                is_sell=True
            )
            self.position = None
            self.shares = 0
            self.entry_price = None
        except Exception as e:
            logger.error(f"[{self.market_id}] Sell failed: {e}")

    async def _close_position(self, price: float, reason: str):
        logger.info(f"[{self.market_id}] Closing position — {reason}")
        await self._sell(self.position, price)
        if self.notify:
            await self.notify(f"🏁 Position Closed\nReason: {reason}\nPrice: {price:.2f}")

    async def _trigger_cooldown(self):
        logger.info(f"[{self.market_id}] Cooldown triggered for {COOLDOWN_SECONDS}s")
        self.in_cooldown = True
        if self.notify:
            await self.notify(f"🔄 Position Flipped\nMarket: {self.market_id}")
        await asyncio.sleep(COOLDOWN_SECONDS)
        self.in_cooldown = False
        logger.info(f"[{self.market_id}] Cooldown lifted.")

    def get_state(self):
        return {
            "market_id": self.market_id,
            "position": self.position,
            "shares": self.shares,
            "entry_price": self.entry_price,
            "in_cooldown": self.in_cooldown,
            "trade_count": self.trade_count
        }