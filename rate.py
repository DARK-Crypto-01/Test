import asyncio
import json
import time
import websockets

async def track_btc_updates():
    uri = "wss://api.gateio.ws/ws/v4/"
    event_count = 0
    start_time = time.time()

    subscribe_message = json.dumps({
        "time": int(time.time()),
        "channel": "spot.tickers",
        "event": "subscribe",
        "payload": ["BTC_USDT"]
    })

    async with websockets.connect(uri) as websocket:
        await websocket.send(subscribe_message)
        print("Monitoring BTC/USDT price updates for 60 seconds...")

        # Create exit timer
        exit_task = asyncio.create_task(asyncio.sleep(60))
        
        while True:
            try:
                # Check if time expired
                if exit_task.done():
                    break
                
                message = await asyncio.wait_for(websocket.recv(), timeout=1)
                data = json.loads(message)

                # Process only ticker updates
                if data.get('channel') == 'spot.tickers' and 'result' in data:
                    event_count += 1
                    price = data['result'].get('last', 'Price unavailable')
                    print(f"Update {event_count}: ${price}")

            except (asyncio.TimeoutError, KeyError):
                continue

    duration = time.time() - start_time
    print(f"\nTotal updates received in {duration:.1f} seconds: {event_count}")
    print(f"Average updates per second: {event_count/duration:.2f}")

if __name__ == "__main__":
    try:
        asyncio.run(track_btc_updates())
    except KeyboardInterrupt:
        print("\nMonitoring stopped early")
