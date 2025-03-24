import time
import json
import logging
import hashlib
import hmac
import uuid

class GateIOWebSocketOrders:
    def __init__(self, ws_connection):
        self.ws_connection = ws_connection
        self.logger = logging.getLogger("GateIOWebSocketOrders")

    def place_stop_limit_order_ws(self, order_type, trigger_price, limit_price, amount, callback=None):
        self.logger.info(f"Placing {order_type} stop-limit order via WebSocket with trigger: {trigger_price} and limit: {limit_price}")
        try:
            timestamp = int(time.time())
            client_order_id = str(uuid.uuid4())
            payload_str = f"channel=spot.order&event=create&time={timestamp}"
            signature = hmac.new(
                self.ws_connection.api_secret.encode('utf-8'),
                payload_str.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            order_msg = {
                "time": timestamp,
                "channel": "spot.order",
                "event": "create",
                "payload": [{
                    "client_order_id": client_order_id,
                    "symbol": self.ws_connection.currency_pair,
                    "type": "limit",
                    "side": order_type,
                    "price": limit_price,
                    "amount": amount,
                    "stopPrice": trigger_price,
                    "timeInForce": "IOC" if order_type == 'buy' else "GTC",  # Hardcoded IOC for buys
                    "price_type": 1
                }],
                "auth": {
                    "method": "api_key",
                    "KEY": self.ws_connection.api_key,
                    "SIGN": signature
                }
            }
            if callback:
                with self.ws_connection.pending_orders_lock:
                    self.ws_connection.pending_orders[client_order_id] = callback
            self.ws_connection.ws.send(json.dumps(order_msg))
            return {"client_order_id": client_order_id, "status": "pending"}
        except Exception as e:
            self.logger.error(f"WebSocket order placement failed: {e}")
            return None

    def cancel_order_ws(self, order_id):
        self.logger.info(f"Cancelling order {order_id} via WebSocket")
        try:
            timestamp = int(time.time())
            payload_str = f"channel=spot.order&event=cancel&time={timestamp}"
            signature = hmac.new(
                self.ws_connection.api_secret.encode('utf-8'),
                payload_str.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
        
            cancel_msg = {
                "time": timestamp,
                "channel": "spot.order",
                "event": "cancel",
                "payload": [order_id],
                "auth": {
                    "method": "api_key",
                    "KEY": self.ws_connection.api_key,
                    "SIGN": signature
                }
            }
            self.ws_connection.ws.send(json.dumps(cancel_msg))
            self.logger.info(f"Cancellation message for order {order_id} sent successfully.")
            return True
        except Exception as e:
            self.logger.error(f"WebSocket order cancellation failed for order {order_id}: {e}")
            return False

    def place_market_order_ws(self, order_type, amount, callback=None):
        self.logger.info(f"Placing market {order_type} order via WebSocket for amount: {amount}")
        try:
            timestamp = int(time.time())
            client_order_id = str(uuid.uuid4())
            payload_str = f"channel=spot.order&event=create&time={timestamp}"
            signature = hmac.new(
                self.ws_connection.api_secret.encode('utf-8'),
                payload_str.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            order_msg = {
                "time": timestamp,
                "channel": "spot.order",
                "event": "create",
                "payload": [{
                    "client_order_id": client_order_id,
                    "symbol": self.ws_connection.currency_pair,
                    "type": "market",
                    "side": order_type,
                    "amount": amount
                }],
                "auth": {
                    "method": "api_key",
                    "KEY": self.ws_connection.api_key,
                    "SIGN": signature
                }
            }
            if callback:
                with self.ws_connection.pending_orders_lock:
                    self.ws_connection.pending_orders[client_order_id] = callback
            self.ws_connection.ws.send(json.dumps(order_msg))
            return {"client_order_id": client_order_id, "status": "pending"}
        except Exception as e:
            self.logger.error(f"WebSocket market order failed: {e}")
            return None

    def amend_order_ws(self, client_order_id, new_trigger, new_limit, new_amount=None):
        self.logger.info(f"Amending order {client_order_id} with new trigger: {new_trigger}, limit: {new_limit}, amount: {new_amount}")
        try:
            timestamp = int(time.time())
            payload = {
                "client_order_id": client_order_id,
                "stopPrice": new_trigger,
                "price": new_limit,
            }
            if new_amount is not None:
                payload["amount"] = new_amount

            payload_str = f"channel=spot.order&event=amend&time={timestamp}"
            signature = hmac.new(
                self.ws_connection.api_secret.encode('utf-8'),
                payload_str.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            amend_msg = {
                "time": timestamp,
                "channel": "spot.order",
                "event": "amend",
                "payload": [payload],
                "auth": {
                    "method": "api_key",
                    "KEY": self.ws_connection.api_key,
                    "SIGN": signature
                }
            }
            self.ws_connection.ws.send(json.dumps(amend_msg))
            return {"client_order_id": client_order_id, "status": "amend_pending"}
        except Exception as e:
            self.logger.error(f"WebSocket order amendment failed: {e}")
            return None
