import requests
import json

SERVER_URL = "http://127.0.0.1:8000/api/ping-trade"

def send_test_signal():
    payload = {
        "coin": "btc",
        "direction": "YES",
        "strategy": "swing_flip"
    }
    
    print(f"Sending test signal: {payload}")
    try:
        response = requests.post(SERVER_URL, json=payload)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"Error sending signal: {e}")

if __name__ == "__main__":
    send_test_signal()
