import time
import json
import threading
import logging
import websocket
import hashlib
import hmac

class GateIOWebSocketClient:
    def __init__(self, currency_pair, on_price_callback, on_order_callback, api_key, api_secret):
        if len(api_key) != 32 or len(api_secret) != 64:
            raise ValueError("Invalid API credentials format")
        self.api_key = api_key
        self.api_secret = api_secret
        # Remove underscores from currency pair (e.g. BTC_USDT -> BTCUSDT)
        self.currency_pair = currency_pair.replace('_', '')
        self.on_price_callback = on_price_callback
        self.on_order_callback = on_order_callback  # Callback for order events
        self.ws_url = "wss://ws.gate.io/v4"
        self.ws = None
        self.thread = None
        self.price_lock = threading.Lock()
        self.current_price = None
        self.logger = logging.getLogger("GateIOWebSocketClient")
        self.logger.info(f"WebSocket client initialized for {self.currency_pair}")

    def on_message(self, ws, message):
        self.logger.debug(f"Received message: {message}")
        try:
            data = json.loads(message)
            channel = data.get('channel')
            event = data.get('event')
            if channel == 'spot.tickers' and event == 'update':
                result = data.get('result', {})
                last_price = result.get('last')
                if not last_price:
                    self.logger.debug("No last price found in the message.")
                    return
                try:
                    price = float(last_price)
                    with self.price_lock:
                        self.current_price = price
                    self.logger.debug(f"Updated current price: {price}")
                    self.on_price_callback(price)
                except (ValueError, TypeError) as e:
                    self.logger.error(f"Price parse error: {e}")
            elif channel == 'spot.orders' and event == 'update':
                result = data.get('result', {})
                order_id = result.get('order_id')
                status = result.get('status')
                self.logger.debug(f"Order update received: Order ID: {order_id}, Status: {status}")
                if status in ["closed", "filled"]:
                    self.logger.info(f"Order {order_id} executed with status: {status}")
                    if self.on_order_callback:
                        self.on_order_callback(result)
            else:
                self.logger.debug("Message ignored: not a subscribed event update.")
        except Exception as e:
            self.logger.error(f"Message processing failed: {e}")

    def on_error(self, ws, error):
        self.logger.error(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")

    def on_open(self, ws):
        self.logger.info("WebSocket connection opened, sending subscription messages.")
        try:
            timestamp = int(time.time())
            ticker_payload = f"channel=spot.tickers&event=subscribe&time={timestamp}"
            ticker_signature = hmac.new(
                self.api_secret.encode('utf-8'),
                ticker_payload.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            ticker_sub_msg = {
                "time": timestamp,
                "channel": "spot.tickers",
                "event": "subscribe",
                "payload": [self.currency_pair],
                "auth": {
                    "method": "api_key",
                    "KEY": self.api_key,
                    "SIGN": ticker_signature
                }
            }
            ws.send(json.dumps(ticker_sub_msg))
            self.logger.info("Ticker subscription message sent.")

            order_payload = f"channel=spot.orders&event=subscribe&time={timestamp}"
            order_signature = hmac.new(
                self.api_secret.encode('utf-8'),
                order_payload.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            order_sub_msg = {
                "time": timestamp,
                "channel": "spot.orders",
                "event": "subscribe",
                "payload": [],
                "auth": {
                    "method": "api_key",
                    "KEY": self.api_key,
                    "SIGN": order_signature
                }
            }
            ws.send(json.dumps(order_sub_msg))
            self.logger.info("Order subscription message sent.")
        except Exception as e:
            self.logger.error(f"Subscription failed: {str(e)}")

    def run(self):
        self.logger.info("Starting WebSocket run loop.")
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        self.ws.run_forever(
            ping_interval=30,
            ping_timeout=10,
            ping_payload="keepalive"
        )

    def update_price(self, price):
        with self.price_lock:
            self.current_price = price
        self.logger.debug(f"Price manually updated to: {price}")

    def start(self):
        self.logger.info("Starting WebSocket thread...")
        def run_forever():
            retry_count = 0
            while True:
                try:
                    self.logger.info("Establishing WebSocket connection...")
                    self.run()
                    retry_count = 0
                except Exception as e:
                    retry_count += 1
                    timeout = min(2 ** retry_count, 30)
                    self.logger.error(f"WebSocket connection error. Reconnecting in {timeout}s: {e}")
                    time.sleep(timeout)
        self.thread = threading.Thread(target=run_forever, daemon=True)
        self.thread.start()
