import logging
import time
import math

def round_up_to_one_significant(x):
    if x == 0:
        return 0
    exponent = math.floor(math.log10(abs(x)))
    factor = 10 ** exponent
    first_digit = int(abs(x) / factor)
    if abs(x) > first_digit * factor:
        first_digit += 1
    if first_digit == 10:
        first_digit = 1
        exponent += 1
    return first_digit * (10 ** exponent)

class ParallelOrderManager:
    def __init__(self, api, state, config, ws_manager):
        self.api = api
        self.state = state
        self.config = config
        self.ws_manager = ws_manager
        self.logger = logging.getLogger("ParallelOrderManager")
        self.pending_timeout = self.config['trading'].get('pending_timeout', 30)

    def place_new_orders(self, calculate_prices_func, get_market_price_func, total_instances):
        for instance_index in range(total_instances):
            if self._should_skip_instance(instance_index):
                continue

            last_price = get_market_price_func()
            order_type = self.state.order_type or 'buy'
            trigger, limit = calculate_prices_func(last_price, order_type, instance_index)
            
            # Calculate amount
            amount = None
            if order_type == 'sell':
                if self.state.last_buy_amount:
                    fee_rate = self.config['trading'].get('sell_trading_fee', 0.001)
                    raw_fee = self.state.last_buy_amount * fee_rate
                    amount = self.state.last_buy_amount - round_up_to_one_significant(raw_fee)
                else:
                    continue
            else:
                amount = self.api.calculate_order_amount(order_type, limit)
            
            # Place order
            ws_client = self.ws_manager.get_ws_client(instance_index)
            order = ws_client.place_stop_limit_order_ws(order_type, trigger, limit, amount)
            
            if order:
                self.state.pending_actions[instance_index] = time.time()
                self.state.active_orders[instance_index] = {
                    'order_id': order.get('id'),
                    'client_order_id': order.get('client_order_id'),
                    'last_price': last_price,
                    'limit_price': limit,
                    'order_type': order_type,
                    'timestamp': time.time()
                }

    def _should_skip_instance(self, instance_index):
        if instance_index in self.state.active_orders:
            return True
        pending_time = self.state.pending_actions.get(instance_index)
        if pending_time:
            if time.time() - pending_time < self.pending_timeout:
                return True
            else:
                del self.state.pending_actions[instance_index]
        return False

    def amend_order(self, instance_index, get_market_price_func, calculate_prices_func):
        if instance_index in self.state.pending_actions:
            return

        order_state = self.state.active_orders.get(instance_index)
        if not order_state:
            return

        try:
            self.state.pending_actions[instance_index] = time.time()
            new_price = get_market_price_func()
            trigger, limit = calculate_prices_func(new_price, 
                                                 order_state['order_type'], 
                                                 instance_index)
            
            # Prepare amendment
            new_amount = None
            if order_state['order_type'] == 'buy':
                new_amount = self.api.calculate_order_amount('buy', limit)
            
            ws_client = self.ws_manager.get_ws_client(instance_index)
            ws_client.amend_order_ws(
                order_state['client_order_id'],
                trigger,
                limit,
                new_amount=new_amount
            )
            order_state['last_price'] = new_price
        except Exception as e:
            self.logger.error(f"Amendment failed: {str(e)}")
            del self.state.pending_actions[instance_index]

    def monitor_active_orders(self, get_market_price_func, calculate_prices_func):
        current_price = get_market_price_func()
        now = time.time()
        
        # Cleanup expired pending flags
        for idx, ts in list(self.state.pending_actions.items()):
            if now - ts > self.pending_timeout:
                del self.state.pending_actions[idx]
                self.logger.warning(f"Cleared expired pending flag for instance {idx}")

        # Check active orders
        for instance_index, order_state in list(self.state.active_orders.items()):
            if instance_index in self.state.pending_actions:
                continue
                
            order_type = order_state['order_type']
            last_price = order_state['last_price']
            
            if ((order_type == 'buy' and current_price < last_price) or 
                (order_type == 'sell' and current_price > last_price)):
                self.amend_order(instance_index, get_market_price_func, calculate_prices_func)

    def handle_order_execution(self, order_id, event):
        for idx, state in list(self.state.active_orders.items()):
            if state['order_id'] == order_id:
                if state['order_type'] == 'buy':
                    self.state.last_buy_amount = float(event.get('filled', 0))
                del self.state.active_orders[idx]
                break

    def recover_state(self, instance_index=None):
        self.logger.info("Initiating state recovery...")
        if instance_index is not None:
            self._cancel_instance_order(instance_index)
        else:
            for idx in list(self.state.active_orders.keys()):
                self._cancel_instance_order(idx)

    def _cancel_instance_order(self, instance_index):
        order_state = self.state.active_orders.get(instance_index)
        if order_state:
            try:
                ws_client = self.ws_manager.get_ws_client(instance_index)
                ws_client.cancel_order_ws(order_state['order_id'])
            except Exception as e:
                self.logger.error(f"Recovery error: {str(e)}")
            finally:
                del self.state.active_orders[instance_index]

    def graceful_shutdown(self):
        self.logger.info("Starting graceful shutdown...")
        self.state.pending_actions.clear()
        
        # Cancel buy orders
        for idx, state in list(self.state.active_orders.items()):
            if state['order_type'] == 'buy':
                self._cancel_instance_order(idx)
        
        # Process sell orders
        sell_orders = [(idx, state) for idx, state in self.state.active_orders.items()
                      if state['order_type'] == 'sell']
        sell_orders.sort(key=lambda x: x[1]['limit_price'])
        
        for idx, state in sell_orders:
            try:
                ws_client = self.ws_manager.get_ws_client(idx)
                if ws_client.cancel_order_ws(state['order_id']):
                    amount = state.get('executed_amount')
                    if amount and amount > 0:
                        ws_client.place_market_order_ws('sell', amount)
            except Exception as e:
                self.logger.error(f"Shutdown error: {str(e)}")
            finally:
                if idx in self.state.active_orders:
                    del self.state.active_orders[idx]
