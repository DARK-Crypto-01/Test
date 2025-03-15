# main.py
import logging
import sys
import yaml
from trading_strategy import TradingStrategy

def load_config():
    try:
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)
        logging.debug("Configuration loaded successfully.")
        return config
    except Exception as e:
        logging.critical(f"Config loading error: {str(e)}")
        sys.exit(1)

def setup_logging(log_config):
    handlers = []
    if log_config.get('enabled', True):
        handlers.append(logging.FileHandler(log_config.get('file', 'trading_bot.log')))
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=log_config.get('level', 'INFO'),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
    logging.info("Logging is set up.")

def main():
    config = load_config()
    setup_logging(config['logging'])
    logging.info("Starting trading bot")
    try:
        trader = TradingStrategy(config)
        trader.manage_strategy()
    except KeyboardInterrupt:
        logging.info("Stopped by user")
    except Exception as e:
        logging.critical(f"Fatal error: {str(e)}")
    finally:
        logging.info("Trading session ended")

if __name__ == "__main__":
    main()
