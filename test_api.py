import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

load_dotenv()

pk = os.getenv("PRIVATE_KEY")
proxy = os.getenv("PROXY_WALLET")

client = ClobClient(
    "https://clob.polymarket.com",
    key=pk,
    chain_id=137,
    signature_type=1,
    funder=proxy
)
client.set_api_creds(client.create_or_derive_api_creds())

try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    balance = client.get_balance_allowance(params=params)
    print(f"Balance: {balance}")
except Exception as e:
    import traceback
    traceback.print_exc()