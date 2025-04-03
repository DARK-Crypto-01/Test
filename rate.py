import asyncio
import websockets
import json
import time

async def count_messages(websocket, counter):
    try:
        while True:
            # Wait for each incoming message and count it
            message = await websocket.recv()
            counter[0] += 1
    except asyncio.CancelledError:
        # When the task is cancelled, exit gracefully
        pass
    except Exception as e:
        print("Error receiving message:", e)

async def measure_ticker_updates():
    url = "wss://api.gateio.ws/ws/v4/"
    counter = [0]  # Use a mutable type to allow modifications inside the task

    async with websockets.connect(url) as websocket:
        # Subscribe to the BTC_USDT ticker updates
        subscribe_message = {
            "time": int(time.time()),
            "channel": "spot.ticker",
            "event": "subscribe",
            "payload": ["BTC_USDT"]
        }
        await websocket.send(json.dumps(subscribe_message))
        print("Subscribed to BTC_USDT ticker updates on Gate.io")

        # Start the counting task
        counting_task = asyncio.create_task(count_messages(websocket, counter))
        
        # Let the counting task run for one minute
        await asyncio.sleep(60)
        
        # Cancel the counting task after one minute
        counting_task.cancel()
        try:
            await counting_task
        except asyncio.CancelledError:
            pass

        print(f"Total price update events received in one minute: {counter[0]}")

if __name__ == "__main__":
    # Use asyncio.run() to properly manage the event loop
    asyncio.run(measure_ticker_updates())
