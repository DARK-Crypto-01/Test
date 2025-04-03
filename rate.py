import asyncio
import websockets
import json
import time

async def measure_ticker_updates():
    url = "wss://api.gateio.ws/ws/v4/"
    event_count = 0
    start_time = time.time()

    async with websockets.connect(url) as websocket:
        # Prepare the subscription message for the BTC/USDT ticker channel.
        subscribe_message = {
            "time": int(time.time()),
            "channel": "spot.ticker",
            "event": "subscribe",
            "payload": ["BTC_USDT"]
        }
        await websocket.send(json.dumps(subscribe_message))
        print("Subscribed to BTC_USDT ticker updates on Gate.io")

        # Loop until one minute has elapsed.
        while True:
            current_time = time.time()
            elapsed = current_time - start_time
            if elapsed >= 60:
                break

            try:
                # Use asyncio.wait_for to adjust the timeout based on remaining time.
                timeout = 60 - elapsed
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                data = json.loads(message)
                event_count += 1
            except asyncio.TimeoutError:
                # No more messages in the remaining time.
                break
            except Exception as e:
                print("Error:", e)
                break

    print(f"Total price update events received in one minute: {event_count}")

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(measure_ticker_updates())
