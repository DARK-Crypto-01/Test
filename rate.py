import asyncio
import json
import time
import websockets

class PriceUpdateCounter:
    def __init__(self):
        self.event_count = 0
        self.start_time = time.time()

    async def handle_messages(self, websocket):
        async for message in websocket:
            data = json.loads(message)
            if 'result' in data and data['result']['channel'] == 'spot.tickers':
                self.event_count += 1

    async def print_stats(self):
        while True:
            await asyncio.sleep(60)
            duration = time.time() - self.start_time
            print(f"\nEvents received in last minute: {self.event_count}")
            print(f"Average events per second: {self.event_count/60:.2f}")
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

    async with websockets.connect(uri) as websocket:
        await websocket.send(subscribe_message)
        print("Connected to Gate.io WebSocket. Monitoring BTC/USDT updates...")
        
        # Start both tasks concurrently
        await asyncio.gather(
            counter.handle_messages(websocket),
            counter.print_stats()
        )

if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(monitor_btc_updates())
    except KeyboardInterrupt:
        print("\nMonitoring stopped")
