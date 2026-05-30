import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
from config import TELEGRAM_TOKEN
from wallet.pocket import Pocket
from market.ingestion import MarketIngestion
from risk.controls import RiskControls
from strategy.swing_flip import SwingFlip
from settings import settings_manager

logger = logging.getLogger(__name__)


class PolyRobinBot:
    def __init__(self):
        self.pocket = Pocket()
        self.risk = RiskControls(self.pocket, notify=self.send_alert)
        self.ingestion = MarketIngestion(
            on_price_update=self._on_price_update
        )
        self.ingestion.set_client(self.pocket.get_client())
        self.app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        self.chat_id = None
        self._register_handlers()
        

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("trade", self.cmd_trade))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))

    def _main_keyboard(self):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Status", callback_data="status"),
                InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
            ],
            [
                InlineKeyboardButton("🔍 Auto ON", callback_data="auto_on"),
                InlineKeyboardButton("⏹ Auto OFF", callback_data="auto_off"),
            ],
            [
                InlineKeyboardButton("🚨 Kill Switch", callback_data="kill"),
                InlineKeyboardButton("✅ Reset", callback_data="reset"),
            ],
            [
                InlineKeyboardButton("💰 Claim Profits", callback_data="claim"),
            ]
        ])

    async def cmd_start(
        self, update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ):
        self.chat_id = update.effective_chat.id
        await self.pocket.refresh_balances()
        summary = self.pocket.get_summary()
        await update.message.reply_text(
            f"🤖 *Poly-Robin is live!*\n\n"
            f"💰 USDC: ${summary['usdc_balance']:.2f}\n"
            f"🔒 Reserved: ${summary['reserved']:.2f}\n"
            f"✅ Available: ${summary['available']:.2f}\n\n"
            f"Use the buttons below to control the bot.\n"
            f"Or use /trade [url] [entry] [flip] to set up a trade.",
            parse_mode="Markdown",
            reply_markup=self._main_keyboard()
        )

    def _configure_swing(self, swing, overrides=None):
        """Apply global settings and any manual overrides to a swing instance."""
        s = settings_manager.get_all()
        if overrides:
            s.update(overrides)
            
        swing.entry_threshold = float(s.get("entry", 70)) / 100
        swing.flip_threshold = float(s.get("flip", 60)) / 100
        swing.take_profit_price = float(s.get("tp", 95)) / 100
        swing.stop_loss_price = float(s.get("stopLoss", 45)) / 100
        swing.buy_buffer = float(s.get("buffer", 2)) / 100
        swing.num_shares = float(s.get("shares", 10))
        swing.cooldown_seconds = float(s.get("cooldown", 30))
        swing.auto_redeem = s.get("autoRedeem", True)

    async def cmd_trade(
        self, update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ):
        args = context.args
        if len(args) < 1:
            await update.message.reply_text(
                "Usage: /trade [url] [optional: entry] [optional: flip]\n"
                "Example: /trade https://polymarket.com/... 0.70 0.60"
            )
            return
        url = args[0]
        overrides = {}
        if len(args) >= 2: overrides["entry"] = float(args[1]) * 100
        if len(args) >= 3: overrides["flip"] = float(args[2]) * 100
        
        market_id, token_id = await self.ingestion.add_market_from_url(url)
        if not market_id:
            await update.message.reply_text("❌ Could not resolve market.")
            return
        if not self.risk.can_open_trade(market_id):
            await update.message.reply_text("⚠️ Trade blocked by risk controls.")
            return
            
        meta = self.ingestion.market_metadata.get(market_id, {})
        tick_size = meta.get("tick_size", 0.01)
        tokens = self.ingestion.active_markets.get(market_id, {})
        
        swing = SwingFlip(
            market_id=market_id,
            tokens=tokens,
            pocket=self.pocket,
            notify=self.send_alert,
            on_done=self.risk.close_trade_async,
            tick_size=tick_size,
            neg_risk=meta.get("neg_risk", False),
            window_end_ts=meta.get("window_end_ts"),
        )
        self._configure_swing(swing, overrides)
        self.risk.register_trade(market_id, swing)
        await update.message.reply_text(
            f"✅ Trade set up!\n"
            f"Market: {market_id}\n"
            f"Entry: {swing.entry_threshold:.2f} | Flip: {swing.flip_threshold:.2f}",
            reply_markup=self._main_keyboard()
        )

    async def cmd_status(
        self, update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ):
        await self._send_status(update.message.reply_text)

    async def handle_callback(
        self, update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "status":
            await self._send_status(query.message.reply_text)

        elif data == "auto_on":
            await self.ingestion.scan_all_crypto_markets()
            await query.message.reply_text(
                f"🔍 Auto scanner ON\n"
                f"Markets loaded: {len(self.ingestion.get_active_markets())}",
                reply_markup=self._main_keyboard()
            )

        elif data == "auto_off":
            self.ingestion.stop_feed()
            await query.message.reply_text(
                "⏹ Auto scanner OFF.",
                reply_markup=self._main_keyboard()
            )

        elif data == "kill":
            await self.risk.kill_all()
            await query.message.reply_text(
                "🚨 Kill switch activated. All positions closed.",
                reply_markup=self._main_keyboard()
            )

        elif data == "reset":
            self.risk.reset_kill_switch()
            await query.message.reply_text(
                "✅ Kill switch reset. Trading re-enabled.",
                reply_markup=self._main_keyboard()
            )

        elif data == "claim":
            redeemed = 0
            for market_id, swing in self.risk.active_trades.items():
                try:
                    await swing._redeem_profits()
                    redeemed += 1
                except Exception as e:
                    logger.error(f"Claim failed for {market_id}: {e}")
            await query.message.reply_text(
                f"💰 Profits claimed for {redeemed} market(s).",
                reply_markup=self._main_keyboard()
            )

        elif data == "refresh":
            await self.pocket.refresh_balances()
            summary = self.pocket.get_summary()
            await query.message.reply_text(
                f"🔄 *Balance Refreshed!*\n\n"
                f"💰 USDC: ${summary['usdc_balance']:.2f}\n"
                f"🔒 Reserved: ${summary['reserved']:.2f}\n"
                f"✅ Available: ${summary['available']:.2f}",
                parse_mode="Markdown",
                reply_markup=self._main_keyboard()
            )

    async def _send_status(self, reply_fn):
        status = self.risk.get_status()
        await self.pocket.refresh_balances()
        summary = self.pocket.get_summary()
        msg = (
            f"📊 *Poly-Robin Status*\n\n"
            f"💰 USDC: ${summary['usdc_balance']:.2f}\n"
            f"✅ Available: ${summary['available']:.2f}\n"
            f"📈 Active Trades: {status['active_trades']}/{status['max_trades']}\n"
            f"🔴 Kill Switch: {'ON' if status['killed'] else 'OFF'}\n\n"
        )
        for m in status["markets"]:
            state = m["state"]
            msg += (
                f"🔹 {m['market_id'][:20]}...\n"
                f"   Position: {state['position']}\n"
                f"   Shares: {state['shares']}\n"
                f"   Entry: {state['entry_price']}\n"
                f"   Cooldown: {state['in_cooldown']}\n\n"
            )
        await reply_fn(
            msg,
            parse_mode="Markdown",
            reply_markup=self._main_keyboard()
        )

    async def send_alert(self, message: str):
        if self.chat_id:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                reply_markup=self._main_keyboard()
            )

    async def _on_price_update(self, market_id: str, price: float):
        if market_id in self.risk.active_trades:
            swing = self.risk.active_trades[market_id]
            await swing.on_price_update(price)
        elif settings_manager.get("master", False) and market_id in self.ingestion.active_markets:
            if not self.risk.can_open_trade(market_id):
                return
            if market_id in self.risk.active_trades:
                return

            tokens = self.ingestion.active_markets.get(market_id)
            meta = self.ingestion.market_metadata.get(market_id, {})
            tick_size = meta.get("tick_size", 0.01)

            swing = SwingFlip(
                market_id=market_id,
                tokens=tokens,
                pocket=self.pocket,
                notify=self.send_alert,
                on_done=self.risk.close_trade_async,
                tick_size=tick_size,
                neg_risk=meta.get("neg_risk", False),
                window_end_ts=meta.get("window_end_ts"),
            )
            self._configure_swing(swing)
            self.risk.register_trade(market_id, swing)
            await swing.on_price_update(price)
        else:
            return

    def run(self):
        logger.info("Poly-Robin bot starting...")
        self.app.run_polling(poll_interval=1.0)
