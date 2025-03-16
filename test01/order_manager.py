import logging
import time

class OrderManager:
    def __init__(self, api, state, config):
        self.api = api
        self.state = state
        self.config = config
        self.logger = logging.getLogger("OrderManager")

    def place_new_order(self, calculate_prices_func, get_market_price_func):
        max_retries = 5
        base_delay = 0.1  # Starting delay in seconds
        retries = 0
        while retries < max_retries:
            last_price = get_market_price_func()  # Get updated market price
            order_type = self.state.order_type or 'buy'
            self.logger.info(f"Placing new {order_type} order based on updated price: {last_price}")
            trigger, limit = calculate_prices_func(last_price, order_type)
            
            custom_amount = None
            if order_type == 'sell':
                if self.state.last_buy_amount is not None:
                    fee = 0.001  # 0.1% trading fee
                    custom_amount = self.state.last_buy_amount * (1 - fee)
                    self.logger.debug(f"Calculated sell order amount from last buy order: {custom_amount}")
                else:
                    self.logger.error("No last buy amount available for sell order; cannot calculate amount.")
                    return
            order = self.api.place_stop_limit_order(order_type, trigger, limit, custom_amount=custom_amount)
            if order:
                self.logger.info(f"New order placed: {order}")
                self.state.active = True
                self.state.order_type = order_type
                self.state.last_price = last_price
                self.state.order_id = order['id']
                return
            else:
                delay = base_delay * (2 ** retries)
                self.logger.error(f"Failed to place order; retrying in {delay:.2f} seconds...")
                time.sleep(delay)
                retries += 1

        self.logger.error("Unable to place new order after several retries.")

    def monitor_active_order(self, get_market_price_func, calculate_prices_func):
        current_price = get_market_price_func()
        self.logger.debug(f"Monitoring active order. Current price: {current_price}, Order last price: {self.state.last_price}")
        if self.state.order_type == 'buy' and current_price < self.state.last_price:
            self.logger.info("Price dropped below last price; cancelling buy order.")
            self.cancel_and_replace(get_market_price_func, calculate_prices_func)
        elif self.state.order_type == 'sell' and current_price > self.state.last_price:
            self.logger.info("Price rose above last price; cancelling sell order.")
            self.cancel_and_replace(get_market_price_func, calculate_prices_func)
        else:
            self.logger.debug("No conditions met for cancellation.")

    def cancel_and_replace(self, get_market_price_func, calculate_prices_func):
        order_id = self.state.order_id
        self.logger.info(f"Cancelling order: {order_id} and replacing it.")
        try:
            if self.api.cancel_order(order_id):
                self.state.active = False
                new_price = get_market_price_func()
                trigger, limit = calculate_prices_func(new_price, self.state.order_type)
                custom_amount = None
                if self.state.order_type == 'sell':
                    if self.state.last_buy_amount is not None:
                        fee = 0.001  # 0.1% fee
                        custom_amount = self.state.last_buy_amount * (1 - fee)
                    else:
                        self.logger.error("No last buy amount available for sell order; cannot calculate amount.")
                        return
                new_order = self.api.place_stop_limit_order(self.state.order_type, trigger, limit, custom_amount=custom_amount)
                if new_order:
                    self.logger.info(f"Replaced order successfully with new order: {new_order}")
                    self.state.last_price = new_price
                    self.state.active = True
                    self.state.order_id = new_order['id']
                else:
                    self.logger.error("Failed to place replacement order.")
            else:
                self.logger.error("Cancellation of order failed.")
        except Exception as e:
            self.logger.error(f"Replace failed: {str(e)}")
            self.recover_state()

    def handle_order_execution(self, execution_event):
        """
        Handle order execution event received via WebSocket.
        Extract the executed amount from the event data using the 'filled' field,
        which represents the executed quantity. If 'filled' is not present, fallback to 'amount'.
        """
        self.logger.info("Order executed successfully via WebSocket event.")
        if self.state.order_type == 'buy':
            executed_amount = 0
            if 'filled' in execution_event:
                try:
                    executed_amount = float(execution_event.get('filled', 0))
                except Exception as e:
                    self.logger.error(f"Error parsing filled amount: {e}")
            else:
                try:
                    executed_amount = float(execution_event.get('amount', 0))
                except Exception as e:
                    self.logger.error(f"Error parsing amount: {e}")
            self.state.last_buy_amount = executed_amount
            self.logger.info(f"Stored last buy amount from WebSocket: {executed_amount}")
        self.state.active = False
        # Flip order type: if executed order was a buy, next will be sell; vice versa.
        self.state.order_type = 'sell' if self.state.order_type == 'buy' else 'buy'

    def recover_state(self):
        self.logger.info("Initiating state recovery...")
        max_retries = 3
        recovered = False
        preserved_order_type = self.state.order_type

        for attempt in range(max_retries):
            try:
                if self.state.order_id:
                    if not self.api.cancel_order(self.state.order_id):
                        raise Exception(f"Failed to cancel order {self.state.order_id}")
                self.state.active = False
                self.state.order_type = preserved_order_type or 'buy'
                self.state.order_id = None
                recovered = True
                break
            except Exception as e:
                self.logger.error(f"Recovery attempt {attempt+1} failed: {str(e)}")
                time.sleep(2 ** attempt)

        if not recovered:
            self.logger.critical("State recovery failed after multiple attempts!")
        self.logger.info(f"State recovery completed. Resuming with order type: {self.state.order_type}")
