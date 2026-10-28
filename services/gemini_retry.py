"""Retry transient Google GenAI server responses with bounded backoff."""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from google.genai.errors import APIError

logger = logging.getLogger("utsavai.gemini")

T = TypeVar("T")
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


def generate_content_with_retry(
    generate_content: Callable[..., T],
    *,
    operation: str,
    **kwargs: object,
) -> T:
    """Retry temporary Gemini API failures, without repeating successful pipeline work."""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return generate_content(**kwargs)
        except APIError as exc:
            if exc.code not in _RETRYABLE_STATUS_CODES or attempt == _MAX_ATTEMPTS - 1:
                raise
            delay_seconds = 2**attempt
            logger.warning(
                "gemini_transient_error_retry",
                extra={
                    "operation": operation,
                    "status_code": exc.code,
                    "attempt": attempt + 1,
                    "delay_seconds": delay_seconds,
                },
            )
            time.sleep(delay_seconds)

    raise RuntimeError("Gemini retry loop ended unexpectedly.")
