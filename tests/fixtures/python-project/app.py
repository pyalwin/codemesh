from utils import format_currency, Logger


def process_payment(amount: float) -> str:
    logger = Logger("payment")
    logger.log(f"Processing payment of {amount}")
    return format_currency(amount)
