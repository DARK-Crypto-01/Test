import time
import json
import threading
import logging
import websocket
import hashlib
import hmac
import uuid

class GateIOWebSocketClient:
    def __init__(self, currency_pair, on_price_callback, on_order_callback, api_key, api_secret, config=None):
        if len(api_key) != 32 or len(api_secret) != 64:
            raise ValueError("Invalid API credentials format")
        self.api_key = api_key
        self.api_secret = api_secret
        self.currency_pair = currency_pair.replace('_', '')
        self.on_price_callback = on_price_callback
        self.on_order_callback = on_order_callback  # Global callback for order events
        self.ws_url = "wss://ws.gate.io/v4"
        self.ws = None
        self.thread = None
        self.price_lock = threading.Lock()
        self.current_price = None
        self.logger = logging.getLogger("GateIOWebSocketClient")
        self.logger.info(f"WebSocket client initialized for {self.currency_pair}")
        self.config = config if config else {}
        
        # New: Dictionary to track pending orders by a unique client_order_id
        self.pending_orders = {}
        self.pending_orders_lock = threading.Lock()

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
            
            elif channel == 'spot.orders':  # Fix typo: 'spot.order' → 'spot.orders'
                event = data.get('event')
                result = data.get('result', {})
                order_id = result.get('id')
                client_order_id = result.get('client_order_id')

                if event == 'order.created':
                    self.logger.info(f"Order created: {result}")
                    # Match to pending_orders via client_order_id
                    with self.pending_orders_lock:
                        callback = self.pending_orders.pop(client_order_id, None)
                    if callback:
                        callback(order_id, result)  # Pass real order_id to callback
                    else:
                        # Forward to global handler
                        if self.on_order_callback:
                            self.on_order_callback(order_id, result)
                elif event in ['order.update', 'order.canceled']:
                    # Forward all updates to global handler using exchange's order_id
                    if self.on_order_callback:
                        self.on_order_callback(order_id, result)
                        
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
            # Subscribe to tickers
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

            # Subscribe to order updates
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

    # Revised method for placing a stop-limit order via WebSocket
    def place_stop_limit_order_ws(self, order_type, trigger_price, limit_price, amount, callback=None):
        self.logger.info(f"Placing {order_type} stop-limit order via WebSocket with trigger: {trigger_price} and limit: {limit_price}")
        try:
            timestamp = int(time.time())
            # Generate a unique client_order_id to track this order
            client_order_id = str(uuid.uuid4())
            payload_str = f"channel=spot.order&event=create&time={timestamp}"
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                payload_str.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            order_msg = {
                "time": timestamp,
                "channel": "spot.order",
                "event": "create",
                "payload": [{
                    "client_order_id": client_order_id,
                    "symbol": self.currency_pair,
                    "type": "limit",
                    "side": order_type,
                    "price": limit_price,
                    "amount": amount,
                    "stopPrice": trigger_price,
                    "timeInForce": "IOC" if self.config.get('ioc', False) else "GTC",
                    "price_type": 1  # Adjust as needed: 1 for market trigger, 0 for index trigger
                }],
                "auth": {
                    "method": "api_key",
                    "KEY": self.api_key,
                    "SIGN": signature
                }
            }
            # Register the callback for this pending order (if provided)
            if callback:
                with self.pending_orders_lock:
                    self.pending_orders[client_order_id] = callback
            self.ws.send(json.dumps(order_msg))
            # Instead of a simulated response, we return a reference to the pending order
            return {"client_order_id": client_order_id, "status": "pending"}
        except Exception as e:
            self.logger.error(f"WebSocket order placement failed: {e}")
            return None

    # Revised method for placing a market order via WebSocket
    def place_market_order_ws(self, order_type, amount, callback=None):
        self.logger.info(f"Placing market {order_type} order via WebSocket for amount: {amount}")
        try:
            timestamp = int(time.time())
            client_order_id = str(uuid.uuid4())
            payload_str = f"channel=spot.order&event=create&time={timestamp}"
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                payload_str.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            order_msg = {
                "time": timestamp,
                "channel": "spot.order",
                "event": "create",
                "payload": [{
                    "client_order_id": client_order_id,
                    "symbol": self.currency_pair,
                    "type": "market",
                    "side": order_type,
                    "amount": amount
                }],
                "auth": {
                    "method": "api_key",
                    "KEY": self.api_key,
                    "SIGN": signature
                }
            }
            if callback:
                with self.pending_orders_lock:
                    self.pending_orders[client_order_id] = callback
            self.ws.send(json.dumps(order_msg))
            return {"client_order_id": client_order_id, "status": "pending"}
        except Exception as e:
            self.logger.error(f"WebSocket market order failed: {e}")
            return None
