from py_clob_client.client import ClobClient
from config import PRIVATE_KEY
import logging

# Connect using your private key (Level 1 Auth)
client = ClobClient("https://clob.polymarket.com", chain_id=137, key=PRIVATE_KEY)

# This derives the L2 credentials needed for trading
creds = client.create_or_derive_api_creds()

print(f"CLOB_API_KEY={creds.api_key}")
print(f"CLOB_SECRET={creds.api_secret}")
print(f"CLOB_PASSPHRASE={creds.api_passphrase}")