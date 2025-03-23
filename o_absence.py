import asyncio
import json
import time
import hashlib
import hmac
from websockets import connect

API_KEY = "3d819e4c695b59cf051204dfe3e8a931"
API_SECRET = "4217aba9cdca23d475b2df22302b934274e022d7c555f192f2ea9cadd9f320a4"

async def test_order_amendment():
    url = "wss://api.gateio.ws/ws/v4/"
    
    async with connect(url) as websocket:
        # 1. Generate signature for authentication
        timestamp = int(time.time())
        body = json.dumps({"method": "server.login", "params": [API_KEY, "", timestamp]})
        signature_payload = f"{timestamp}\n{body}".encode()
        signature = hmac.new(API_SECRET.encode(), signature_payload, hashlib.sha512).hexdigest()

        # 2. Authenticate
        login_payload = {
            "id": 1,
            "method": "server.login",
            "params": [API_KEY, signature, timestamp]
        }
        await websocket.send(json.dumps(login_payload))
        login_response = await websocket.recv()
        print("Login response:", login_response)

        # 3. Send order amendment with CORRECT METHOD NAME
        amendment_payload = {
            "id": 12345,
            "method": "spot.order.update",  # <-- FIXED METHOD NAME (was "order.update")
            "params": [
                "invalid_order_123",  # Fake order ID
                "BTC_USDT",           # Valid trading pair
                {
                    "amount": "0.001", 
                    "price": "30000"
                }
            ]
        }
        await websocket.send(json.dumps(amendment_payload))
        
        # 4. Capture response
        response = await websocket.recv()
        data = json.loads(response)
        
        if "error" in data:
            print(f"\nError (Code {data['error']['code']}): {data['error']['message']}")
        else:
            print("Response:", data)

if __name__ == "__main__":
    asyncio.run(test_order_amendment())
