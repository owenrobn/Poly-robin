import asyncio
import logging
import threading
from bot.telegram_bot import PolyRobinBot
from bot.webapp_server import start_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting Poly-Robin...")
    bot = PolyRobinBot()

    # Run FastAPI server in background thread
    server_thread = threading.Thread(
        target=start_server,
        args=(bot.pocket, bot.risk, bot.ingestion),
        daemon=True
    )
    server_thread.start()
    logger.info("Mini App server running on http://localhost:8000")

    # Run Telegram bot (blocking)
    bot.run()

if __name__ == "__main__":
    main()