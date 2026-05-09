import os
from dotenv import load_dotenv

load_dotenv()

# Wallet
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

# Polymarket CLOB
CLOB_API_URL = "https://clob.polymarket.com"
POLY_API_KEY = os.getenv("POLY_API_KEY")
CHAIN_ID = 137  # Polygon mainnet

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Strategy defaults
ENTRY_THRESHOLD = 0.70      # Buy YES at 70¢
FLIP_THRESHOLD = 0.60       # Flip to NO at 60¢
TAKE_PROFIT = 0.95          # Exit at 95¢
COOLDOWN_SECONDS = 30       # Whipsaw protection
MAX_ACTIVE_TRADES = 3       # Max simultaneous positions
SLIPPAGE_MAX = 0.02         # 2% max slippage

# Pocket
MIN_POCKET_USDC = 50.0      # Minimum wallet balance to trade
MIN_SHARES = 5              # Polymarket CLOB minimum
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY")