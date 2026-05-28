import os
import logging
import time
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

logger = logging.getLogger(__name__)

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


class Pocket:
    def __init__(self):
        self.usdc_balance = 0.0
        self.reserved = 0.0
        self.client = None
        self.last_refresh = 0
        self._init_client()

    def _init_client(self):
        try:
            from config import PRIVATE_KEY, PROXY_WALLET, CLOB_API_KEY, CLOB_SECRET, CLOB_PASSPHRASE, CLOB_API_URL
            from py_clob_client.client import ClobClient, ApiCreds
            from py_clob_client_v2.client import ClobClient as ClobClientV2

            if not PRIVATE_KEY:
                logger.error("PRIVATE_KEY not set in .env")
                return
            
            creds = ApiCreds(
                api_key=CLOB_API_KEY,
                api_secret=CLOB_SECRET,
                api_passphrase=CLOB_PASSPHRASE
            )

            # V1 Client for general info
            self.client = ClobClient(
                CLOB_API_URL,
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
                signature_type=1,
                funder=PROXY_WALLET,
                creds=creds
            )
            
            # V2 Client for order execution
            self.client_v2 = ClobClientV2(
                CLOB_API_URL,
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
                creds=creds,
                signature_type=1,
                funder=PROXY_WALLET
            )
            
            logger.info("Polymarket CLOB clients (v1 & v2) initialized successfully")
        except Exception as e:
            logger.error(f"CLOB client init failed: {e}")

    async def refresh_balances(self, force=False):
        now = time.time()
        if not force and self.last_refresh > 0 and (now - self.last_refresh < 30):
            return
            
        try:
            balance = float(await self._get_balance())
            self.usdc_balance = balance
            self.last_refresh = now
            logger.info(f"Polymarket balance refreshed: ${self.usdc_balance:.6f}")
        except Exception as e:
            logger.error(f"Failed to refresh balance: {e}")

    async def _get_balance(self) -> float:
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL
            )
            
            result = self.client.get_balance_allowance(params=params)
            raw = int(result.get("balance", 0))
            balance = float(raw / 1_000_000)
            
            return balance
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
            return 0.0

    async def get_token_balance(self, token_id: str) -> float:
        """Fetch current share balance for a specific token ID."""
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id
            )
            result = self.client.get_balance_allowance(params=params)
            raw = int(result.get("balance", 0))
            # Conditional tokens use 6 decimals usually on Polymarket
            balance = float(raw / 1_000_000)
            return balance
        except Exception as e:
            logger.error(f"Token balance fetch failed for {token_id}: {e}")
            return 0.0

    def is_ready(self) -> bool:
        available = float(self.usdc_balance - self.reserved)
        if available < 1.0:
            logger.warning(f"Insufficient balance: ${available:.2f}")
            return False
        return True

    def calculate_size(self, price: float, shares: float = 10) -> float:
        """
        Calculate safe trade size based on available balance and minimum limits.
        """
        from config import MIN_SHARES
        available = float(self.usdc_balance - self.reserved)
        
        # Calculate max shares we can afford at this price
        if price <= 0:
            return 0.0
            
        # Add a small buffer for fees
        cost_per_share = price * 1.005 
        max_affordable = available / cost_per_share
        
        target_shares = min(float(shares), max_affordable)
        min_limit = float(MIN_SHARES)
        
        if target_shares < min_limit:
            logger.warning(f"Calculated size {target_shares:.2f} is below minimum {min_limit}")
            return 0.0
            
        return round(target_shares, 2)

    def reserve(self, amount: float):
        self.reserved += float(amount)
        logger.info(f"Reserved ${amount:.2f}. Total: ${self.reserved:.2f}")

    def release(self, amount: float):
        self.reserved = max(0.0, self.reserved - float(amount))
        logger.info(f"Released ${amount:.2f}. Total: ${self.reserved:.2f}")

    def get_summary(self) -> dict:
        available = self.usdc_balance - self.reserved
        return {
            "usdc_balance": self.usdc_balance,
            "reserved": self.reserved,
            "available": available
        }

    def get_client(self):
        return self.client

    def get_client_v2(self):
        return self.client_v2