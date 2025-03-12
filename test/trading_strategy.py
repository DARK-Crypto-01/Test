import time
import math
import logging
from gateio_api import GateIOAPIClient
from gateio_websocket import GateIOWebSocketClient
from order_manager import OrderManager

class OrderState:
    def __init__(self):
        self.active = False
        self.order_type = None  # 'buy' or 'sell'
        self.last_price = None
        self.order_id = None

class TradingStrategy:
    def __init__(self, config):
        self.config = config
        self.api = GateIOAPIClient(config)
        self.state = OrderState()
        self.logger = logging.getLogger("TradingStrategy")
        self.current_price = None

        # Initialize WebSocket client with credentials
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

        # Instantiate OrderManager to handle order operations
        self.order_manager = OrderManager(self.api, self.state, self.config)

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

    def _calculate_prices(self, last_price, order_type):
        self.logger.debug(f"Calculating prices for {order_type} order with last price: {last_price}")
        if order_type == 'buy':
            trigger = last_price * (1 + self.config['trading'][order_type]['trigger_price_adjust'] / 100)
            limit = last_price * (1 + self.config['trading'][order_type]['limit_price_adjust'] / 100)
        elif order_type == 'sell':
            trigger = last_price * (1 - self.config['trading'][order_type]['trigger_price_adjust'] / 100)
            limit = last_price * (1 - self.config['trading'][order_type]['limit_price_adjust'] / 100)
        self.logger.debug(f"Calculated trigger: {trigger}, limit: {limit}")
        return trigger, limit

    def _get_market_price(self):
        self.logger.debug("Fetching current market price...")
        start_time = time.time()
        while self.current_price is None:
            if time.time() - start_time > 5:
                self.logger.error("No price update received from websocket within 5 seconds.")
                break
            time.sleep(0.01)
        self.logger.debug(f"Current market price is: {self.current_price}")
        return self.current_price

    def manage_strategy(self):
        trade_count = 0
        max_trades = self.config['trading'].get('trade_limit')
        self.logger.info("Starting strategy management loop.")
        while True:
            if max_trades is not None and trade_count >= max_trades:
                self.logger.info("Trade limit reached. Exiting trading loop.")
                break
            try:
                open_orders = self.api.get_open_orders()
                self.logger.debug(f"Open orders: {open_orders}")
                if not open_orders:
                    if not self.state.active:
                        self.logger.info("No active orders - placing initial order")
                        self.order_manager.place_new_order(self._calculate_prices, self._get_market_price)
                    else:
                        self.logger.info("Order executed successfully")
                        self.order_manager.handle_order_execution()
                        trade_count += 1  # Increment only when order execution is confirmed
                else:
                    self.logger.debug("Monitoring existing orders")
                    self.order_manager.monitor_active_order(
                        open_orders[0],
                        self._get_market_price,
                        self._calculate_prices
                    )
                time.sleep(self.config['trading']['price_poll_interval'])
            except KeyboardInterrupt:
                self.logger.info("Stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Error in manage_strategy loop: {str(e)}")
                self.order_manager.recover_state()
                time.sleep(1)
