class BotError(Exception):
    """An operational error whose message is safe to log."""


class APIError(BotError):
    def __init__(self, service: str, status_code: int | None = None):
        self.service = service
        self.status_code = status_code
        detail = f"HTTP {status_code}" if status_code is not None else "transport/response error"
        super().__init__(f"{service}: {detail}")


class SummaryError(BotError):
    pass
