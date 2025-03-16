import logging
import time

class ParallelOrderManager:
    def __init__(self, api, state, config, ws_client):
        """
        The state object is expected to have an 'active_orders' dictionary,
        where each key is an instance index and its value is a dictionary containing:
          - order_id: the ID of the placed order
          - last_price: the market price when the order was placed
          - limit_price: the calculated limit price for that order
          - order_type: 'buy' or 'sell' for that instance
          - executed_amount: (optional) the amount executed (set upon buy execution)
        Additionally, state.last_buy_amount is used globally for sell order calculations.
        """
        self.api = api
        self.state = state
        self.config = config
        self.ws_client = ws_client
        self.logger = logging.getLogger("ParallelOrderManager")

    def place_new_orders(self, calculate_prices_func, get_market_price_func, total_instances):
        for instance_index in range(total_instances):
            if instance_index in self.state.active_orders:
                continue  # Skip if an active order already exists for this instance.
            last_price = get_market_price_func()
            order_type = self.state.order_type or 'buy'
            self.logger.info(f"Instance {instance_index}: Placing new {order_type} order at price {last_price}")
            trigger, limit = calculate_prices_func(last_price, order_type, instance_index)
            custom_amount = None
            if order_type == 'sell':
                if self.state.last_buy_amount is not None:
                    fee = 0.001  # 0.1% trading fee
                    custom_amount = self.state.last_buy_amount * (1 - fee)
                    self.logger.debug(f"Instance {instance_index}: Calculated sell order amount: {custom_amount}")
                else:
                    self.logger.error(f"Instance {instance_index}: No last buy amount available for sell order; skipping.")
                    continue
            amount = custom_amount if custom_amount is not None else self.api.calculate_order_amount(order_type, limit)
            order = self.ws_client.place_stop_limit_order_ws(order_type, trigger, limit, amount)
            if not order:
                self.logger.error(f"Instance {instance_index}: WebSocket order placement failed; falling back to API.")
                order = self.api.place_stop_limit_order(order_type, trigger, limit, custom_amount=custom_amount)
            if order:
                self.logger.info(f"Instance {instance_index}: Order placed: {order}")
                self.state.active_orders[instance_index] = {
                    'order_id': order.get('id', None),
                    'last_price': last_price,
                    'limit_price': limit,  # Store limit price for shutdown ordering
                    'order_type': order_type
                }
            else:
                self.logger.error(f"Instance {instance_index}: Failed to place order.")

    def monitor_active_orders(self, get_market_price_func, calculate_prices_func):
        current_price = get_market_price_func()
        for instance_index, order_state in list(self.state.active_orders.items()):
            order_type = order_state.get('order_type')
            last_price = order_state.get('last_price')
            self.logger.debug(f"Instance {instance_index}: Monitoring order. Current price: {current_price}, Order price: {last_price}")
            if order_type == 'buy' and current_price < last_price:
                self.logger.info(f"Instance {instance_index}: Price dropped below order price; cancelling buy order.")
                self.cancel_and_replace(instance_index, get_market_price_func, calculate_prices_func)
            elif order_type == 'sell' and current_price > last_price:
                self.logger.info(f"Instance {instance_index}: Price rose above order price; cancelling sell order.")
                self.cancel_and_replace(instance_index, get_market_price_func, calculate_prices_func)
            else:
                self.logger.debug(f"Instance {instance_index}: No conditions met for cancellation.")

    def cancel_and_replace(self, instance_index, get_market_price_func, calculate_prices_func):
        order_state = self.state.active_orders.get(instance_index)
        if not order_state:
            self.logger.error(f"Instance {instance_index}: No active order found to cancel.")
            return
        order_id = order_state.get('order_id')
        order_type = order_state.get('order_type')
        self.logger.info(f"Instance {instance_index}: Cancelling order {order_id} and replacing it.")
        try:
            if self.ws_client.cancel_order_ws(order_id):
                new_price = get_market_price_func()
                trigger, limit = calculate_prices_func(new_price, order_type, instance_index)
                custom_amount = None
                if order_type == 'sell':
                    if self.state.last_buy_amount is not None:
                        fee = 0.001
                        custom_amount = self.state.last_buy_amount * (1 - fee)
                    else:
                        self.logger.error(f"Instance {instance_index}: No last buy amount available for sell order; cannot replace.")
                        return
                amount = custom_amount if custom_amount is not None else self.api.calculate_order_amount(order_type, limit)
                new_order = self.ws_client.place_stop_limit_order_ws(order_type, trigger, limit, amount)
                if not new_order:
                    self.logger.error(f"Instance {instance_index}: WebSocket replacement order failed; falling back to API.")
                    new_order = self.api.place_stop_limit_order(order_type, trigger, limit, custom_amount=custom_amount)
                if new_order:
                    self.logger.info(f"Instance {instance_index}: Replaced order successfully: {new_order}")
                    self.state.active_orders[instance_index] = {
                        'order_id': new_order.get('id', None),
                        'last_price': new_price,
                        'limit_price': limit,
                        'order_type': order_type
                    }
                else:
                    self.logger.error(f"Instance {instance_index}: Failed to place replacement order.")
            else:
                self.logger.error(f"Instance {instance_index}: Cancellation of order {order_id} failed.")
        except Exception as e:
            self.logger.error(f"Instance {instance_index}: Error during cancel and replace: {str(e)}")
            self.recover_state(instance_index)

    def handle_order_execution(self, instance_index, execution_event):
        self.logger.info(f"Instance {instance_index}: Order executed successfully via WebSocket event.")
        order_state = self.state.active_orders.get(instance_index)
        if not order_state:
            self.logger.error(f"Instance {instance_index}: No active order state found for execution event.")
            return
        order_type = order_state.get('order_type')
        if order_type == 'buy':
            executed_amount = 0
            if 'filled' in execution_event:
                try:
                    executed_amount = float(execution_event.get('filled', 0))
                except Exception as e:
                    self.logger.error(f"Instance {instance_index}: Error parsing filled amount: {e}")
            else:
                try:
                    executed_amount = float(execution_event.get('amount', 0))
                except Exception as e:
                    self.logger.error(f"Instance {instance_index}: Error parsing amount: {e}")
            order_state['executed_amount'] = executed_amount
            self.state.last_buy_amount = executed_amount
            self.logger.info(f"Instance {instance_index}: Stored executed buy amount: {executed_amount}")
        new_order_type = 'sell' if order_type == 'buy' else 'buy'
        self.logger.info(f"Instance {instance_index}: Flipping order type from {order_type} to {new_order_type}")
        if instance_index in self.state.active_orders:
            del self.state.active_orders[instance_index]

    def recover_state(self, instance_index=None):
        self.logger.info("Initiating recovery of order state for parallel orders.")
        if instance_index is not None:
            order_state = self.state.active_orders.get(instance_index)
            if order_state:
                order_id = order_state.get('order_id')
                try:
                    if self.ws_client.cancel_order_ws(order_id):
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
        # Cancel all buy orders immediately.
        for instance_index, order_state in list(self.state.active_orders.items()):
            if order_state.get('order_type') == 'buy':
                order_id = order_state.get('order_id')
                self.logger.info(f"Instance {instance_index}: Cancelling bot-placed buy order {order_id}")
                try:
                    self.ws_client.cancel_order_ws(order_id)
                except Exception as e:
                    self.logger.error(f"Instance {instance_index}: Failed to cancel buy order {order_id}: {e}")
                del self.state.active_orders[instance_index]
        
        # Process sell orders: cancel and replace with market orders sequentially.
        sell_orders = []
        for instance_index, order_state in self.state.active_orders.items():
            if order_state.get('order_type') == 'sell':
                sell_orders.append((instance_index, order_state))
        
        if sell_orders:
            sell_orders.sort(key=lambda x: x[1].get('limit_price', float('inf')))
            for instance_index, order_state in sell_orders:
                order_id = order_state.get('order_id')
                limit_price = order_state.get('limit_price')
                self.logger.info(f"Instance {instance_index}: Processing sell order {order_id} with limit price {limit_price}")
                try:
                    if self.ws_client.cancel_order_ws(order_id):
                        self.logger.info(f"Instance {instance_index}: Cancelled sell order {order_id}")
                        amount = order_state.get('executed_amount')
                        if not amount or amount <= 0:
                            self.logger.error(f"Instance {instance_index}: No valid executed amount to sell; skipping market order.")
                        else:
                            market_order = self.ws_client.place_market_order_ws('sell', amount)
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
