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
            factor = 10 ** exponent
    return first_digit * factor

class ParallelOrderManager:
    def __init__(self, api, state, config, ws_manager):
        self.api = api
        self.state = state
        self.config = config
        self.ws_manager = ws_manager
        self.logger = logging.getLogger("ParallelOrderManager")

    def place_new_orders(self, calculate_prices_func, get_market_price_func, total_instances):
        for instance_index in range(total_instances):
            lock = self.state.instance_locks[instance_index]
            with lock:
                if self.state.pending_flags.get(instance_index, False):
                    self.logger.debug(f"Instance {instance_index} is pending, skipping order placement.")
                    continue
                if instance_index in self.state.active_orders:
                    continue
                # Set pending flag before initiating the order action.
                self.state.pending_flags[instance_index] = True
                last_price = get_market_price_func()
                order_type = self.state.order_type or 'buy'
                self.logger.info(f"Instance {instance_index}: Placing new {order_type} order at price {last_price}")
                trigger, limit = calculate_prices_func(last_price, order_type, instance_index)
                custom_amount = None
                if order_type == 'sell':
                    if self.state.last_buy_amount is not None:
                        fee_rate = self.config['trading'].get('sell_trading_fee', 0.001)
                        raw_fee = self.state.last_buy_amount * fee_rate
                        rounded_fee = round_up_to_one_significant(raw_fee)
                        custom_amount = self.state.last_buy_amount - rounded_fee
                        self.logger.debug(f"Instance {instance_index}: Calculated sell order amount: {custom_amount} "
                                          f"(raw fee: {raw_fee}, rounded fee: {rounded_fee})")
                    else:
                        self.logger.error(f"Instance {instance_index}: No last buy amount available for sell order; skipping.")
                        self.state.pending_flags[instance_index] = False
                        continue
                amount = custom_amount if custom_amount is not None else self.api.calculate_order_amount(order_type, limit)
                ws_client = self.ws_manager.get_ws_client(instance_index)
                order = ws_client.place_stop_limit_order_ws(order_type, trigger, limit, amount)
                if not order:
                    self.logger.error(f"Instance {instance_index}: WebSocket order placement failed; falling back to API.")
                    order = self.api.place_stop_limit_order(order_type, trigger, limit, custom_amount=custom_amount)
                if order:
                    self.logger.info(f"Instance {instance_index}: Order placed: {order}")
                    self.state.active_orders[instance_index] = {
                        'order_id': order.get('id', None),
                        'client_order_id': order.get('client_order_id'),
                        'last_price': last_price,
                        'limit_price': limit,
                        'order_type': order_type
                    }
                else:
                    self.logger.error(f"Instance {instance_index}: Failed to place order.")
                # Clear pending flag after processing.
                self.state.pending_flags[instance_index] = False

    def amend_order(self, instance_index, get_market_price_func, calculate_prices_func):
        lock = self.state.instance_locks[instance_index]
        with lock:
            order_state = self.state.active_orders.get(instance_index)
            if not order_state:
                self.logger.error(f"Instance {instance_index}: No active order found to amend.")
                return
            if self.state.pending_flags.get(instance_index, False):
                self.logger.debug(f"Instance {instance_index} is already processing an action; skipping amendment.")
                return
            # Set pending flag before initiating the amendment.
            self.state.pending_flags[instance_index] = True
            new_price = get_market_price_func()
            trigger, limit = calculate_prices_func(new_price, order_state.get('order_type'), instance_index)
            new_amount = None
            if order_state.get('order_type') == 'buy':
                new_amount = self.api.calculate_order_amount('buy', limit)
            ws_client = self.ws_manager.get_ws_client(instance_index)
            client_order_id = order_state.get('client_order_id')
            order = ws_client.amend_order_ws(client_order_id, trigger, limit, new_amount=new_amount)
            if order:
                self.logger.info(f"Instance {instance_index}: Order amended successfully via WebSocket: {order}")
                order_state['last_price'] = new_price
            else:
                self.logger.error(f"Instance {instance_index}: WebSocket amendment failed; trying REST fallback.")
                order_id = order_state.get('order_id')
                order = self.api.amend_stop_limit_order(order_id, order_state.get('order_type'), trigger, limit, custom_amount=new_amount)
                if order:
                    self.logger.info(f"Instance {instance_index}: Order amended successfully via REST fallback: {order}")
                    order_state['last_price'] = new_price
                else:
                    self.logger.error(f"Instance {instance_index}: REST amendment fallback also failed.")
            # Clear pending flag after processing.
            self.state.pending_flags[instance_index] = False

    def monitor_active_orders(self, get_market_price_func, calculate_prices_func):
        current_price = get_market_price_func()
        for instance_index in list(self.state.active_orders.keys()):
            lock = self.state.instance_locks[instance_index]
            with lock:
                if self.state.pending_flags.get(instance_index, False):
                    self.logger.debug(f"Instance {instance_index} is pending, skipping monitoring.")
                    continue
                order_state = self.state.active_orders.get(instance_index)
                if not order_state:
                    continue
                order_type = order_state.get('order_type')
                last_price = order_state.get('last_price')
                self.logger.debug(f"Instance {instance_index}: Monitoring order. Current price: {current_price}, Order price: {last_price}")
                condition = False
                if order_type == 'buy' and current_price < last_price:
                    condition = True
                elif order_type == 'sell' and current_price > last_price:
                    condition = True
                if condition:
                    self.logger.info(f"Instance {instance_index}: Price condition met for order amendment.")
                    # Set pending flag and call amendment.
                    self.state.pending_flags[instance_index] = True
            self.amend_order(instance_index, get_market_price_func, calculate_prices_func)

    def handle_order_execution(self, order_id, event):
        self.logger.info(f"Handling execution for order {order_id}")
        # Expand this method as needed to map order execution events to instance indexes.

    def recover_state(self, instance_index=None):
        self.logger.info("Initiating recovery of order state for parallel orders.")
        if instance_index is not None:
            lock = self.state.instance_locks[instance_index]
            with lock:
                order_state = self.state.active_orders.get(instance_index)
                if order_state:
                    order_id = order_state.get('order_id')
                    try:
                        ws_client = self.ws_manager.get_ws_client(instance_index)
                        if ws_client.cancel_order_ws(order_id):
                            self.logger.info(f"Instance {instance_index}: Order {order_id} cancelled during recovery.")
                        else:
                            self.logger.error(f"Instance {instance_index}: Failed to cancel order {order_id} during recovery.")
                    except Exception as e:
                        self.logger.error(f"Instance {instance_index}: Recovery error: {str(e)}")
                    del self.state.active_orders[instance_index]
        else:
            for idx in list(self.state.active_orders.keys()):
                self.recover_state(idx)
        self.logger.info("Recovery of parallel orders completed.")

    def graceful_shutdown(self):
        self.logger.info("Initiating graceful shutdown of bot-managed orders...")
        # Process buy orders: cancel them.
        for instance_index in list(self.state.active_orders.keys()):
            lock = self.state.instance_locks[instance_index]
            with lock:
                order_state = self.state.active_orders.get(instance_index)
                if order_state and order_state.get('order_type') == 'buy':
                    order_id = order_state.get('order_id')
                    self.logger.info(f"Instance {instance_index}: Cancelling bot-placed buy order {order_id}")
                    try:
                        ws_client = self.ws_manager.get_ws_client(instance_index)
                        ws_client.cancel_order_ws(order_id)
                    except Exception as e:
                        self.logger.error(f"Instance {instance_index}: Failed to cancel buy order {order_id}: {e}")
                    del self.state.active_orders[instance_index]
        # Process sell orders: cancel and, if appropriate, place market orders.
        sell_orders = []
        for instance_index, order_state in self.state.active_orders.items():
            if order_state.get('order_type') == 'sell':
                sell_orders.append((instance_index, order_state))
        if sell_orders:
            sell_orders.sort(key=lambda x: x[1].get('limit_price', float('inf')))
            for instance_index, order_state in sell_orders:
                lock = self.state.instance_locks[instance_index]
                with lock:
                    order_id = order_state.get('order_id')
                    limit_price = order_state.get('limit_price')
                    self.logger.info(f"Instance {instance_index}: Processing sell order {order_id} with limit price {limit_price}")
                    try:
                        ws_client = self.ws_manager.get_ws_client(instance_index)
                        if ws_client.cancel_order_ws(order_id):
                            self.logger.info(f"Instance {instance_index}: Cancelled sell order {order_id}")
                            amount = order_state.get('executed_amount')
                            if not amount or amount <= 0:
                                self.logger.error(f"Instance {instance_index}: No valid executed amount to sell; skipping market order.")
                            else:
                                market_order = ws_client.place_market_order_ws('sell', amount)
                                if market_order:
                                    self.logger.info(f"Instance {instance_index}: Market sell order placed: {market_order}")
                                else:
                                    self.logger.error(f"Instance {instance_index}: Failed to place market sell order.")
                        else:
                            self.logger.error(f"Instance {instance_index}: Failed to cancel sell order {order_id}.")
                    except Exception as e:
                        self.logger.error(f"Instance {instance_index}: Error during processing sell order: {e}")
                    finally:
                        if instance_index in self.state.active_orders:
                            del self.state.active_orders[instance_index]
        self.logger.info("Graceful shutdown complete. All bot-managed orders have been processed.")
