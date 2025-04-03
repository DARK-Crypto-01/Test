import asyncio
import websockets
import json
import time

async def subscribe_ticker():
    # Gate.io WebSocket endpoint for version 4 of the API
    url = "wss://api.gateio.ws/ws/v4/"
    
    async with websockets.connect(url) as websocket:
        # Create the subscription message. The "payload" is a list of trading pairs.
        subscribe_message = {
            "time": int(time.time()),
            "channel": "spot.ticker",
            "event": "subscribe",
            "payload": ["BTC_USDT"]
        }
        # Send the subscription request to the websocket
        await websocket.send(json.dumps(subscribe_message))
        print("Subscribed to BTC_USDT ticker updates on Gate.io")

        # Listen for incoming messages (price updates)
        while True:
            try:
                message = await websocket.recv()
                data = json.loads(message)
                print("Received update:", data)
            except Exception as e:
                print("Error:", e)
                break

# Start the asyncio event loop and run the subscription coroutine
if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(subscribe_ticker())
