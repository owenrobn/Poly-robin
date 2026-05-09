import os
import logging
import aiohttp
from config import WALLET_ADDRESS, MIN_POCKET_USDC, MIN_SHARES

logger = logging.getLogger(__name__)

USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
POL_MIN_BALANCE = 0.5


class Pocket:
    def __init__(self):
        self.usdc_balance = 0.0
        self.pol_balance = 0.0
        self.reserved = 0.0

    async def refresh_balances(self):
        self.usdc_balance = await self._get_usdc_balance()
        self.pol_balance = await self._get_pol_balance()
        logger.info(
            f"Balances refreshed — "
            f"USDC: {self.usdc_balance:.2f} | "
            f"POL: {self.pol_balance:.4f}"
        )

    async def _get_usdc_balance(self) -> float:
        url = (
            f"https://api.etherscan.io/v2/api"
            f"?chainid=137&module=account&action=tokenbalance"
            f"&contractaddress={USDC_CONTRACT}"
            f"&address={WALLET_ADDRESS}"
            f"&tag=latest&apikey={os.getenv('POLYGONSCAN_API_KEY')}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    data = await resp.json()
                    raw = int(data.get("result", 0))
                    return raw / 1_000_000
        except Exception as e:
            logger.error(f"USDC balance fetch failed: {e}")
            return 0.0

    async def _get_pol_balance(self) -> float:
        url = (
            f"https://api.etherscan.io/v2/api"
            f"?chainid=137&module=account&action=balance"
            f"&address={WALLET_ADDRESS}"
            f"&tag=latest&apikey={os.getenv('POLYGONSCAN_API_KEY')}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    data = await resp.json()
                    raw = int(data.get("result", 0))
                    return raw / 1e18
        except Exception as e:
            logger.error(f"POL balance fetch failed: {e}")
            return 0.0

    def is_ready(self) -> bool:
        available = self.usdc_balance - self.reserved
        if available < MIN_POCKET_USDC:
            logger.warning(
                f"Insufficient USDC: {available:.2f} "
                f"(min {MIN_POCKET_USDC})"
            )
            return False
        if self.pol_balance < POL_MIN_BALANCE:
            logger.warning(
                f"Low POL for gas: {self.pol_balance:.4f} "
                f"(min {POL_MIN_BALANCE})"
            )
            return False
        return True

    def calculate_size(self, price: float, fraction: float = 0.2) -> float:
        available = self.usdc_balance - self.reserved
        usdc_to_spend = available * fraction
        if price <= 0:
            return 0.0
        size = usdc_to_spend / price
        size = round(size, 2)
        if size < MIN_SHARES:
            logger.warning(
                f"Calculated size {size} below minimum {MIN_SHARES}."
            )
            return 0.0
        return size

    def reserve(self, amount: float):
        self.reserved += amount
        logger.info(
            f"Reserved {amount:.2f} USDC. "
            f"Total reserved: {self.reserved:.2f}"
        )

    def release(self, amount: float):
        self.reserved = max(0.0, self.reserved - amount)
        logger.info(
            f"Released {amount:.2f} USDC. "
            f"Total reserved: {self.reserved:.2f}"
        )

    def get_summary(self) -> dict:
        return {
            "usdc_balance": self.usdc_balance,
            "pol_balance": self.pol_balance,
            "reserved": self.reserved,
            "available": self.usdc_balance - self.reserved
        }