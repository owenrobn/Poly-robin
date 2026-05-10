import urllib.request
import json

req = urllib.request.Request(
    'https://gamma-api.polymarket.com/events?slug=bitcoin-above-on-may-10',
    headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://polymarket.com/',
        'Origin': 'https://polymarket.com'
    }
)
try:
    r = urllib.request.urlopen(req)
    data = json.loads(r.read().decode())
    print("SUCCESS")
    print(json.dumps(data[0].get("markets", [{}])[0], indent=2)[:1000])
except Exception as e:
    print(f"FAILED: {e}")