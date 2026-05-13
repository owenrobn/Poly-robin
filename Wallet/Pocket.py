import os
import logging
import aiohttp

logger = logging.getLogger(__name__)

CLOB_API_URL = "https://clob.polymarket.com"

class Pocket:
    def __init__(self):
        self.usdc_balance = 0.0
        self.reserved = 0.0
        self.api_key = os.getenv("POLY_API_KEY", "")

    async def refresh_balances(self):
        self.usdc_balance = await self._get_polymarket_balance()
        logger.info(f"Polymarket balance: ${self.usdc_balance:.2f}")

    async def _get_polymarket_balance(self) -> float:
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'POLY-API-KEY': self.api_key
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{CLOB_API_URL}/balance",
                    headers=headers
                ) as resp:
                    logger.info(f"Balance API status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        return float(data.get("balance", 0))
                    else:
                        raw = await resp.text()
                        logger.error(f"Balance error {resp.status}: {raw[:200]}")
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
        return 0.0

    def is_ready(self) -> bool:
        available = self.usdc_balance - self.reserved
        if available < 1.0:
            logger.warning(f"Insufficient balance: ${available:.2f}")
            return False
        return True

    def calculate_size(self, price: float, shares: int = 10) -> float:
        """Use fixed shares instead of percentage sizing."""
        if price <= 0:
            return 0.0
        from config import MIN_SHARES
        if shares < MIN_SHARES:
            logger.warning(f"Shares {shares} below minimum {MIN_SHARES}")
            return 0.0
        return float(shares)

    def reserve(self, amount: float):
        self.reserved += amount
        logger.info(f"Reserved ${amount:.2f}. Total: ${self.reserved:.2f}")

    def release(self, amount: float):
        self.reserved = max(0.0, self.reserved - amount)
        logger.info(f"Released ${amount:.2f}. Total: ${self.reserved:.2f}")

    def get_summary(self) -> dict:
        return {
            "usdc_balance": self.usdc_balance,
            "pol_balance": 0.0,
            "reserved": self.reserved,
            "available": self.usdc_balance - self.reserved
        }