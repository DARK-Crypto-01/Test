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
        self.max_retries = config['api'].get('max_ws_retries', 3)
        self.retry_interval = config['api'].get('ws_retry_initial_interval', 0.1)
        self.retry_multiplier = config['api'].get('ws_retry_multiplier', 2)
        
    def _should_skip_instance(self, instance_index):
        return instance_index in self.state.pending_actions

    def _generate_order_parameters(self, instance_index, get_market_price_func, 
                                  calculate_prices_func, order_type):
        current_price = get_market_price_func()
        trigger, limit = calculate_prices_func(current_price, order_type, instance_index)
        
        amount = None
        if order_type == 'sell':
            if self.state.last_buy_amount:
                fee_rate = self.config['trading'].get('sell_trading_fee', 0.001)
                raw_fee = self.state.last_buy_amount * fee_rate
                amount = self.state.last_buy_amount - round_up_to_one_significant(raw_fee)
        else:
            amount = self.api.calculate_order_amount(order_type, limit)
            
        return {
            'price': current_price,
            'trigger': trigger,
            'limit': limit,
            'amount': amount
        }

    def _ws_operation_with_retry(self, operation, instance_index, get_market_price_func,
                                calculate_prices_func, order_type, is_amendment=False,
                                previous_params=None):
        for attempt in range(self.max_retries + 1):
            try:
                params = self._generate_order_parameters(
                    instance_index, get_market_price_func,
                    calculate_prices_func, order_type
                )
                
                if is_amendment and previous_params:
                    params.update(previous_params)

                result = operation(**params)
                
                if order_type == 'buy' and params['amount'] <= 0:
                    raise ValueError("Invalid buy amount after recalculation")
                    
                return result, params
                
            except Exception as e:
                if attempt == self.max_retries:
                    raise
                sleep_time = self.retry_interval * (self.retry_multiplier ** attempt)
                self.logger.debug(f"Retry {attempt+1}/{self.max_retries} in {sleep_time:.2f}s")
                time.sleep(sleep_time)
                
        return None, None

    def _rest_fallback_operation(self, operation_name, instance_index, 
                                get_market_price_func, calculate_prices_func,
                                order_type, previous_order_id=None):
        try:
            params = self._generate_order_parameters(
                instance_index, get_market_price_func,
                calculate_prices_func, order_type
            )
            
            if operation_name == 'place':
                return self.api.place_stop_limit_order(
                    order_type, params['trigger'], params['limit'],
                    custom_amount=params['amount'] if order_type == 'sell' else None
                )
                
            elif operation_name == 'amend' and previous_order_id:
                return self.api.amend_stop_limit_order(
                    previous_order_id, order_type,
                    params['trigger'], params['limit'],
                    custom_amount=params['amount']
                )
                
        except Exception as e:
            self.logger.error(f"REST {operation_name} failed: {str(e)}")
            return None

    def place_new_orders(self, calculate_prices_func, get_market_price_func, total_instances):
        for instance_index in range(total_instances):
            if self._should_skip_instance(instance_index):
                continue

            self.state.pending_actions[instance_index] = True
            order_type = self.state.order_type or 'buy'
            
            try:
                ws_client = self.ws_manager.get_ws_client(instance_index)
                order, params = self._ws_operation_with_retry(
                    operation=lambda p: ws_client.place_stop_limit_order_ws(
                        order_type, p['trigger'], p['limit'], p['amount']
                    ),
                    instance_index=instance_index,
                    get_market_price_func=get_market_price_func,
                    calculate_prices_func=calculate_prices_func,
                    order_type=order_type
                )

                if not order:
                    order = self._rest_fallback_operation(
                        'place', instance_index, 
                        get_market_price_func,
                        calculate_prices_func,
                        order_type
                    )

                if order:
                    client_order_id = order.get('client_order_id')
                    # Update both mappings
                    self.state.active_orders[instance_index] = {
                        'order_id': order.get('id'),
                        'client_order_id': client_order_id,
                        'last_price': params['price'],
                        'limit_price': params['limit'],
                        'order_type': order_type,
                        'timestamp': time.time()
                    }
                    if client_order_id:
                        self.state.order_mapping[client_order_id] = instance_index
                    del self.state.pending_actions[instance_index]
                else:
                    self.logger.error(f"Permanent failure: {instance_index}")

            except Exception as e:
                self.logger.error(f"Final failure: {instance_index} - {str(e)}")

    def amend_order(self, instance_index, get_market_price_func, calculate_prices_func):
        self.state.pending_actions[instance_index] = True
        
        try:
            order_state = self.state.active_orders.get(instance_index)
            if not order_state:
                return

            ws_client = self.ws_manager.get_ws_client(instance_index)
            amendment, params = self._ws_operation_with_retry(
                operation=lambda p: ws_client.amend_order_ws(
                    order_state['client_order_id'],
                    p['trigger'], p['limit'], p['amount']
                ),
                instance_index=instance_index,
                get_market_price_func=get_market_price_func,
                calculate_prices_func=calculate_prices_func,
                order_type=order_state['order_type'],
                is_amendment=True,
                previous_params={
                    'client_order_id': order_state['client_order_id']
                }
            )

            if not amendment:
                amendment = self._rest_fallback_operation(
                    'amend', instance_index,
                    get_market_price_func,
                    calculate_prices_func,
                    order_state['order_type'],
                    previous_order_id=order_state['order_id']
                )

            if amendment:
                # Update active orders but preserve client_order_id
                order_state['last_price'] = params['price']
                order_state['limit_price'] = params['limit']
                order_state['timestamp'] = time.time()
                del self.state.pending_actions[instance_index]

        except Exception as e:
            self.logger.error(f"Permanent failure: {instance_index} - {str(e)}")

    def monitor_active_orders(self, get_market_price_func, calculate_prices_func):
        current_price = get_market_price_func()
        
        for instance_index, order_state in list(self.state.active_orders.items()):
            if instance_index in self.state.pending_actions:
                continue
                
            order_type = order_state['order_type']
            last_price = order_state['last_price']
            
            if ((order_type == 'buy' and current_price < last_price) or 
                (order_type == 'sell' and current_price > last_price)):
                self.amend_order(instance_index, get_market_price_func, calculate_prices_func)

    def handle_order_execution(self, order_id, event):
        client_order_id = event.get('client_order_id')
        instance_index = self.state.order_mapping.get(client_order_id)
        
        if not instance_index:
            # Fallback search
            for idx, state in self.state.active_orders.items():
                if state.get('client_order_id') == client_order_id:
                    instance_index = idx
                    break
        
        if instance_index is not None:
            if state := self.state.active_orders.get(instance_index):
                if state['order_type'] == 'buy':
                    self.state.last_buy_amount = float(event.get('filled', 0))
                # Clean both mappings
                del self.state.active_orders[instance_index]
                if client_order_id in self.state.order_mapping:
                    del self.state.order_mapping[client_order_id]

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
                client_order_id = order_state.get('client_order_id')
                ws_client = self.ws_manager.get_ws_client(instance_index)
                ws_client.cancel_order_ws(order_state['order_id'])
                
                # Clean both mappings
                if client_order_id in self.state.order_mapping:
                    del self.state.order_mapping[client_order_id]
                del self.state.active_orders[instance_index]
                
            except Exception as e:
                self.logger.error(f"Recovery error: {str(e)}")
            finally:
                self.state.pending_actions.pop(instance_index, None)

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
                client_order_id = state.get('client_order_id')
                ws_client = self.ws_manager.get_ws_client(idx)
                if ws_client.cancel_order_ws(state['order_id']):
                    amount = state.get('executed_amount')
                    if amount and amount > 0:
                        ws_client.place_market_order_ws('sell', amount)
            except Exception as e:
                self.logger.error(f"Shutdown error: {str(e)}")
            finally:
                if idx in self.state.active_orders:
                    # Clean both mappings
                    if client_order_id in self.state.order_mapping:
                        del self.state.order_mapping[client_order_id]
                    del self.state.active_orders[idx]
