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
            last_price = get_market_price_func()  # Get the updated market price
            order_type = self.state.order_type or 'buy'
            self.logger.info(f"Placing new {order_type} order based on updated price: {last_price}")
            trigger, limit = calculate_prices_func(last_price, order_type)
            order = self.api.place_stop_limit_order(order_type, trigger, limit)
            if order:
                self.logger.info(f"New order placed: {order}")
                self.state.active = True
                self.state.order_type = order_type
                self.state.last_price = last_price
                self.state.order_id = order['id']
                return
            else:
                delay = base_delay * (2 ** retries)
                self.logger.error(
                    f"Failed to place order; retrying with updated market price in {delay:.2f} seconds..."
                )
                time.sleep(delay)
                retries += 1

        self.logger.error("Unable to place new order after several retries.")

    def monitor_active_order(self, order, get_market_price_func, calculate_prices_func):
        current_price = get_market_price_func()
        self.logger.debug(f"Monitoring active order. Current price: {current_price}, Order last price: {self.state.last_price}")
        if self.state.order_type == 'buy' and current_price < self.state.last_price:
            self.logger.info("Price dropped below last price; cancelling buy order.")
            self.cancel_and_replace(order, get_market_price_func, calculate_prices_func)
        elif self.state.order_type == 'sell' and current_price > self.state.last_price:
            self.logger.info("Price rose above last price; cancelling sell order.")
            self.cancel_and_replace(order, get_market_price_func, calculate_prices_func)
        else:
            self.logger.debug("No conditions met for cancellation.")

    def cancel_and_replace(self, order, get_market_price_func, calculate_prices_func):
        self.logger.info(f"Cancelling order: {order['id']} and replacing it.")
        try:
            if self.api.cancel_order(order['id']):
                self.state.active = False
                new_price = get_market_price_func()
                trigger, limit = calculate_prices_func(new_price, self.state.order_type)
                new_order = self.api.place_stop_limit_order(self.state.order_type, trigger, limit)
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

    def handle_order_execution(self):
        self.logger.info("Order executed successfully.")
        self.state.active = False
        # Flip order type for the next trade
        self.state.order_type = 'sell' if self.state.order_type == 'buy' else 'buy'
        self.logger.info(f"Next order type set to: {self.state.order_type}")

    def recover_state(self):
        self.logger.info("Initiating state recovery...")
        max_retries = 3
        recovered = False
        preserved_order_type = self.state.order_type  # Capture the current order type

        for attempt in range(max_retries):
            try:
                # 1. Cancel all existing orders
                canceled = self.api.cancel_all_orders(self.config['trading']['currency_pair'])
                open_orders = self.api.get_open_orders()
                if open_orders:
                    self.logger.error(f"Failed to cancel orders: {open_orders}")
                    raise Exception("Order cancellation failed")
                # 2. Reset state while preserving order type
                self.state.active = False
                self.state.order_type = preserved_order_type or 'buy'
                recovered = True
                break
            except Exception as e:
                self.logger.error(f"Recovery attempt {attempt+1} failed: {str(e)}")
                time.sleep(2 ** attempt)

        if not recovered:
            self.logger.critical("State recovery failed after multiple attempts!")
            # Add emergency shutdown logic here if needed

        self.logger.info(f"State recovery completed. Resuming with order type: {self.state.order_type}")
