import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from src.errors import APIError

logger = logging.getLogger(__name__)


class RetryingHTTPClient:
    """Retry transient failures without exposing URLs, headers, or response bodies."""

    def __init__(
        self,
        client: httpx.Client,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client
        self.max_retries = max_retries
        self.sleep = sleep

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        delay = float(2**attempt)
        if response is not None and (value := response.headers.get("Retry-After")):
            try:
                delay = max(delay, float(value))
            except ValueError:
                try:
                    delay = max(
                        delay, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
                    )
                except (ValueError, TypeError, OverflowError):
                    pass
        return delay

    def request(
        self,
        method: str,
        url: str,
        *,
        service: str,
        allowed_statuses: tuple[int, ...] = (),
        **kwargs: Any,
    ) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            response = None
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                pass
            if response is not None:
                if response.status_code in allowed_statuses or response.is_success:
                    return response
                if response.status_code != 429 and response.status_code < 500:
                    raise APIError(service, response.status_code)
            if attempt == self.max_retries:
                raise APIError(service, response.status_code if response is not None else None)
            delay = self._retry_delay(response, attempt)
            logger.warning(
                "%s temporarily unavailable; retry %d/%d in %.1fs",
                service,
                attempt + 1,
                self.max_retries,
                delay,
            )
            self.sleep(delay)
        raise AssertionError("unreachable")

    def json(self, method: str, url: str, *, service: str, **kwargs: Any) -> Any:
        response = self.request(method, url, service=service, **kwargs)
        if response.status_code == 404:
            return None
        try:
            return response.json()
        except ValueError:
            # Includes DBLP returning an HTML anti-bot page with HTTP 200.
            raise APIError(service) from None
