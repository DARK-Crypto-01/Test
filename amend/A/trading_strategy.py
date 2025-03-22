import time
import math
import logging
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
        self.current_price = self._fetch_initial_price()
        market = self.api.exchange.market(self.api.symbol)
        price_precision = market['precision'].get('price', 
            self.config['trading'].get('fallback_price_precision'))
        self.tick_size = 10 ** (-price_precision)
        
        # WS Manager setup
        self.parallel_instances = self._determine_instances(self.current_price)
        self.ws_manager = WSManager(
            self.config['trading']['currency_pair'],
            self.update_price,
            self.on_order_event,
            self.config['api']['key'],
            self.config['api']['secret'],
            self.config['api'],
            self.parallel_instances,
            self.config['websocket'].get('max_instances_per_ws', 5)
        )
        
        # Order managers
        self.order_manager = OrderManager(self.api, self.state, self.config, self.ws_manager)
        self.parallel_order_manager = ParallelOrderManager(
            self.api, self.state, self.config, self.ws_manager)

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
        
        # Find and clear pending flag
        instance_index = next(
            (idx for idx, state in self.state.active_orders.items()
             if state.get('client_order_id') == client_order_id),
            None
        )
        if instance_index is not None and instance_index in self.state.pending_actions:
            del self.state.pending_actions[instance_index]
        
        # Update order status
        if status in ["closed", "filled", "canceled"]:
            if order_id in self.state.active_orders:
                del self.state.active_orders[order_id]
        elif status == "open":
            self.state.active_orders[order_id] = {
                'order_id': order_id,
                'symbol': event.get('symbol'),
                'side': event.get('side'),
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
