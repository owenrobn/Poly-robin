import asyncio
import logging
from py_clob_client_v2.clob_types import OrderArgs
from py_clob_client_v2.order_builder.constants import BUY, SELL
from config import (
    ENTRY_THRESHOLD, FLIP_THRESHOLD, TAKE_PROFIT,
    COOLDOWN_SECONDS, SLIPPAGE_MAX, MIN_SHARES
)

logger = logging.getLogger(__name__)


class SwingFlip:
    def __init__(self, market_id, tokens, pocket, notify=None, on_done=None, tick_size=0.01, neg_risk=False):
        self.market_id = market_id
        self.yes_token = tokens.get("yes")
        self.no_token = tokens.get("no")
        self.pocket = pocket
        self.notify = notify
        self.on_done = on_done
        self.tick_size = tick_size
        self.neg_risk = neg_risk

        self.position = None
        self.shares = 0.0
        self.entry_price = None
        self.in_cooldown = False
        self.is_placing_order = False  
        self.trade_count = 0
        self.auto_redeem = True

        # Strategic Thresholds
        self.entry_threshold = float(ENTRY_THRESHOLD)
        self.flip_threshold = float(FLIP_THRESHOLD)
        self.take_profit_pct = 0.50  # 50% profit from entry (relative)
        self.stop_loss_pct = 0.20    # 20% loss from entry (relative)
        
        # Absolute targets (optional, overrides relative)
        self.take_profit_price = None
        self.stop_loss_price = None
        
        self.buy_buffer = 0.01       # 1 cent buffer
        self.sell_buffer = 0.01      # 1 cent buffer for aggressive exits
        self.num_shares = float(MIN_SHARES) 
        self.cooldown_seconds = float(COOLDOWN_SECONDS)
        self.min_shares = float(MIN_SHARES)

        self.entry_direction = None
        self.last_yes_price = None
        self.last_no_price = None
        
        # P&L Tracking
        self.total_pnl = 0.0
        self.last_pnl = 0.0
        self.last_cost = 0.0

    def _round_price(self, price: float) -> float:
        """Round price to the market's tick size."""
        if self.tick_size <= 0:
            return round(price, 2)
        # Polymarket prices are [0.01, 0.99]
        rounded = round(round(price / self.tick_size) * self.tick_size, 4)
        return max(0.01, min(0.99, rounded))

    def _round_shares(self, shares: float) -> float:
        """Round shares to 2 decimal places (standard for Polymarket)."""
        return round(float(shares), 2)

    async def force_entry(self, direction: str):
        """Forces an entry in a specific direction, bypassing threshold checks."""
        if self.position is not None or self.is_placing_order:
            return
            
        side = direction.upper()
        price = self.last_yes_price if side == "YES" else self.last_no_price
        if not price: price = 0.5 # Fallback
        
        buy_price = self._round_price(min(price + self.buy_buffer, 0.99))
        size = float(self.pocket.calculate_size(buy_price, self.num_shares))
        if size >= self.num_shares:
            logger.info(f"[{self.market_id}] Forced entry signal received: {side}")
            self.entry_direction = side
            await self._buy(side, buy_price, size)

    async def on_price_update(self, yes_price: float):
        """
        Processes price updates sent down by the server. Keeps your 
        complete automation mechanics monitoring for exits and flips alive.
        """
        if self.is_placing_order:
            logger.info(f"[{self.market_id}] Order placement in progress, skipping tick.")
            return

        no_price = self._round_price(1 - yes_price)
        self.last_yes_price = yes_price
        self.last_no_price = no_price
  
        # DEBUG: Log every price update during testing to see if strategy is active
        logger.debug(f"[{self.market_id}] Price Update: YES=${yes_price:.4f} | NO=${no_price:.4f}")

        if self.in_cooldown:
            return

        if self.position is None:
            logger.info(f"[{self.market_id}] Monitoring for Entry: YES=${yes_price:.2f} (Target: ${self.entry_threshold:.2f})")
            await self._check_entry(yes_price, no_price)
        elif self.position == "YES":
            await self._manage_yes_position(yes_price)
        elif self.position == "NO":
            await self._manage_no_position(no_price)

    async def _check_entry(self, yes_price: float, no_price: float):
        # Calculate dynamic bounds for safety
        def get_bounds(side, price):
            if side == "YES":
                tp = self.take_profit_price or (price + (price * self.take_profit_pct))
                sl = self.stop_loss_price or (price - (price * self.stop_loss_pct))
            else:
                tp = self.take_profit_price or (price + (price * self.take_profit_pct))
                sl = self.stop_loss_price or (price - (price * self.stop_loss_pct))
            return tp, sl

        if yes_price >= self.entry_threshold:
            tp, sl = get_bounds("YES", yes_price)
            buy_price = self._round_price(min(self.entry_threshold + self.buy_buffer, 0.99))
            if buy_price >= (tp - 0.02):
                logger.warning(f"[{self.market_id}] Skipping YES entry: Planned order {buy_price:.2f} too close to TP {tp:.2f}")
                return
            size = float(self.pocket.calculate_size(buy_price, self.num_shares))
            logger.info(f"[{self.market_id}] Threshold met for YES: {yes_price:.2f} >= {self.entry_threshold:.2f}. Placing limit at {buy_price:.2f}. Size: {size:.2f}/{self.num_shares:.2f}")
            if size >= self.num_shares:
                self.entry_direction = "YES"
                await self._buy("YES", buy_price, size)
        elif no_price >= self.entry_threshold:
            tp, sl = get_bounds("NO", no_price)
            buy_price = self._round_price(min(self.entry_threshold + self.buy_buffer, 0.99))
            if buy_price >= (tp - 0.02):
                logger.warning(f"[{self.market_id}] Skipping NO entry: Planned order {buy_price:.2f} too close to TP {tp:.2f}")
                return
            size = float(self.pocket.calculate_size(buy_price, self.num_shares))
            logger.info(f"[{self.market_id}] Threshold met for NO: {no_price:.2f} >= {self.entry_threshold:.2f}. Placing limit at {buy_price:.2f}. Size: {size:.2f}/{self.num_shares:.2f}")
            if size >= self.num_shares:
                self.entry_direction = "NO"
                await self._buy("NO", buy_price, size)

    async def _manage_yes_position(self, yes_price: float):
        # Determine targets
        if self.take_profit_price:
            tp_price = self.take_profit_price
        else:
            tp_price = self.entry_price + (self.entry_price * self.take_profit_pct) if self.entry_price else TAKE_PROFIT
            tp_price = min(tp_price, TAKE_PROFIT) 
        
        if self.stop_loss_price:
            sl_price = self.stop_loss_price
        else:
            sl_price = self.entry_price - (self.entry_price * self.stop_loss_pct) if self.entry_price else 0.45

        if yes_price >= tp_price:
            await self._close_position(yes_price, reason=f"Take Profit (Target: ${tp_price:.2f})")
            return

        if yes_price <= sl_price:
            await self._close_position(yes_price, reason=f"Stop Loss (Limit: ${sl_price:.2f})")
            return

        # Flip logic
        if yes_price <= self.flip_threshold:
            logger.info(f"[{self.market_id}] Flip triggered: YES price {yes_price:.2f} <= threshold {self.flip_threshold:.2f}")
            
            # Atomic flip attempt
            self.is_placing_order = True
            try:
                # Pre-check: Can we afford the flip?
                no_price = self._round_price(1 - yes_price)
                buy_price = self._round_price(min(no_price + self.buy_buffer, 0.99))

                available = max(0.0, float(self.pocket.usdc_balance - self.pocket.reserved))
                effective_available = available + (float(self.shares) * float(yes_price))
                size = min(float(self.num_shares), float(effective_available) / (float(buy_price) * 1.005)) if buy_price > 0 else 0.0

                if size < 5.0:
                    logger.warning(f"[{self.market_id}] Cannot flip: Insufficient effective balance for NO entry (Need 5.0, have {size:.2f}).")
                    return

                # 1. Exit current position
                success = await self._sell("YES", yes_price)
                if not success:
                    logger.error(f"[{self.market_id}] Flip failed: Could not exit YES position. Aborting flip.")
                    return

                # 2. Enter opposite position
                await self._buy("NO", buy_price, size)
                await self._trigger_cooldown()
            finally:
                self.is_placing_order = False

    async def _manage_no_position(self, no_price: float):
        # Determine targets
        if self.take_profit_price:
            tp_price = self.take_profit_price
        else:
            tp_price = self.entry_price + (self.entry_price * self.take_profit_pct) if self.entry_price else TAKE_PROFIT
            tp_price = min(tp_price, TAKE_PROFIT)
        
        if self.stop_loss_price:
            sl_price = self.stop_loss_price
        else:
            sl_price = self.entry_price - (self.entry_price * self.stop_loss_pct) if self.entry_price else 0.45

        if no_price >= tp_price:
            await self._close_position(no_price, reason=f"NO Take Profit (Target: ${tp_price:.2f})")
            return

        if no_price <= sl_price:
            await self._close_position(no_price, reason=f"NO Stop Loss (Limit: ${sl_price:.2f})")
            return

        # Flip logic
        if no_price <= self.flip_threshold:
            logger.info(f"[{self.market_id}] Flip triggered: NO price {no_price:.2f} <= threshold {self.flip_threshold:.2f}")

            self.is_placing_order = True
            try:
                # Pre-check: Can we afford the flip?
                yes_price = self._round_price(1 - no_price)
                buy_price = self._round_price(min(yes_price + self.buy_buffer, 0.99))

                available = max(0.0, float(self.pocket.usdc_balance - self.pocket.reserved))
                effective_available = available + (float(self.shares) * float(no_price))
                size = min(float(self.num_shares), float(effective_available) / (float(buy_price) * 1.005)) if buy_price > 0 else 0.0

                if size < 5.0:
                    logger.warning(f"[{self.market_id}] Cannot flip: Insufficient effective balance for YES entry (Need 5.0, have {size:.2f}).")
                    return

                # 1. Exit current position
                success = await self._sell("NO", no_price)
                if not success:
                    logger.error(f"[{self.market_id}] Flip failed: Could not exit NO position. Aborting flip.")
                    return

                # 2. Enter opposite position
                await self._buy("YES", buy_price, size)
                await self._trigger_cooldown()
            finally:
                self.is_placing_order = False

    async def _close_position(self, price: float, reason: str):
        logger.info(f"[{self.market_id}] Closing — {reason}")
        current_position = self.position
        if not current_position:
            logger.warning(f"[{self.market_id}] No position to close.")
            return

        success = await self._sell(current_position, price)
        
        if success:
            pnl_str = f"P&L: ${self.last_pnl:+.2f}"
            logger.info(f"[{self.market_id}] Trade Finished | {pnl_str} | Total: ${self.total_pnl:+.2f}")
            
            if self.notify:
                await self.notify(
                    f"🏁 Position Closed\n"
                    f"Reason: {reason}\n"
                    f"Side: {current_position}\n"
                    f"Price: {price:.2f}\n"
                    f"{pnl_str}"
                )
            
            if self.auto_redeem:
                await asyncio.sleep(2) # Wait for resolution
                await self._redeem_profits()

            if self.on_done:
                await self.on_done(self.market_id)
        else:
            logger.error(f"[{self.market_id}] Failed to close position via {reason}. State may be inconsistent.")

    async def _redeem_profits(self):
        try:
            client = self.pocket.get_client()
            if client:
                logger.info(f"[{self.market_id}] Attempting to redeem winnings...")
                # The market_id passed from ingestion is usually the condition_id or slug
                # V1 redeem expects condition_id
                resp = await asyncio.to_thread(client.redeem, self.market_id)
                logger.info(f"[{self.market_id}] Redemption response: {resp}")
        except Exception as e:
            logger.error(f"[{self.market_id}] Redemption failed: {e}")

    async def _buy(self, side: str, price: float, size: float) -> bool:
        token_id = self.yes_token if side == "YES" else self.no_token
        if not token_id:
            logger.error(f"[{self.market_id}] Cannot buy {side}: No token ID resolved.")
            return False

        size = self._round_shares(size)
        usd_amount = price * size
        logger.info(f"[{self.market_id}] 🚀 ATTEMPTING BUY: {side} | Price: {price:.4f} | Shares: {size:.2f} | Total: ${usd_amount:.2f}")

        # Reserve balance in pocket
        self.pocket.reserve(usd_amount)

        outer_placing_state = self.is_placing_order
        self.is_placing_order = True
        try:
            client_v2 = self.pocket.get_client_v2()
            if client_v2:
                from py_clob_client_v2.clob_types import PartialCreateOrderOptions, OrderType
                
                logger.info(f"[{self.market_id}] Sending signed order to Polymarket for {side}...")
                
                order_args = OrderArgs(
                    token_id=token_id,
                    price=float(price),
                    size=float(size),
                    side=BUY,
                )
                options = PartialCreateOrderOptions(neg_risk=self.neg_risk)
                
                signed_order = await asyncio.to_thread(client_v2.create_order, order_args, options)
                resp = await asyncio.to_thread(client_v2.post_order, signed_order, OrderType.GTC)
                
                if resp.get("success"):
                    order_id = resp.get("orderID") or resp.get("orderId") or resp.get("id")
                    if not order_id:
                        logger.error(f"[{self.market_id}] Buy accepted but missing order id: {resp}")
                        self.pocket.release(usd_amount)
                        return False

                    matched = 0.0
                    for _ in range(5):
                        order = await asyncio.to_thread(client_v2.get_order, order_id)
                        matched = float(order.get("size_matched", 0) or 0)
                        if matched > 0:
                            break
                        await asyncio.sleep(1.0)

                    try:
                        from py_clob_client_v2.clob_types import OrderPayload
                        await asyncio.to_thread(client_v2.cancel_order, OrderPayload(orderID=order_id))
                    except Exception:
                        pass

                    if matched <= 0:
                        self.pocket.release(usd_amount)
                        return False

                    filled_cost = float(price) * matched
                    if usd_amount > filled_cost:
                        self.pocket.release(usd_amount - filled_cost)

                    logger.info(f"[{self.market_id}] Buy filled: {order_id} | Shares: {matched:.2f} @ {price:.4f}")
                    self.position = side
                    self.shares = float(matched)
                    self.entry_price = float(price)
                    self.last_cost = float(filled_cost)
                    self.trade_count += 1
                    if self.notify:
                        await self.notify(
                            f"✅ Entry Executed\n"
                            f"Side: {side}\n"
                            f"Price: {price:.2f}\n"
                            f"Shares: {matched:.2f}\n"
                            f"Cost: ${filled_cost:.2f}"
                        )
                    return True
                else:
                    logger.error(f"[{self.market_id}] Buy Order failed: {resp.get('errorMsg')}")
                    self.pocket.release(usd_amount)
                    return False

            self.pocket.release(usd_amount)
            return False
        except Exception as e:
            logger.error(f"[{self.market_id}] Buy exception: {e}")
            self.pocket.release(usd_amount)
            self.position = None 
            return False
        finally:
            self.is_placing_order = outer_placing_state

    async def _sell(self, side: str, price: float) -> bool:
        token_id = self.yes_token if side == "YES" else self.no_token
        if not token_id:
            logger.error(f"[{self.market_id}] Cannot sell {side}: No token ID resolved.")
            return False

        # Verify actual balance before selling to prevent failures
        # Retry up to 3 times with small delay to account for CLOB lag after buy
        actual_shares = 0.0
        for i in range(3):
            actual_shares = await self.pocket.get_token_balance(token_id)
            if actual_shares >= 0.01:
                break
            if i < 2:
                logger.info(f"[{self.market_id}] Waiting for shares to reflect in wallet (Attempt {i+1})...")
                await asyncio.sleep(1.5)

        if actual_shares < 0.01:
            logger.warning(f"[{self.market_id}] No actual shares found for {side} in wallet. Syncing state.")
            self.position = None
            self.shares = 0.0
            self.entry_price = None
            return True # Consider it "closed" if we have nothing

        shares_to_sell = self._round_shares(actual_shares)
        
        # Use an aggressive sell price to ensure immediate execution (limit order that hits the book)
        aggressive_price = self._round_price(max(0.01, price - self.sell_buffer))
        
        logger.info(f"[{self.market_id}] SELL {side} | Price: {aggressive_price:.4f} (Market: {price:.4f}) | Shares: {shares_to_sell:.2f}")
        
        outer_placing_state = self.is_placing_order
        self.is_placing_order = True
        try:
            client_v2 = self.pocket.get_client_v2()
            if client_v2:
                from py_clob_client_v2.clob_types import PartialCreateOrderOptions, OrderType
                order_args = OrderArgs(
                    token_id=token_id,
                    price=float(aggressive_price),
                    size=float(shares_to_sell),
                    side=SELL,
                )
                options = PartialCreateOrderOptions(neg_risk=self.neg_risk)
                
                signed_order = await asyncio.to_thread(client_v2.create_order, order_args, options)
                order_type = getattr(OrderType, "IOC", OrderType.GTC)
                resp = await asyncio.to_thread(client_v2.post_order, signed_order, order_type)
                
                if resp.get("success"):
                    order_id = resp.get("orderID") or resp.get("orderId") or resp.get("id")
                    if not order_id:
                        logger.error(f"[{self.market_id}] Sell accepted but missing order id: {resp}")
                        return False

                    order = await asyncio.to_thread(client_v2.get_order, order_id)
                    matched = float(order.get("size_matched", 0) or 0)
                    if matched <= 0:
                        try:
                            from py_clob_client_v2.clob_types import OrderPayload
                            await asyncio.to_thread(client_v2.cancel_order, OrderPayload(orderID=order_id))
                        except Exception:
                            pass
                        return False
                    
                    # Calculate P&L
                    cost_per_share = (float(self.last_cost) / float(self.shares)) if self.shares else float(self.entry_price or aggressive_price)
                    cost_sold = cost_per_share * matched
                    proceeds = float(aggressive_price) * matched
                    pnl = proceeds - cost_sold
                    self.last_pnl = pnl
                    self.total_pnl += pnl
                    
                    # Release reservation
                    self.pocket.release(cost_sold)

                    remaining = max(0.0, float(actual_shares) - matched)
                    if remaining < 0.01:
                        self.position = None
                        self.shares = 0.0
                        self.entry_price = None
                        self.last_cost = 0.0
                        return True

                    self.shares = remaining
                    self.last_cost = cost_per_share * remaining
                    return False
                else:
                    logger.error(f"[{self.market_id}] Sell Order failed: {resp.get('errorMsg')}")
                    return False

            return False
        except Exception as e:
            logger.error(f"[{self.market_id}] Sell exception: {e}")
            return False
        finally:
            self.is_placing_order = outer_placing_state

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
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "buy_buffer": self.buy_buffer,
            "num_shares": self.num_shares,
            "last_yes_price": self.last_yes_price,
            "last_no_price": self.last_no_price,
            "last_pnl": self.last_pnl,
            "total_pnl": self.total_pnl,
        }
