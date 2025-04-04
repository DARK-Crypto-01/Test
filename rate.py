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
                data = json.loads(message)
                if 'result' in data and data['result']['channel'] == 'spot.tickers':
                    self.event_count += 1
        except websockets.ConnectionClosed:
            print("Connection closed by server. Reconnecting...")
            await self.reconnect()

    async def print_stats(self):
        while True:
            await asyncio.sleep(60)
            duration = time.time() - self.start_time
            print(f"\nEvents received in last minute: {self.event_count}")
            print(f"Average events per second: {self.event_count/60:.2f}")
            self.event_count = 0
            self.start_time = time.time()

    async def reconnect(self):
        await asyncio.sleep(5)  # Wait before reconnecting
        await monitor_btc_updates()

async def monitor_btc_updates():
    counter = PriceUpdateCounter()
    uri = "wss://api.gateio.ws/ws/v4/"
    
    subscribe_message = json.dumps({
        "time": int(time.time()),
        "channel": "spot.tickers",
        "event": "subscribe",
        "payload": ["BTC_USDT"]
    })

    try:
        async with websockets.connect(uri, ping_interval=30) as websocket:
            await websocket.send(subscribe_message)
            print("Connected to Gate.io WebSocket. Monitoring BTC/USDT updates...")
            
            # Create separate tasks
            handler = asyncio.create_task(counter.handle_messages(websocket))
            stats = asyncio.create_task(counter.print_stats())
            
            await asyncio.gather(handler, stats)
            
    except Exception as e:
        print(f"Connection error: {e}")
        await counter.reconnect()

if __name__ == "__main__":
    try:
        asyncio.run(monitor_btc_updates())
    except KeyboardInterrupt:
        print("\nMonitoring stopped gracefully")
