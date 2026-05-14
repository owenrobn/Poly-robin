import os
import logging
from py_clob_client.client import ClobClient

logger = logging.getLogger(__name__)

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


class Pocket:
    def __init__(self):
        self.usdc_balance = 0.0
        self.reserved = 0.0
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            private_key = os.getenv("PRIVATE_KEY")
            proxy_wallet = os.getenv("PROXY_WALLET")
            if not private_key:
                logger.error("PRIVATE_KEY not set in .env")
                return
            self.client = ClobClient(
                HOST,
                key=private_key,
                chain_id=CHAIN_ID,
                signature_type=1,
                funder=proxy_wallet
            )
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            logger.info("Polymarket CLOB client initialized successfully")
        except Exception as e:
            logger.error(f"CLOB client init failed: {e}")

    async def refresh_balances(self):
        self.usdc_balance = await self._get_balance()
        logger.info(f"Polymarket balance: ${self.usdc_balance:.2f}")

    async def _get_balance(self) -> float:
        try:
            if not self.client:
                return 0.0
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = self.client.get_balance_allowance(params=params)
            raw = int(result.get("balance", 0))
            balance = raw / 1_000_000
            logger.info(f"Raw balance: {raw} → ${balance:.4f}")
            return balance
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
        from config import MIN_SHARES
        if shares < MIN_SHARES:
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