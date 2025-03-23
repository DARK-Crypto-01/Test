import asyncio
import json
import time
import hashlib
import hmac
from websockets import connect

# Replace with your API credentials
API_KEY = "3d819e4c695b59cf051204dfe3e8a931"
API_SECRET = "4217aba9cdca23d475b2df22302b934274e022d7c555f192f2ea9cadd9f320a4"

async def test_order_amendment():
    url = "wss://api.gateio.ws/ws/v4/"
    
    async with connect(url) as websocket:
        # Generate authentication signature
        timestamp = int(time.time())
        body = json.dumps({"method": "server.login", "params": [API_KEY, "", timestamp]})
        signature_payload = f"{timestamp}\n{body}".encode()
        signature = hmac.new(API_SECRET.encode(), signature_payload, hashlib.sha512).hexdigest()

        # Login first
        login_payload = {
            "id": 1,
            "method": "server.login",
            "params": [API_KEY, signature, timestamp]
        }
        await websocket.send(json.dumps(login_payload))
        login_response = await websocket.recv()
        print("Login response:", login_response)

        # Send amendment for non-existent order
        amendment_payload = {
            "id": 12345,
            "method": "order.update",
            "params": [
                "invalid_order_123",  # Fake order ID
                "BTC_USDT",           # Trading pair
                {
                    "amount": "0.001", 
                    "price": "30000"
                }
            ]
        }
        await websocket.send(json.dumps(amendment_payload))
        
        # Capture response
        response = await websocket.recv()
        data = json.loads(response)
        
        if "error" in data:
            print("\nError received:")
            print(f"Code: {data['error']['code']}")
            print(f"Message: {data['error']['message']}")
        else:
            print("Unexpected response:", data)

if __name__ == "__main__":
    try:
        asyncio.run(test_order_amendment())
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        print("Test completed")
