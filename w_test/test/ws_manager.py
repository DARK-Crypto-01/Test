import math
import logging
from gateio_websocket import GateIOWebSocketClient

class WSManager:
    def __init__(self, currency_pair, on_price_callback, on_order_callback, api_key, api_secret, config, total_instances, max_instances_per_ws):
        self.currency_pair = currency_pair
        self.on_price_callback = on_price_callback
        self.on_order_callback = on_order_callback
        self.api_key = api_key
        self.api_secret = api_secret
        self.config = config
        self.max_instances_per_ws = max_instances_per_ws
        self.total_instances = total_instances
        self.ws_clients = []
        self.logger = logging.getLogger("WSManager")
        self._create_ws_clients()

    def _create_ws_clients(self):
        num_clients = math.ceil(self.total_instances / self.max_instances_per_ws)
        self.logger.info(f"Creating {num_clients} WebSocket client(s) for {self.total_instances} instance(s) (max per connection: {self.max_instances_per_ws})")
        for i in range(num_clients):
            ws_client = GateIOWebSocketClient(
                currency_pair=self.currency_pair,
                on_price_callback=self.on_price_callback,
                on_order_callback=self.on_order_callback,
                api_key=self.api_key,
                api_secret=self.api_secret,
                config=self.config
            )
            ws_client.start()
            self.ws_clients.append(ws_client)

    def get_ws_client(self, instance_index=None):
        """
        For parallel orders, returns the ws client based on the instance index.
        For non-parallel orders (instance_index is None), returns the first ws client.
        """
        if instance_index is None:
            return self.ws_clients[0]
        client_index = instance_index // self.max_instances_per_ws
        if client_index >= len(self.ws_clients):
            client_index = len(self.ws_clients) - 1
        return self.ws_clients[client_index]
