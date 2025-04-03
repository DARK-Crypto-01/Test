import asyncio
import websockets
import json
import time

async def count_messages(websocket, counter):
    try:
        while True:
            message = await websocket.recv()
            data = json.loads(message)
            counter[0] += 1
            print("Received message:", data)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print("Error receiving message:", e)

async def measure_ticker_updates():
    url = "wss://api.gateio.ws/ws/v4/"
    counter = [0]  # mutable counter to track events

    async with websockets.connect(url) as websocket:
        # Use the "ticker" channel per the API v4 documentation
        subscribe_message = {
            "time": int(time.time()),
            "channel": "ticker",
            "event": "subscribe",
            "payload": ["BTC_USDT"]
        }
        await websocket.send(json.dumps(subscribe_message))
        print("Subscribed to BTC_USDT ticker updates on Gate.io")

        # Start a concurrent task to count messages
        counting_task = asyncio.create_task(count_messages(websocket, counter))
        
        # Wait for one minute while messages are received
        await asyncio.sleep(60)
        
        # Cancel the counting task after one minute and wait for cancellation to finish
        counting_task.cancel()
        try:
            await counting_task
        except asyncio.CancelledError:
            pass

        print(f"Total ticker update events received in one minute: {counter[0]}")

if __name__ == "__main__":
    asyncio.run(measure_ticker_updates())
