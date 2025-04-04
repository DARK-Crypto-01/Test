import asyncio
import json
import time
import websockets
from datetime import datetime

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
        print("Connected. Monitoring BTC/USDT for 60 seconds...")

        try:
            while time.time() - start_time < 60:
                message = await asyncio.wait_for(websocket.recv(), timeout=1)
                data = json.loads(message)
                
                # Handle subscription confirmation first
                if data.get('event') == 'subscribe':
                    print(f"Subscribed to channel: {data.get('channel')}")
                    continue
                
                # Handle actual price updates
                if data.get('channel') == 'spot.tickers' and 'result' in data:
                    event_count += 1
                    result = data['result']
                    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    
                    # Safely get price values with fallbacks
                    price = result.get('last', 'N/A')
                    change = result.get('change_percentage', 'N/A')
                    
                    print(f"[{timestamp}] Price: ${price} | 24h Δ: {change}%")

        except asyncio.TimeoutError:
            pass  # Expected when stopping

        finally:
            duration = time.time() - start_time
            print(f"\nTotal updates received in {duration:.1f} seconds: {event_count}")
            print(f"Average updates per second: {event_count/duration:.2f}")

if __name__ == "__main__":
    try:
        asyncio.run(track_btc_updates())
    except KeyboardInterrupt:
        print("\nMonitoring stopped early")
