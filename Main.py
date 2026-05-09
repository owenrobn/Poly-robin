import asyncio
import logging
from bot.telegram_bot import PolyRobinBot

# Logging setup
logging.basicConfig(
    level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
        logger = logging.getLogger(__name__)

        def main():
            logger.info("Starting Poly-Robin...")
                bot = PolyRobinBot()
                    bot.run()

                    if __name__ == "__main__":
                        main()
