import logging
import time

class ParallelOrderManager:
    def __init__(self, api, state, config):
        """
        The state object is expected to have an 'active_orders' dictionary,
        where each key is an instance index and its value is a dictionary containing:
          - order_id: the ID of the placed order
          - last_price: the market price when the order was placed
          - order_type: 'buy' or 'sell' for that instance
        Additionally, state.last_buy_amount is used globally for sell order calculations.
        """
        self.api = api
        self.state = state
        self.config = config
        self.logger = logging.getLogger("ParallelOrderManager")

    def place_new_orders(self, calculate_prices_func, get_market_price_func, total_instances):
        """
        For each instance (from 0 to total_instances - 1) that does not currently have an active order,
        place a new order using instance-specific price offsets.
        """
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
            order = self.api.place_stop_limit_order(order_type, trigger, limit, custom_amount=custom_amount)
            if order:
                self.logger.info(f"Instance {instance_index}: Order placed: {order}")
                self.state.active_orders[instance_index] = {
                    'order_id': order['id'],
                    'last_price': last_price,
                    'order_type': order_type
                }
            else:
                self.logger.error(f"Instance {instance_index}: Failed to place order.")

    def monitor_active_orders(self, get_market_price_func, calculate_prices_func):
        """
        Iterate over each active order instance. If market conditions have moved unfavorably
        (for a buy order: current price is below the order's price; for a sell order: current price is above),
        cancel and replace the order for that specific instance.
        """
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
        """
        For the given instance, cancel the current order and place a replacement order using updated prices.
        """
        order_state = self.state.active_orders.get(instance_index)
        if not order_state:
            self.logger.error(f"Instance {instance_index}: No active order found to cancel.")
            return
        order_id = order_state.get('order_id')
        order_type = order_state.get('order_type')
        self.logger.info(f"Instance {instance_index}: Cancelling order {order_id} and replacing it.")
        try:
            if self.api.cancel_order(order_id):
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
                new_order = self.api.place_stop_limit_order(order_type, trigger, limit, custom_amount=custom_amount)
                if new_order:
                    self.logger.info(f"Instance {instance_index}: Replaced order successfully: {new_order}")
                    self.state.active_orders[instance_index] = {
                        'order_id': new_order['id'],
                        'last_price': new_price,
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
        """
        Process an order execution event for a specific instance.
        If a buy order is executed, update the global last_buy_amount.
        Then, flip the order type for that instance and remove its active order record.
        """
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
            self.state.last_buy_amount = executed_amount
            self.logger.info(f"Instance {instance_index}: Stored last buy amount from execution: {executed_amount}")
        # Flip order type for this instance (buy becomes sell and vice versa)
        new_order_type = 'sell' if order_type == 'buy' else 'buy'
        self.logger.info(f"Instance {instance_index}: Flipping order type from {order_type} to {new_order_type}")
        # Remove the executed order so a new order can be placed in the next cycle.
        del self.state.active_orders[instance_index]

    def recover_state(self, instance_index=None):
        """
        In case of errors, cancel and remove active orders.
        If instance_index is provided, recover that specific instance.
        Otherwise, recover all active orders.
        """
        self.logger.info("Initiating recovery of order state for parallel orders.")
        if instance_index is not None:
            order_state = self.state.active_orders.get(instance_index)
            if order_state:
                order_id = order_state.get('order_id')
                try:
                    if self.api.cancel_order(order_id):
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
