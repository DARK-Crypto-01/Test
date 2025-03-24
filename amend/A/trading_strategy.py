import time
import math
import logging
from math import ceil
from gateio_api import GateIOAPIClient
from ws_manager import WSManager
from order_manager import OrderManager
from parallel_order_manager import ParallelOrderManager

class OrderState:
    def __init__(self):
        self.active_orders = {}
        self.order_type = 'buy'
        self.last_buy_amount = None
        self.pending_actions = {}  # {instance_index: timestamp}

class TradingStrategy:
    def __init__(self, config):
        self.config = config
        self.api = GateIOAPIClient(config)
        self.state = OrderState()
        self.logger = logging.getLogger("TradingStrategy")
        
        # Initial price setup
        self.logger.info("Fetching initial market price...")
        self.current_price = self._fetch_initial_price()
        
        # Determine price precision
        market = self.api.exchange.market(self.api.symbol)
        price_precision = market['precision'].get('price', self.config['trading'].get('fallback_price_precision'))
        self.tick_size = 10 ** (-price_precision)
        self.logger.info(f"Determined tick size: {self.tick_size} (precision: {price_precision} digits)")
        
        # WS Manager setup
        self.parallel_instances = self._determine_instances(self.current_price)
        self.ws_manager = WSManager(
            currency_pair=self.config['trading']['currency_pair'],
            on_price_callback=self.update_price,
            on_order_callback=self.on_order_event,
            api_key=self.config['api']['key'],
            api_secret=self.config['api']['secret'],
            config=self.config['api'],
            total_instances=self.parallel_instances,
            max_instances_per_ws=self.config['websocket'].get('max_instances_per_ws', 5)
        )
        
        # Order managers
        self.order_manager = OrderManager(self.api, self.state, self.config, self.ws_manager)
        self.parallel_order_manager = ParallelOrderManager(self.api, self.state, self.config, self.ws_manager)

    def _fetch_initial_price(self):
        try:
            ticker = self.api.exchange.fetch_ticker(self.api.symbol)
            return float(ticker['last'])
        except Exception as e:
            self.logger.critical(f"Initial price fetch failed: {str(e)}")
            raise SystemExit(1)

    def update_price(self, price):
        self.current_price = price

    def on_order_event(self, order_id, event):
        status = event.get('status')
        client_order_id = event.get('client_order_id')
        side = event.get('side')
        filled = float(event.get('filled', 0))
        
        # Find instance index using client_order_id
        instance_index = next(
            (idx for idx, state in self.state.active_orders.items()
             if state.get('client_order_id') == client_order_id),
            None
        )
        
        # Clear pending actions flag
        if instance_index is not None and instance_index in self.state.pending_actions:
            del self.state.pending_actions[instance_index]

        # Handle sell execution (full or partial)
        if side == 'sell' and filled > 0:
            if instance_index is not None and instance_index in self.state.active_orders:
                del self.state.active_orders[instance_index]
                self.logger.info(f"Sell order {client_order_id} removed from tracking (filled: {filled})")
                self.state.order_type = 'buy'
            return  # Skip normal status handling

        # Normal handling for non-sell orders
        if status in ["closed", "filled", "canceled"]:
            if instance_index is not None and instance_index in self.state.active_orders:
                del self.state.active_orders[instance_index]
        elif status == "open":
            self.state.active_orders[instance_index] = {
                'order_id': order_id,
                'client_order_id': client_order_id,
                'symbol': event.get('symbol'),
                'side': side,
                'price': float(event.get('price')),
                'amount': float(event.get('amount')),
                'status': status
            }

    def _calculate_prices(self, last_price, order_type, instance_index=0):
        tick_size = self.tick_size
        if order_type == 'buy':
            base_trigger = self.config['trading']['buy']['trigger_price_adjust']
            base_limit = self.config['trading']['buy']['limit_price_adjust']
            trigger = last_price + ((base_trigger + instance_index) * tick_size)
            limit = last_price + ((base_limit + instance_index) * tick_size)
        else:
            base_trigger = self.config['trading']['sell']['trigger_price_adjust']
            base_limit = self.config['trading']['sell']['limit_price_adjust']
            trigger = last_price - ((base_trigger + instance_index) * tick_size)
            limit = last_price - ((base_limit + instance_index) * tick_size)
        decimal_places = -int(math.log10(tick_size))
        return round(trigger, decimal_places), round(limit, decimal_places)

    def _get_market_price(self):
        start_time = time.time()
        while self.current_price is None:
            if time.time() - start_time > 5:
                self.logger.error("No price update received within 5 seconds.")
                break
            time.sleep(0.01)
        return self.current_price

    def _determine_instances(self, current_price):
        if fixed := self.config['trading'].get('parallel_instances'):
            return fixed
        price_str = f"{current_price:.8f}"
        digits = [c for c in price_str if c.isdigit() and c != '0']
        significant = float(f"{digits[0]}.{digits[1]}") if len(digits) > 1 else float(price_str)
        return int(significant * self.config['trading'].get('dynamic_multiplier', 24))

    def manage_strategy(self):
        trade_count = 0
        max_trades = self.config['trading'].get('trade_limit')
        while True:
            if max_trades and trade_count >= max_trades:
                break
            try:
                if not self.state.active_orders:
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
                break
            except Exception as e:
                self.logger.error(f"Strategy error: {str(e)}")
                self.parallel_order_manager.recover_state()
                time.sleep(1)
