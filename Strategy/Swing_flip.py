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
        self.auto_redeem = True

        self.entry_threshold = ENTRY_THRESHOLD
        self.flip_threshold = FLIP_THRESHOLD
        self.take_profit = TAKE_PROFIT
        self.stop_loss = 0.45
        self.buy_buffer = 0.02
        self.num_shares = MIN_SHARES
        self.cooldown_seconds = COOLDOWN_SECONDS

        self.entry_direction = None
        self.last_yes_price = None
        self.last_no_price = None

    async def on_price_update(self, yes_price: float):
        no_price = round(1 - yes_price, 4)
        self.last_yes_price = yes_price
        self.last_no_price = no_price

        if self.in_cooldown:
            logger.info(f"[{self.market_id}] Cooldown active, skipping tick.")
            return

        if self.position is None:
            await self._check_entry(yes_price, no_price)
        elif self.position == "YES":
            await self._manage_yes_position(yes_price)
        elif self.position == "NO":
            await self._manage_no_position(no_price)

    async def _check_entry(self, yes_price: float, no_price: float):
        if yes_price >= self.entry_threshold:
            buy_price = min(yes_price + self.buy_buffer, 0.99)
            size = self.pocket.calculate_size(buy_price, self.num_shares)
            if size >= MIN_SHARES:
                self.entry_direction = "YES"
                await self._buy("YES", buy_price, size)
        elif no_price >= self.entry_threshold:
            buy_price = min(no_price + self.buy_buffer, 0.99)
            size = self.pocket.calculate_size(buy_price, self.num_shares)
            if size >= MIN_SHARES:
                self.entry_direction = "NO"
                await self._buy("NO", buy_price, size)

    async def _manage_yes_position(self, yes_price: float):
        if yes_price >= self.take_profit:
            await self._close_position(yes_price, reason="Take Profit")
            if self.auto_redeem:
                await self._redeem_profits()
            return
        if yes_price <= self.stop_loss:
            await self._close_position(yes_price, reason="Stop Loss")
            if self.auto_redeem:
                await self._redeem_profits()
            return
        if yes_price <= self.flip_threshold:
            slippage = abs(yes_price - self.flip_threshold) / self.flip_threshold
            if slippage > SLIPPAGE_MAX:
                logger.warning(f"[{self.market_id}] Slippage too high. Skipping flip.")
                return
            await self._sell("YES", yes_price)
            no_price = round(1 - yes_price, 4)
            buy_price = min(no_price + self.buy_buffer, 0.99)
            size = self.pocket.calculate_size(buy_price, self.num_shares)
            if size >= MIN_SHARES:
                await self._buy("NO", buy_price, size)
            await self._trigger_cooldown()

    async def _manage_no_position(self, no_price: float):
        yes_price = round(1 - no_price, 4)
        if no_price >= self.take_profit:
            await self._close_position(yes_price, reason="NO Take Profit")
            if self.auto_redeem:
                await self._redeem_profits()
            return
        if no_price <= self.stop_loss:
            await self._close_position(yes_price, reason="NO Stop Loss")
            if self.auto_redeem:
                await self._redeem_profits()
            return
        if no_price <= self.flip_threshold:
            slippage = abs(no_price - self.flip_threshold) / self.flip_threshold
            if slippage > SLIPPAGE_MAX:
                logger.warning(f"[{self.market_id}] Slippage too high. Skipping flip.")
                return
            await self._sell("NO", no_price)
            buy_price = min(yes_price + self.buy_buffer, 0.99)
            size = self.pocket.calculate_size(buy_price, self.num_shares)
            if size >= MIN_SHARES:
                await self._buy("YES", buy_price, size)
            await self._trigger_cooldown()

    async def _redeem_profits(self):
        try:
            if self.clob:
                logger.info(f"[{self.market_id}] Redeeming profits...")
                await self.clob.redeem_positions(market_id=self.market_id)
                if self.notify:
                    await self.notify(f"💰 Profits Redeemed\nMarket: {self.market_id}")
        except Exception as e:
            logger.error(f"[{self.market_id}] Redemption failed: {e}")

    async def _buy(self, side: str, price: float, size: float):
        logger.info(f"[{self.market_id}] BUY {side} | Price: {price:.2f} | Size: {size}")
        try:
            if self.clob:
                await self.clob.place_limit_order(
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
                    f"✅ Entry Executed\n"
                    f"Side: {side}\n"
                    f"Price: {price:.2f}\n"
                    f"Size: {size}\n"
                    f"Buffer: +{self.buy_buffer:.2f}"
                )
        except Exception as e:
            logger.error(f"[{self.market_id}] Buy failed: {e}")

    async def _sell(self, side: str, price: float):
        logger.info(f"[{self.market_id}] SELL {side} | Price: {price:.2f} | Size: {self.shares}")
        try:
            if self.clob:
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
        logger.info(f"[{self.market_id}] Closing — {reason}")
        await self._sell(self.position, price)
        if self.notify:
            await self.notify(
                f"🏁 Position Closed\n"
                f"Reason: {reason}\n"
                f"Price: {price:.2f}"
            )

    async def _trigger_cooldown(self):
        self.in_cooldown = True
        if self.notify:
            await self.notify(f"🔄 Position Flipped\nMarket: {self.market_id}")
        await asyncio.sleep(self.cooldown_seconds)
        self.in_cooldown = False
        logger.info(f"[{self.market_id}] Cooldown lifted.")

    def get_state(self):
        return {
            "market_id": self.market_id,
            "position": self.position,
            "shares": self.shares,
            "entry_price": self.entry_price,
            "entry_direction": self.entry_direction,
            "in_cooldown": self.in_cooldown,
            "trade_count": self.trade_count,
            "stop_loss": self.stop_loss,
            "buy_buffer": self.buy_buffer,
            "num_shares": self.num_shares,
            "last_yes_price": self.last_yes_price,
            "last_no_price": self.last_no_price,
        }