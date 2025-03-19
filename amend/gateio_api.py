import ccxt
import logging

class GateIOAPIClient:
    def __init__(self, config):
        # Load API and trading configuration from the provided config
        self.config = config['api']
        self.trading_config = config['trading']
        self.key = self.config['key']
        self.secret = self.config['secret']
        self.base_url = self.config['base_url']
        # Format the symbol from "BTC_USDT" to "BTC/USDT"
        self.symbol = self.trading_config['currency_pair'].replace("_", "/")
        
        # Initialize the ccxt Gate.io client with API credentials and rate limit enabled.
        self.exchange = ccxt.gateio({
            'apiKey': self.key,
            'secret': self.secret,
            'enableRateLimit': True,
        })
        self.logger = logging.getLogger("GateIOAPIClient")
        self.logger.info(f"Initialized API client for {self.symbol}")

    def get_open_orders(self):
        self.logger.debug("Fetching open orders...")
        try:
            open_orders = self.exchange.fetch_open_orders(self.symbol)
            self.logger.debug(f"Open orders: {open_orders}")
            return open_orders
        except Exception as e:
            self.logger.error(f"API Error (fetching open orders): {str(e)}")
            return []

    def cancel_order(self, order_id):
        self.logger.debug(f"Attempting to cancel order: {order_id}")
        try:
            self.exchange.cancel_order(order_id, self.symbol)
            self.logger.info(f"Cancelled order {order_id}.")
            return True
        except Exception as e:
            self.logger.error(f"Cancel Error for order {order_id}: {str(e)}")
            return False

    def calculate_order_amount(self, side, limit_price, custom_amount=None):
        """
        For buy orders, convert the fixed USDT value (configured in 'buy.fixed_usdt')
        to the corresponding base currency amount based on the current limit price.
        Each parallel instance uses the full fixed_usdt value.
        
        For sell orders, if a custom amount is provided (typically adjusted for fees or specific logic),
        it is used directly.
        """
        self.logger.debug(f"Calculating order amount for side: {side} at limit price: {limit_price}")
        try:
            if side == 'buy':
                fixed_usdt = self.trading_config['buy'].get('fixed_usdt')
                if fixed_usdt is None:
                    raise ValueError("fixed_usdt is not set in the configuration for buy orders.")
                # Convert fixed USDT to base currency amount using the current limit price.
                amount = float(fixed_usdt) / limit_price
                self.logger.debug(f"Calculated fixed buy amount (base currency): {amount}")
                return amount
            elif side == 'sell':
                if custom_amount is not None:
                    self.logger.debug(f"Using custom sell amount: {custom_amount}")
                    return custom_amount
                else:
                    raise ValueError("No custom sell amount provided for sell order.")
            else:
                raise ValueError("Invalid side specified")
        except Exception as e:
            self.logger.error(f"Order amount calculation error: {str(e)}")
            return 0

    def place_stop_limit_order(self, order_type, trigger_price, limit_price, custom_amount=None):
        """
        Place a stop-limit order on Gate.io with the provided parameters.
        If 'custom_amount' is not provided, the order amount is calculated using the fixed USDT amount.
        The order includes a 'timeInForce' parameter set to IOC if enabled in the configuration.
        """
        self.logger.info(f"Placing {order_type} stop-limit order with trigger: {trigger_price} and limit: {limit_price}")
        try:
            # Determine the order amount (if custom_amount is provided, use it; otherwise calculate)
            amount = custom_amount if custom_amount is not None else self.calculate_order_amount(order_type, limit_price)
            if amount <= 0:
                self.logger.error("Invalid order amount calculated; order will not be placed.")
                return None

            market = self.exchange.market(self.symbol)
            # Format the amount using the market's precision settings
            amount = self.exchange.amount_to_precision(self.symbol, amount)
            self.logger.debug(f"Formatted amount: {amount}")

            params = {
                'stopPrice': trigger_price,
                'type': 'limit',
                'price': limit_price,
                'amount': amount
            }

            # Add IOC (Immediate-Or-Cancel) if enabled in the config.
            if self.config.get('ioc', False):
                params['timeInForce'] = 'IOC'

            order = self.exchange.create_order(
                symbol=self.symbol,
                type='limit',
                side=order_type,
                amount=amount,
                price=limit_price,
                params=params
            )
            self.logger.info(f"Order placed: {order}")
            return order
        except Exception as e:
            self.logger.error(f"Order failed: {str(e)}")
            return None

    def place_market_order(self, order_type, amount):
        """
        Place a market order on Gate.io.
        """
        self.logger.info(f"Placing market {order_type} order for amount: {amount}")
        try:
            market = self.exchange.market(self.symbol)
            amount = self.exchange.amount_to_precision(self.symbol, amount)
            order = self.exchange.create_order(
                symbol=self.symbol,
                type='market',
                side=order_type,
                amount=amount
            )
            self.logger.info(f"Market order placed: {order}")
            return order
        except Exception as e:
            self.logger.error(f"Market order failed: {str(e)}")
            return None

    def amend_stop_limit_order(self, order_id, order_type, new_trigger, new_limit, custom_amount=None):
        """
        Amend a stop-limit order on Gate.io via REST as a fallback method.
        For buy orders, the new amount is recalculated based on the new limit price.
        For sell orders, the amount remains unchanged.
        
        According to Gate.io API v4 documentation, an order can be amended by sending a PATCH request to:
            /api/v4/spot/orders/{order_id}
        with a JSON payload containing:
            - price: new_limit
            - stopPrice: new_trigger
            - amount: (only for buy orders, if needed)
        """
        self.logger.info(f"Amending order {order_id} via REST with new trigger: {new_trigger} and new limit: {new_limit}")
        try:
            if order_type == 'buy':
                # For buy orders, calculate new amount if not provided
                amount = custom_amount if custom_amount is not None else self.calculate_order_amount('buy', new_limit)
            else:
                amount = None

            params = {
                "price": new_limit,
                "stopPrice": new_trigger,
            }
            if order_type == 'buy' and amount is not None:
                params["amount"] = amount

            # Build the URL using the order_id:
            url = f"{self.base_url}/spot/orders/{order_id}"
            # Send a PATCH request using ccxt's request method.
            order = self.exchange.request(url, "PATCH", params)
            self.logger.info(f"Order amended via REST: {order}")
            return order
        except Exception as e:
            self.logger.error(f"REST amendment failed for order {order_id}: {str(e)}")
            return None
