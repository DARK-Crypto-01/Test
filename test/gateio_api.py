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

    def calculate_order_amount(self, side, limit_price):
        self.logger.debug(f"Calculating order amount for side: {side} at limit price: {limit_price}")
        balance = self.exchange.fetch_balance()
        base, quote = self.symbol.split('/')
    
        try:
            if side == 'buy':
                buy_pct = self.trading_config['buy']['amount_percentage']
                available_quote = balance[quote]['free']
                self.logger.debug(f"Available {quote}: {available_quote}")
                quote_amount = (available_quote * buy_pct) / 100
                amount = quote_amount / limit_price
                self.logger.debug(f"Calculated buy amount (base currency): {amount}")
                return amount
            elif side == 'sell':
                sell_pct = self.trading_config['sell']['amount_percentage']
                available_base = balance[base]['free']
                self.logger.debug(f"Available {base}: {available_base}")
                amount = (available_base * sell_pct) / 100
                self.logger.debug(f"Calculated sell amount (base currency): {amount}")
                return amount
            else:
                raise ValueError("Invalid side specified")
        except KeyError as e:
            self.logger.error(f"Balance check failed: {str(e)}")
            return 0
        except ZeroDivisionError:
            self.logger.error("Invalid zero price encountered")
            return 0

    def place_stop_limit_order(self, order_type, trigger_price, limit_price):
        self.logger.info(f"Placing {order_type} stop-limit order with trigger: {trigger_price} and limit: {limit_price}")
        try:
            amount = self.calculate_order_amount(order_type, limit_price)
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

            # Add IOC parameter if enabled in config
            if self.config.get('api', {}).get('ioc', False):
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
