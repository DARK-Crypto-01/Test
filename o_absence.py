import time
import json
import hmac
import hashlib
import websocket  # install via `pip install websocket-client`

# --- Configuration ---
API_KEY = "3d819e4c695b59cf051204dfe3e8a931"
API_SECRET = "4217aba9cdca23d475b2df22302b934274e022d7c555f192f2ea9cadd9f320a4".encode('utf-8')  # must be in bytes for HMAC
WS_URL = "wss://ws.gate.io/v4/"  # Using the API v4 WebSocket endpoint for spot

# --- Helper: Create signature ---
def create_signature(secret, channel, event, ts):
    message = f"channel={channel}&event={event}&time={ts}"
    signature = hmac.new(secret, message.encode('utf-8'), hashlib.sha512).hexdigest()
    return signature

# --- Build amend order request ---
def build_amend_request(order_id, new_price, new_size):
    ts = int(time.time() * 1000)  # current time in milliseconds
    channel = "order.amend"   # the method name for amending an order
    event = "api"           # using "api" event for request
    signature = create_signature(API_SECRET, channel, event, ts)
    
    # Assemble the request payload. (Note: The actual parameter names may vary;
    # this example follows the general format documented by Gate.io.)
    req = {
        "id": ts,  # using timestamp as request id
        "method": channel,
        "params": [
            order_id,          # the order ID we wish to amend (using a random value)
            {"price": new_price, "amount": new_size}  # new price and size
        ],
        "auth": {
            "method": "api_key",
            "KEY": API_KEY,
            "SIGN": signature
        },
        "time": ts
    }
    return req

def on_message(ws, message):
    print("Received:")
    print(json.dumps(json.loads(message), indent=2))
    # Close after receiving the response
    ws.close()

def on_error(ws, error):
    print("Error:", error)

def on_close(ws):
    print("Connection closed.")

def on_open(ws):
    # Use a random order id (here, a large number unlikely to exist)
    fake_order_id = 99999999999  
    new_price = "1.2345"  # arbitrary new price
    new_size = "100"      # arbitrary new size
    amend_req = build_amend_request(fake_order_id, new_price, new_size)
    print("Sending amend request:")
    print(json.dumps(amend_req, indent=2))
    ws.send(json.dumps(amend_req))

if __name__ == "__main__":
    ws = websocket.WebSocketApp(WS_URL,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.run_forever()
