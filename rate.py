import asyncio
import json
import time
import websockets

class PriceUpdateCounter:
    def __init__(self):
        self.event_count = 0
        self.start_time = time.time()

    async def handle_messages(self, websocket):
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    # Check if it's a ticker update message
                    if data.get('channel') == 'spot.tickers' and 'result' in data:
                        self.event_count += 1
                        # Optional: Uncomment to see individual updates
                        # print(f"Update received: {data['result']['last']}")
                        
                except json.JSONDecodeError:
                    print("Received non-JSON message:", message)
                except KeyError as e:
                    print(f"Unexpected message format: {e}")

        except websockets.ConnectionClosed as e:
            print(f"Connection closed: {e.code} - {e.reason}")

    async def print_stats(self):
        while True:
            await asyncio.sleep(60)
            print(f"\n[Stats] Events/min: {self.event_count} | Avg/s: {self.event_count/60:.2f}")
            self.event_count = 0
            self.start_time = time.time()

async def monitor_btc_updates():
    counter = PriceUpdateCounter()
    uri = "wss://api.gateio.ws/ws/v4/"
    
    subscribe_message = json.dumps({
        "time": int(time.time()),
        "channel": "spot.tickers",
        "event": "subscribe",
        "payload": ["BTC_USDT"]
    })

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20) as websocket:
                await websocket.send(subscribe_message)
                print("Connected successfully. Monitoring BTC/USDT updates...")
                
                # Run both tasks concurrently
                await asyncio.gather(
                    counter.handle_messages(websocket),
                    counter.print_stats()
                )
                
        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(monitor_btc_updates())
    except KeyboardInterrupt:
        print("\nMonitoring stopped gracefully")
