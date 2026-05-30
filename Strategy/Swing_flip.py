import asyncio
import logging
import time
from py_clob_client_v2.clob_types import OrderArgs
from py_clob_client_v2.order_builder.constants import BUY, SELL
from config import (
    ENTRY_THRESHOLD, FLIP_THRESHOLD, TAKE_PROFIT,
    COOLDOWN_SECONDS, SLIPPAGE_MAX, MIN_SHARES
)

logger = logging.getLogger(__name__)

# #region debug-point single-trade-stall:setup
import json as _dbg_json, urllib.request as _dbg_ur, os as _dbg_os, time as _dbg_time
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


class SwingFlip:
    def __init__(self, market_id, tokens, pocket, notify=None, on_done=None, tick_size=0.01, neg_risk=False, window_end_ts=None):
        self.market_id = market_id
        self.yes_token = tokens.get("yes")
        self.no_token = tokens.get("no")
        self.pocket = pocket
        self.notify = notify
        self.on_done = on_done
        self.tick_size = tick_size
        self.neg_risk = neg_risk
        self.window_end_ts = float(window_end_ts) if window_end_ts else None

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
        self._prev_yes_price = None
        self._prev_no_price = None
        
        # P&L Tracking
        self.total_pnl = 0.0
        self.last_pnl = 0.0
        self.last_cost = 0.0
        self._pending_close = None
        self._pending_flip = None
        self._order_lock = asyncio.Lock()

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
        prev_yes = self._prev_yes_price
        prev_no = self._prev_no_price

        _dbg_emit(
            "B",
            "strategy/swing_flip.py:on_price_update",
            "[DEBUG] tick",
            {
                "market_id": self.market_id,
                "yes": float(yes_price),
                "no": float(no_price),
                "position": self.position,
                "shares": float(self.shares),
                "cooldown": bool(self.in_cooldown),
                "pending_close": bool(self._pending_close),
                "pending_flip": bool(self._pending_flip),
                "is_placing_order": bool(self.is_placing_order),
            },
        )
  
        # DEBUG: Log every price update during testing to see if strategy is active
        logger.debug(f"[{self.market_id}] Price Update: YES=${yes_price:.4f} | NO=${no_price:.4f}")

        if self._pending_close:
            await self._handle_pending_close()
            return

        if self._pending_flip:
            await self._handle_pending_flip()
            return

        if self.window_end_ts:
            now_ts = time.time()
            if self.position is None and now_ts >= (self.window_end_ts - 45.0):
                return
            if self.position is not None and now_ts >= (self.window_end_ts - 20.0) and not self._pending_close:
                price_for_close = yes_price if self.position == "YES" else no_price
                await self._close_position(price_for_close, reason="Window Expiry")
                return

        if self.position is None:
            if self.in_cooldown:
                self._prev_yes_price = yes_price
                self._prev_no_price = no_price
                return
            logger.info(f"[{self.market_id}] Monitoring for Entry: YES=${yes_price:.2f} (Target: ${self.entry_threshold:.2f})")
            await self._check_entry(yes_price, no_price, prev_yes, prev_no)
        elif self.position == "YES":
            await self._manage_yes_position(yes_price)
        elif self.position == "NO":
            await self._manage_no_position(no_price)

        self._prev_yes_price = yes_price
        self._prev_no_price = no_price

    async def _check_entry(self, yes_price: float, no_price: float, prev_yes: float, prev_no: float):
        # Calculate dynamic bounds for safety
        def get_bounds(side, price):
            if side == "YES":
                tp = self.take_profit_price or (price + (price * self.take_profit_pct))
                sl = self.stop_loss_price or (price - (price * self.stop_loss_pct))
            else:
                tp = self.take_profit_price or (price + (price * self.take_profit_pct))
                sl = self.stop_loss_price or (price - (price * self.stop_loss_pct))
            return tp, sl

        buy_price = self._round_price(min(self.entry_threshold + self.buy_buffer, 0.99))

        if yes_price >= self.entry_threshold:
            tp, sl = get_bounds("YES", yes_price)
            _dbg_emit(
                "E",
                "strategy/swing_flip.py:_check_entry",
                "[DEBUG] entry-check YES",
                {
                    "market_id": self.market_id,
                    "trigger_yes": float(yes_price),
                    "planned_buy": float(buy_price),
                    "tp": float(tp),
                    "sl": float(sl),
                },
            )
            if float(yes_price) > float(buy_price):
                logger.info(f"[{self.market_id}] Skipping YES entry: Market {yes_price:.2f} above cap {buy_price:.2f}")
                return
            if not (float(sl) < float(buy_price) < float(tp)):
                logger.warning(
                    f"[{self.market_id}] Skipping YES entry: SL/Entry/TP not ordered "
                    f"(SL {float(sl):.2f}, Entry {float(buy_price):.2f}, TP {float(tp):.2f})"
                )
                return
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
            _dbg_emit(
                "E",
                "strategy/swing_flip.py:_check_entry",
                "[DEBUG] entry-check NO",
                {
                    "market_id": self.market_id,
                    "trigger_no": float(no_price),
                    "planned_buy": float(buy_price),
                    "tp": float(tp),
                    "sl": float(sl),
                },
            )
            if float(no_price) > float(buy_price):
                logger.info(f"[{self.market_id}] Skipping NO entry: Market {no_price:.2f} above cap {buy_price:.2f}")
                return
            if not (float(sl) < float(buy_price) < float(tp)):
                logger.warning(
                    f"[{self.market_id}] Skipping NO entry: SL/Entry/TP not ordered "
                    f"(SL {float(sl):.2f}, Entry {float(buy_price):.2f}, TP {float(tp):.2f})"
                )
                return
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

        _dbg_emit(
            "B",
            "strategy/swing_flip.py:_manage_yes_position",
            "[DEBUG] manage YES",
            {
                "market_id": self.market_id,
                "yes": float(yes_price),
                "tp": float(tp_price),
                "sl": float(sl_price),
                "flip": float(self.flip_threshold),
                "cooldown": bool(self.in_cooldown),
            },
        )

        if yes_price >= tp_price:
            _dbg_emit("B", "strategy/swing_flip.py:_manage_yes_position", "[DEBUG] tp-trigger YES", {"market_id": self.market_id, "yes": float(yes_price), "tp": float(tp_price)})
            await self._close_position(yes_price, reason=f"Take Profit (Target: ${tp_price:.2f})")
            return

        if yes_price <= sl_price:
            _dbg_emit("B", "strategy/swing_flip.py:_manage_yes_position", "[DEBUG] sl-trigger YES", {"market_id": self.market_id, "yes": float(yes_price), "sl": float(sl_price)})
            await self._close_position(yes_price, reason=f"Stop Loss (Limit: ${sl_price:.2f})")
            return

        # Flip logic
        if (not self.in_cooldown) and yes_price <= self.flip_threshold:
            logger.info(f"[{self.market_id}] Flip triggered: YES price {yes_price:.2f} <= threshold {self.flip_threshold:.2f}")
            self._pending_flip = {
                "from": "YES",
                "to": "NO",
                "trigger_price": float(yes_price),
                "reason": "Flip",
            }
            await self._handle_pending_flip()
            return

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

        _dbg_emit(
            "B",
            "strategy/swing_flip.py:_manage_no_position",
            "[DEBUG] manage NO",
            {
                "market_id": self.market_id,
                "no": float(no_price),
                "tp": float(tp_price),
                "sl": float(sl_price),
                "flip": float(self.flip_threshold),
                "cooldown": bool(self.in_cooldown),
            },
        )

        if no_price >= tp_price:
            _dbg_emit("B", "strategy/swing_flip.py:_manage_no_position", "[DEBUG] tp-trigger NO", {"market_id": self.market_id, "no": float(no_price), "tp": float(tp_price)})
            await self._close_position(no_price, reason=f"NO Take Profit (Target: ${tp_price:.2f})")
            return

        if no_price <= sl_price:
            _dbg_emit("B", "strategy/swing_flip.py:_manage_no_position", "[DEBUG] sl-trigger NO", {"market_id": self.market_id, "no": float(no_price), "sl": float(sl_price)})
            await self._close_position(no_price, reason=f"NO Stop Loss (Limit: ${sl_price:.2f})")
            return

        # Flip logic
        if (not self.in_cooldown) and no_price <= self.flip_threshold:
            logger.info(f"[{self.market_id}] Flip triggered: NO price {no_price:.2f} <= threshold {self.flip_threshold:.2f}")
            self._pending_flip = {
                "from": "NO",
                "to": "YES",
                "trigger_price": float(no_price),
                "reason": "Flip",
            }
            await self._handle_pending_flip()
            return

    async def _close_position(self, price: float, reason: str):
        current_position = self.position
        if not current_position:
            logger.warning(f"[{self.market_id}] No position to close.")
            return
        self._pending_close = {
            "reason": reason,
            "requested_price": float(price),
            "side": current_position,
        }
        await self._handle_pending_close()

    async def _handle_pending_close(self):
        if not self._pending_close:
            return
        async with self._order_lock:
            if not self._pending_close:
                return
            side = self._pending_close.get("side")
            if not side or self.position is None:
                self._pending_close = None
                return

            if side == "YES":
                live_price = self.last_yes_price
            else:
                live_price = self.last_no_price

            price = float(live_price) if live_price is not None else float(self._pending_close.get("requested_price", 0.5))

            _dbg_emit(
                "B",
                "strategy/swing_flip.py:_handle_pending_close",
                "[DEBUG] close-attempt",
                {"market_id": self.market_id, "side": side, "reason": self._pending_close.get("reason"), "price": float(price)},
            )
            logger.info(f"[{self.market_id}] Closing — {self._pending_close.get('reason')}")
            success = await self._sell(side, price)
            _dbg_emit(
                "B",
                "strategy/swing_flip.py:_handle_pending_close",
                "[DEBUG] close-result",
                {"market_id": self.market_id, "side": side, "success": bool(success), "shares_state": float(self.shares)},
            )
            if not success:
                return

            reason = self._pending_close.get("reason", "Closed")
            self._pending_close = None

            pnl_str = f"P&L: ${self.last_pnl:+.2f}"
            logger.info(f"[{self.market_id}] Trade Finished | {pnl_str} | Total: ${self.total_pnl:+.2f}")

            if self.notify:
                await self.notify(
                    f"🏁 Position Closed\n"
                    f"Reason: {reason}\n"
                    f"Side: {side}\n"
                    f"Price: {price:.2f}\n"
                    f"{pnl_str}"
                )

            if self.on_done:
                await self.on_done(self.market_id)

            if self.auto_redeem:
                await asyncio.sleep(2)
                await self._redeem_profits()

    async def _handle_pending_flip(self):
        if not self._pending_flip:
            return
        async with self._order_lock:
            if not self._pending_flip:
                return

            from_side = self._pending_flip.get("from")
            to_side = self._pending_flip.get("to")
            if self.position != from_side:
                self._pending_flip = None
                return

            if from_side == "YES":
                live_from_price = self.last_yes_price
            else:
                live_from_price = self.last_no_price

            from_price = float(live_from_price) if live_from_price is not None else float(self._pending_flip.get("trigger_price", 0.5))

            opposite_price = self._round_price(1 - from_price)
            buy_price = self._round_price(min(opposite_price + self.buy_buffer, 0.99))

            available = max(0.0, float(self.pocket.usdc_balance - self.pocket.reserved))
            effective_available = available + (float(self.shares) * float(from_price))
            size = min(float(self.num_shares), float(effective_available) / (float(buy_price) * 1.005)) if buy_price > 0 else 0.0

            if size < float(self.min_shares):
                logger.warning(f"[{self.market_id}] Cannot flip: Insufficient effective balance for {to_side} entry (Need {self.min_shares:.1f}, have {size:.2f}).")
                self._pending_flip = None
                return

            self.is_placing_order = True
            try:
                success = await self._sell(from_side, from_price)
                if not success:
                    return
                ok = await self._buy(to_side, buy_price, size)
                if ok:
                    await self._trigger_cooldown()
                self._pending_flip = None
            finally:
                self.is_placing_order = False

    async def _redeem_profits(self):
        try:
            client = self.pocket.get_client()
            if client and hasattr(client, "redeem"):
                logger.info(f"[{self.market_id}] Attempting to redeem winnings...")
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
            other_token_id = self.no_token if side == "YES" else self.yes_token
            other_shares = 0.0
            if other_token_id:
                other_shares = await self.pocket.get_token_balance(other_token_id)

            _dbg_emit(
                "B",
                "strategy/swing_flip.py:_sell",
                "[DEBUG] sell-sync-no-shares",
                {"market_id": self.market_id, "side": side, "actual_shares": float(actual_shares), "other_shares": float(other_shares)},
            )

            if other_shares < 0.01:
                logger.warning(f"[{self.market_id}] No shares found in wallet for either side. Syncing state as closed.")
                self.position = None
                self.shares = 0.0
                self.entry_price = None
                return True

            logger.warning(f"[{self.market_id}] Shares not yet reflected for {side}. Will retry close on next tick.")
            return False

        shares_to_sell = self._round_shares(actual_shares)
        
        # Use an aggressive sell price to ensure immediate execution (limit order that hits the book)
        aggressive_price = self._round_price(max(0.01, price - self.sell_buffer))

        _dbg_emit(
            "B",
            "strategy/swing_flip.py:_sell",
            "[DEBUG] sell-attempt",
            {
                "market_id": self.market_id,
                "side": side,
                "shares_to_sell": float(shares_to_sell),
                "market_price": float(price),
                "limit_price": float(aggressive_price),
            },
        )
        
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
                    _dbg_emit(
                        "B",
                        "strategy/swing_flip.py:_sell",
                        "[DEBUG] sell-match",
                        {"market_id": self.market_id, "order_id": str(order_id), "matched": float(matched)},
                    )
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
                        _dbg_emit(
                            "B",
                            "strategy/swing_flip.py:_sell",
                            "[DEBUG] sell-closed",
                            {"market_id": self.market_id, "matched": float(matched), "pnl": float(self.last_pnl)},
                        )
                        return True

                    self.shares = remaining
                    self.last_cost = cost_per_share * remaining
                    _dbg_emit(
                        "B",
                        "strategy/swing_flip.py:_sell",
                        "[DEBUG] sell-partial",
                        {"market_id": self.market_id, "matched": float(matched), "remaining": float(remaining)},
                    )
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
