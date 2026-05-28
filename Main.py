import asyncio
import logging
import threading

# Import configurations
from bot.telegram_bot import PolyRobinBot
from bot.webapp_server import start_server

# ==========================================
#          LOGGING CONFIGURATION
# ==========================================
# 1. Set root logger to WARNING to suppress third-party raw WebSocket/API tick floods
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S"
)

# 2. Force your local modules to show vital operational info (INFO level)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 3. Explicitly allow your custom bot architectures to print trades and states
logging.getLogger("bot").setLevel(logging.INFO)
logging.getLogger("strategy").setLevel(logging.INFO)
logging.getLogger("wallet").setLevel(logging.INFO)
logging.getLogger("market").setLevel(logging.INFO)

# Optional: Directly isolate and quiet third-party libraries if they still bypass root
logging.getLogger("py_clob_client").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
async def startup(bot):
    """Run async startup tasks before the bot polling loop begins."""
    await bot.pocket.refresh_balances()
    
    # 1. Scan markets
    await bot.ingestion.scan_all_crypto_markets()
    
    # 2. Launch background tasks
    logger.info("Initializing background feed streams and market expiration watchdogs...")
    asyncio.create_task(bot.ingestion.watch_market_expiry())
    asyncio.create_task(bot.ingestion.start_feed()) 
    
    logger.info("🚀 Poly-Robin Ingestion Pipeline is fully operational and tracking live ticks!")

def run_headless(bot):
    async def runner():
        await startup(bot)
        while True:
            await asyncio.sleep(3600)

    asyncio.run(runner())

def main():
    logger.info("Starting Poly-Robin...")
    
    bot = PolyRobinBot()
    clob_client = bot.pocket.get_client()

    if not clob_client:
        logger.error("❌ Failed to initialize CLOB Client.")
        return

    server_thread = threading.Thread(
        target=start_server,
        args=(bot.pocket, bot.risk, bot.ingestion),
        daemon=False
    )
    server_thread.start()
    logger.info("Mini App server running on http://localhost:8000")

    async def post_init(application):
        asyncio.create_task(startup(bot))

    try:
        bot.app.post_init = post_init
        bot.run()
    except BaseException as e:
        logger.error(f"Telegram bot failed ({type(e).__name__}: {e}). Running headless UI mode.")
        run_headless(bot)

if __name__ == "__main__":
    main()
