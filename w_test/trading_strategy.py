import time
import math
import logging
from gateio_api import GateIOAPIClient
from ws_manager import WSManager
from order_manager import OrderManager
from parallel_order_manager import ParallelOrderManager

class OrderState:
    def __init__(self):
        self.active_orders = {}   # key: instance_index, value: dict with order details
        self.order_type = 'buy'   # Starting order type
        self.last_buy_amount = None  # To store executed buy amount (for sell orders)

class TradingStrategy:
    def __init__(self, config):
        self.config = config
        self.api = GateIOAPIClient(config)
        self.state = OrderState()
        self.logger = logging.getLogger("TradingStrategy")

        # Fetch initial market price via REST API
        self.logger.info("Fetching initial market price...")
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

        # Create a WSManager instance to manage multiple WebSocket connections.
        max_ws_instances = self.config.get('websocket', {}).get('max_instances_per_ws', 5)
        from math import ceil  # just in case
        self.ws_manager = WSManager(
            currency_pair=self.config['trading']['currency_pair'],
            on_price_callback=self.update_price,
            on_order_callback=self.on_order_event,
            api_key=self.config['api']['key'],
            api_secret=self.config['api']['secret'],
            config=self.config['api'],
            total_instances=self.parallel_instances,
            max_instances_per_ws=max_ws_instances
        )

        # Instantiate order managers with the WSManager for primary order management.
        self.order_manager = OrderManager(self.api, self.state, self.config, self.ws_manager)
        self.parallel_order_manager = ParallelOrderManager(self.api, self.state, self.config, self.ws_manager)

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

    def on_order_event(self, order_id, event):
        """
         Handles order events (creation, fills, cancellations) using the exchange's order_id.
        """
        status = event.get('status')
        self.logger.debug(f"Order event received for {order_id}: {event}")

        # Remove from active_orders if order is completed
        if status in ["closed", "filled", "canceled"]:
            if order_id in self.state.active_orders:
                del self.state.active_orders[order_id]
            self.logger.info(f"Order {order_id} executed with status: {status}")
    
        # Update active_orders with real order data
        if status == "open":
            self.state.active_orders[order_id] = {
                'order_id': order_id,
                'symbol': event.get('symbol'),
                'side': event.get('side'),
                'price': float(event.get('price')),
                'amount': float(event.get('amount')),
                'filled': float(event.get('filled', 0.0)),
                'status': status
                }

    def _calculate_prices(self, last_price, order_type, instance_index=0):
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
                if not self.state.active_orders:
                    self.logger.info("No active orders - placing new parallel orders")
                    self.parallel_order_manager.place_new_orders(
                        self._calculate_prices,
                        self._get_market_price,
                        self.parallel_instances
                    )
                else:
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
