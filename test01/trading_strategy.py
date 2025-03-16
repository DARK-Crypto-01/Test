import time
import math
import logging
from gateio_api import GateIOAPIClient
from gateio_websocket import GateIOWebSocketClient
from order_manager import OrderManager
from parallel_order_manager import ParallelOrderManager  # New module for parallel order handling

class OrderState:
    def __init__(self):
        # For parallel orders, active_orders stores per-instance order details (e.g. order_id, last_price)
        self.active_orders = {}   # key: instance_index, value: dict with order details
        self.order_type = 'buy'   # Starting order type
        self.last_buy_amount = None  # To store executed buy amount (for sell orders)

class TradingStrategy:
    def __init__(self, config):
        self.config = config
        self.api = GateIOAPIClient(config)
        self.state = OrderState()
        self.logger = logging.getLogger("TradingStrategy")
        self.current_price = None

        # Initialize WebSocket client with callbacks for price and order events
        self.ws_client = GateIOWebSocketClient(
            currency_pair=self.config['trading']['currency_pair'],
            on_price_callback=self.update_price,
            on_order_callback=self.on_order_event,
            api_key=self.config['api']['key'],
            api_secret=self.config['api']['secret']
        )
        self.ws_client.start()
        self.logger.info("WebSocket client started. Fetching initial market price...")
        self.current_price = self._fetch_initial_price()
        self.logger.info(f"Initial market price: {self.current_price}")

        # Determine tick size based on market precision or fallback
        market = self.api.exchange.market(self.api.symbol)
        if 'price' in market['precision']:
            price_precision = market['precision']['price']
        else:
            price_precision = self.config['trading'].get('fallback_price_precision')
        self.tick_size = 10 ** (-price_precision)
        self.logger.info(f"Determined tick size: {self.tick_size} (precision: {price_precision} digits)")

        # Determine the number of parallel order instances (fixed or dynamic)
        self.parallel_instances = self._determine_instances(self.current_price)
        self.logger.info(f"Number of parallel order instances: {self.parallel_instances}")

        # Instantiate the parallel order manager to handle concurrent orders
        self.parallel_order_manager = ParallelOrderManager(self.api, self.state, self.config)

    def _fetch_initial_price(self):
        try:
            ticker = self.api.exchange.fetch_ticker(self.api.symbol)
            price = float(ticker['last'])
            self.logger.debug(f"Fetched initial ticker: {ticker}")
            return price
        except Exception as e:
            self.logger.critical(f"Initial price fetch failed: {str(e)}")
            raise SystemExit(1)

    def update_price(self, price):
        self.current_price = price
        self.logger.debug(f"Price updated via callback: {price}")

    def on_order_event(self, event):
        """
        Called when an order event is received via WebSocket.
        Dispatches the event to the appropriate parallel instance based on order_id.
        """
        order_id = event.get('order_id')
        self.logger.debug(f"Order event received: {event}")
        # Identify the instance (if any) that matches this order_id
        for instance_index, order_state in self.state.active_orders.items():
            if order_state.get('order_id') == order_id:
                self.logger.info(f"Instance {instance_index}: Order {order_id} executed. Processing execution event.")
                self.parallel_order_manager.handle_order_execution(instance_index, event)
                break

    def _calculate_prices(self, last_price, order_type, instance_index=0):
        """
        Calculate trigger and limit prices for an order based on the current price,
        the order type, and the branch (instance) index. Each subsequent instance increases
        the offsets by 1 tick.
        """
        self.logger.debug(f"Calculating prices for {order_type} order with last price: {last_price} for instance {instance_index}")
        tick_size = self.tick_size
        if order_type == 'buy':
            base_trigger = self.config['trading']['buy']['trigger_price_adjust']
            base_limit = self.config['trading']['buy']['limit_price_adjust']
            trigger = last_price + ((base_trigger + instance_index) * tick_size)
            limit = last_price + ((base_limit + instance_index) * tick_size)
        elif order_type == 'sell':
            base_trigger = self.config['trading']['sell']['trigger_price_adjust']
            base_limit = self.config['trading']['sell']['limit_price_adjust']
            trigger = last_price - ((base_trigger + instance_index) * tick_size)
            limit = last_price - ((base_limit + instance_index) * tick_size)
        else:
            raise ValueError("Invalid order type specified")
        decimal_places = -int(math.log10(tick_size))
        trigger = round(trigger, decimal_places)
        limit = round(limit, decimal_places)
        self.logger.debug(f"Instance {instance_index}: Calculated trigger: {trigger}, limit: {limit}")
        return trigger, limit

    def _get_market_price(self):
        self.logger.debug("Fetching current market price...")
        start_time = time.time()
        while self.current_price is None:
            if time.time() - start_time > 5:
                self.logger.error("No price update received from WebSocket within 5 seconds.")
                break
            time.sleep(0.01)
        self.logger.debug(f"Current market price is: {self.current_price}")
        return self.current_price

    def _determine_instances(self, current_price):
        """
        Determine the number of parallel order instances.
        If a fixed value (parallel_instances) is provided in config, use it.
        Otherwise, calculate dynamically by multiplying the first two significant digits
        (with a decimal between them) by the configured dynamic_multiplier.
        If the product has a fractional part, round up by adding 1.
        """
        fixed_instances = self.config['trading'].get('parallel_instances')
        if fixed_instances:
            return fixed_instances
        price_str = f"{current_price:.8f}"
        digits = [c for c in price_str if c.isdigit() and c != '0']
        if len(digits) < 2:
            significant = float(price_str)
        else:
            significant = float(f"{digits[0]}.{digits[1]}")
        multiplier = self.config['trading'].get('dynamic_multiplier', 24)
        product = significant * multiplier
        instances = int(product) if product == int(product) else int(product) + 1
        return instances

    def manage_strategy(self):
        trade_count = 0
        max_trades = self.config['trading'].get('trade_limit')
        self.logger.info("Starting strategy management loop.")
        while True:
            if max_trades is not None and trade_count >= max_trades:
                self.logger.info("Trade limit reached. Exiting trading loop.")
                break
            try:
                # If there are no active orders, place new parallel orders
                if not self.state.active_orders:
                    self.logger.info("No active orders - placing new parallel orders")
                    self.parallel_order_manager.place_new_orders(
                        self._calculate_prices,
                        self._get_market_price,
                        self.parallel_instances
                    )
                else:
                    # Monitor each active order individually
                    self.parallel_order_manager.monitor_active_orders(
                        self._get_market_price,
                        self._calculate_prices
                    )
                time.sleep(self.config['trading']['price_poll_interval'])
                trade_count += 1
            except KeyboardInterrupt:
                self.logger.info("Stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Error in manage_strategy loop: {str(e)}")
                self.parallel_order_manager.recover_state()
                time.sleep(1)
