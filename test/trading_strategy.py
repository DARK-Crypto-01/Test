# trading_strategy.py
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

        # Initialize WebSocket client with both callbacks
        self.ws_client = GateIOWebSocketClient(
            currency_pair=self.config['trading']['currency_pair'],
            on_price_callback=self.update_price,
            on_order_callback=self.on_order_event,  # New callback for order events
            api_key=self.config['api']['key'],
            api_secret=self.config['api']['secret']
        )
        self.ws_client.start()

        self.logger.info("WebSocket client started. Fetching initial market price...")
        self.current_price = self._fetch_initial_price()
        self.logger.info(f"Initial market price: {self.current_price}")

        # Determine tick size based on market precision
        market = self.api.exchange.market(self.api.symbol)
        if 'price' in market['precision']:
            price_precision = market['precision']['price']
        else:
            fallback = self.config['trading'].get('fallback_price_precision')
            if fallback is None:
                raise ValueError("Price precision not provided by exchange and no fallback configured.")
            price_precision = fallback

        self.tick_size = 10 ** (-price_precision)
        self.logger.info(f"Determined tick size: {self.tick_size} (precision: {price_precision} digits)")

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

    def on_order_event(self, event):
        """Called when an order event is received via websocket.
           If the event corresponds to the current active order and indicates execution,
           call the order manager’s execution handler.
        """
        event_order_id = event.get('order_id')
        self.logger.debug(f"Order event received: {event}")
        if self.state.active and event_order_id == self.state.order_id:
            self.logger.info(f"Active order {event_order_id} executed. Processing execution event.")
            self.order_manager.handle_order_execution()

    def _calculate_prices(self, last_price, order_type):
        self.logger.debug(f"Calculating prices for {order_type} order with last price: {last_price}")
        tick_size = self.tick_size
        
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
        
        decimal_places = -int(math.log10(tick_size))
        trigger = round(trigger, decimal_places)
        limit = round(limit, decimal_places)
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
                if not self.state.active:
                    self.logger.info("No active order - placing new order")
                    self.order_manager.place_new_order(self._calculate_prices, self._get_market_price)
                else:
                    # Monitor only the order stored in state
                    self.order_manager.monitor_active_order(self._get_market_price, self._calculate_prices)
                time.sleep(self.config['trading']['price_poll_interval'])
            except KeyboardInterrupt:
                self.logger.info("Stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Error in manage_strategy loop: {str(e)}")
                self.order_manager.recover_state()
                time.sleep(1)
