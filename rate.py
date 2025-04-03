import asyncio
import websockets
import json
import time

# Global counter to keep track of the number of events
event_count = 0

# Function to handle counting events
async def subscribe_ticker():
    global event_count
    url = "wss://api.gateio.ws/ws/v4/"
    
    async with websockets.connect(url) as websocket:
        # Create the subscription message for BTC_USDT ticker updates
        subscribe_message = {
            "time": int(time.time()),
            "channel": "spot.ticker",
            "event": "subscribe",
            "payload": ["BTC_USDT"]
        }
        
        # Send the subscription message to the websocket
        await websocket.send(json.dumps(subscribe_message))
        print("Subscribed to BTC_USDT ticker updates on Gate.io")

        # Start the loop to listen for messages
        while True:
            try:
                # Wait for a new message from the websocket
                message = await websocket.recv()
                data = json.loads(message)
                
                # Increment the counter each time a price update message is received
                event_count += 1
                
                # Print the received data (optional)
                # print("Received update:", data)
            except Exception as e:
                print("Error:", e)
                break

# Function to measure the event count over a 1-minute period
async def measure_event_count():
    global event_count
    # Run the subscribe_ticker function in the background
    asyncio.create_task(subscribe_ticker())
    
    while True:
        # Wait for 60 seconds
        await asyncio.sleep(60)
        
        # Print the number of events received in the last minute
        print(f"Number of events received in the last 1 minute: {event_count}")
        
        # Reset the event count for the next minute
        event_count = 0

# Start the asyncio event loop and run the measure_event_count coroutine
if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(measure_event_count())
