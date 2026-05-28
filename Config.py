import os
from dotenv import load_dotenv

load_dotenv()

# --- Polymarket ---
CLOB_API_URL = "https://clob.polymarket.com"

# Credentials (ensure these are in your .env file)
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CLOB_API_KEY = os.getenv("CLOB_API_KEY")
CLOB_SECRET = os.getenv("CLOB_SECRET")
CLOB_PASSPHRASE = os.getenv("CLOB_PASSPHRASE")
PROXY_WALLET = os.getenv("PROXY_WALLET")

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- Strategy defaults ---
ENTRY_THRESHOLD = 0.70
FLIP_THRESHOLD = 0.60
TAKE_PROFIT = 0.95
COOLDOWN_SECONDS = 30
MAX_ACTIVE_TRADES = 3
SLIPPAGE_MAX = 0.02

# --- Pocket ---
MIN_POCKET_USDC = 1.0
MIN_SHARES = 5