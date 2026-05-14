import os
from dotenv import load_dotenv

load_dotenv()

# Polymarket
CLOB_API_URL = "https://clob.polymarket.com"
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Strategy defaults
ENTRY_THRESHOLD = 0.70
FLIP_THRESHOLD = 0.60
TAKE_PROFIT = 0.95
COOLDOWN_SECONDS = 30
MAX_ACTIVE_TRADES = 3
SLIPPAGE_MAX = 0.02

# Pocket
MIN_POCKET_USDC = 1.0
MIN_SHARES = 5