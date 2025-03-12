import math
import logging

class TradingStrategy:
    def __init__(self, config):
        self.config = config
        self.api = GateIOAPIClient(config)
        self.state = OrderState()
        self.logger = logging.getLogger("TradingStrategy")
        self.current_price = None

        # Initialize WebSocket client as before
        self.ws_client = GateIOWebSocketClient(
            currency_pair=self.config['trading']['currency_pair'],
            on_price_callback=self.update_price,
            api_key=self.config['api']['key'],
            api_secret=self.config['api']['secret']
        )
        self.ws_client.start()

        self.logger.info("WebSocket client started. Fetching initial market price...")
        self.current_price = self._fetch_initial_price()
        self.logger.info(f"Initial market price: {self.current_price}")

        # Fetch market details once and determine price precision
        market = self.api.exchange.market(self.api.symbol)
        if 'price' in market['precision']:
            price_precision = market['precision']['price']
        else:
            # Use fallback value from config.yaml if provided, else raise error.
            fallback = self.config['trading'].get('fallback_price_precision')
            if fallback is None:
                raise ValueError("Price precision not provided by exchange and no fallback configured.")
            price_precision = fallback

        # Calculate and store tick size using the determined precision.
        self.tick_size = 10 ** (-price_precision)
        self.logger.info(f"Determined tick size: {self.tick_size} (precision: {price_precision} digits)")

        # Instantiate OrderManager to handle order operations
        self.order_manager = OrderManager(self.api, self.state, self.config)
    
    # ... other methods remain unchanged

    def _calculate_prices(self, last_price, order_type):
        self.logger.debug(f"Calculating prices for {order_type} order with last price: {last_price}")
        tick_size = self.tick_size  # use the stored tick size
        
        if order_type == 'buy':
            trigger_adjust = self.config['trading']['buy']['trigger_price_adjust']
            limit_adjust = self.config['trading']['buy']['limit_price_adjust']
            trigger = last_price + (trigger_adjust * tick_size)
            limit = last_price + (limit_adjust * tick_size)
        elif order_type == 'sell':
            trigger_adjust = self.config['trading']['sell']['trigger_price_adjust']
            limit_adjust = self.config['trading']['sell']['limit_price_adjust']
            trigger = last_price - (trigger_adjust * tick_size)
            limit = last_price - (limit_adjust * tick_size)
        else:
            raise ValueError("Invalid order type specified")
        
        # Rounding using the determined precision.
        # Compute the number of decimal places from tick_size:
        decimal_places = -int(math.log10(tick_size))
        trigger = round(trigger, decimal_places)
        limit = round(limit, decimal_places)
        self.logger.debug(f"Calculated trigger: {trigger}, limit: {limit}")
        return trigger, limit
