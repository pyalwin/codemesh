def format_currency(amount: float) -> str:
    return f"${amount:.2f}"


class Logger:
    def __init__(self, name: str):
        self.name = name

    def log(self, message: str) -> None:
        print(f"[{self.name}] {message}")
