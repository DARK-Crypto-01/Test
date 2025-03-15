import ccxt
import logging

class GateIOAPIClient:
    def __init__(self, config):
        self.config = config['api']
        self.trading_config = config['trading']
        self.key = self.config['key']
        self.secret = self.config['secret']
        self.base_url = self.config['base_url']
        self.symbol = self.trading_config['currency_pair'].replace("_", "/")
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

    def cancel_all_orders(self):
        self.logger.debug("Cancelling all orders...")
        canceled_orders = []
        open_orders = self.get_open_orders()
        for order in open_orders:
            if order.get('symbol') == self.symbol:
                order_id = order.get('id')
                if order_id and self.cancel_order(order_id):
                    canceled_orders.append(order_id)
        if canceled_orders:
            self.logger.info(f"All orders canceled for {self.symbol}: {canceled_orders}")
        else:
            self.logger.info(f"No open orders to cancel for {self.symbol}.")
        return canceled_orders

    def calculate_order_amount(self, side, limit_price, custom_amount=None):
        self.logger.debug(f"Calculating order amount for side: {side} at limit price: {limit_price}")
        try:
            if side == 'buy':
                fixed_usdt = self.trading_config['buy'].get('fixed_usdt')
                if fixed_usdt is None:
                    raise ValueError("fixed_usdt is not set in the configuration for buy orders.")
                # Convert fixed USDT to base currency amount using the current limit price
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
        self.logger.info(f"Placing {order_type} stop-limit order with trigger: {trigger_price} and limit: {limit_price}")
        try:
            amount = custom_amount if custom_amount is not None else self.calculate_order_amount(order_type, limit_price)
            if amount <= 0:
                self.logger.error("Invalid order amount calculated; order will not be placed.")
                return None

            market = self.exchange.market(self.symbol)
            precision = market['precision']['amount']
            amount = self.exchange.amount_to_precision(self.symbol, amount)
            self.logger.debug(f"Formatted amount: {amount}")

            params = {
                'stopPrice': trigger_price,
                'type': 'limit',
                'price': limit_price,
                'amount': amount
            }

            # Add IOC parameter if enabled in configuration
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
