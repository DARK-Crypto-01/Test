import logging
import time
import math

def round_up_to_one_significant(x):
    """
    Rounds a positive number up to one significant figure.
    Examples:
      801 -> 900
      0.000801 -> 0.0009
    """
    if x == 0:
        return 0
    exponent = math.floor(math.log10(abs(x)))
    factor = 10 ** exponent
    first_digit = int(abs(x) / factor)
    # If any fraction exists beyond the first digit, bump it up by one.
    if abs(x) > first_digit * factor:
        first_digit += 1
        # Handle the rollover case (e.g. 9 -> 10)
        if first_digit == 10:
            first_digit = 1
            exponent += 1
            factor = 10 ** exponent
    return first_digit * factor

class OrderManager:
    def __init__(self, api, state, config, ws_manager):
        self.api = api
        self.state = state
        self.config = config
        self.ws_manager = ws_manager
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
                    fee_rate = self.config['trading'].get('sell_trading_fee', 0.001)
                    raw_fee = self.state.last_buy_amount * fee_rate
                    rounded_fee = round_up_to_one_significant(raw_fee)
                    custom_amount = self.state.last_buy_amount - rounded_fee
                    self.logger.debug(f"Calculated sell order amount from last buy order: {custom_amount} "
                                      f"(raw fee: {raw_fee}, rounded fee: {rounded_fee})")
                else:
                    self.logger.error("No last buy amount available for sell order; cannot calculate amount.")
                    return
            amount = custom_amount if custom_amount is not None else self.api.calculate_order_amount(order_type, limit)
            # Use the first ws client for non-parallel orders.
            ws_client = self.ws_manager.get_ws_client()
            order = ws_client.place_stop_limit_order_ws(order_type, trigger, limit, amount)
            if not order:
                self.logger.error("WebSocket order placement failed; falling back to API.")
                order = self.api.place_stop_limit_order(order_type, trigger, limit, custom_amount=custom_amount)
            if order:
                self.logger.info(f"New order placed: {order}")
                self.state.active = True
                self.state.order_type = order_type
                self.state.last_price = last_price
                # Store the client_order_id for future amendments.
                self.state.client_order_id = order.get('client_order_id')
                # Also store the exchange-generated order id for REST fallback amendments.
                self.state.order_id = order.get('id', None)
                return
            else:
                delay = base_delay * (2 ** retries)
                self.logger.error(f"Failed to place order; retrying in {delay:.2f} seconds...")
                time.sleep(delay)
                retries += 1

        self.logger.error("Unable to place new order after several retries.")

    def amend_current_order(self, get_market_price_func, calculate_prices_func):
        """
        Amend the current active order with new trigger, limit, and (if buy) amount values.
        """
        new_price = get_market_price_func()
        trigger, limit = calculate_prices_func(new_price, self.state.order_type)
        
        new_amount = None
        if self.state.order_type == 'buy':
            new_amount = self.api.calculate_order_amount('buy', limit)
        
        ws_client = self.ws_manager.get_ws_client()
        client_order_id = self.state.client_order_id
        order = ws_client.amend_order_ws(client_order_id, trigger, limit, new_amount=new_amount)
        
        if order:
            self.logger.info(f"Order amended successfully via WebSocket: {order}")
            self.state.last_price = new_price
        else:
            self.logger.error("WebSocket amendment failed; trying REST amendment as fallback.")
            order_id = self.state.order_id
            order = self.api.amend_stop_limit_order(order_id, self.state.order_type, trigger, limit, custom_amount=new_amount)
            if order:
                self.logger.info(f"Order amended successfully via REST fallback: {order}")
                self.state.last_price = new_price
            else:
                self.logger.error("REST amendment fallback also failed.")

    def monitor_active_order(self, get_market_price_func, calculate_prices_func):
        current_price = get_market_price_func()
        self.logger.debug(f"Monitoring active order. Current price: {current_price}, Order last price: {self.state.last_price}")
        if self.state.order_type == 'buy' and current_price < self.state.last_price:
            self.logger.info("Price dropped below last price; amending buy order.")
            self.amend_current_order(get_market_price_func, calculate_prices_func)
        elif self.state.order_type == 'sell' and current_price > self.state.last_price:
            self.logger.info("Price rose above last price; amending sell order.")
            self.amend_current_order(get_market_price_func, calculate_prices_func)
        else:
            self.logger.debug("No conditions met for order amendment.")

    def handle_order_execution(self, execution_event):
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
        self.state.order_type = 'sell' if self.state.order_type == 'buy' else 'buy'

    def recover_state(self):
        self.logger.info("Initiating state recovery...")
        max_retries = 3
        recovered = False
        preserved_order_type = self.state.order_type

        for attempt in range(max_retries):
            try:
                if self.state.client_order_id:
                    ws_client = self.ws_manager.get_ws_client()
                    if not ws_client.cancel_order_ws(self.state.client_order_id):
                        raise Exception(f"Failed to cancel order {self.state.client_order_id}")
                self.state.active = False
                self.state.order_type = preserved_order_type or 'buy'
                self.state.client_order_id = None
                recovered = True
                break
            except Exception as e:
                self.logger.error(f"Recovery attempt {attempt+1} failed: {str(e)}")
                time.sleep(2 ** attempt)

        if not recovered:
            self.logger.critical("State recovery failed after multiple attempts!")
        self.logger.info(f"State recovery completed. Resuming with order type: {self.state.order_type}")
