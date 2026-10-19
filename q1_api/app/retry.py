"""HTTP POST with explicit timeouts and bounded retries, used for OCR and account opening.

- 2xx: returned to the caller.
- 4xx: PermanentError, not retried. The service rejected this exact request, so
  sending it again cannot change the answer. Examples are the OCR 422 for an
  unreadable scan, and a 400, which a request built to the contract never gets.
- 5xx, timeout, or connection failure: retried after an exponential backoff,
  then RetriesExhausted once max_attempts is used up.
"""
import logging
import random
import time

import httpx

from app import config

log = logging.getLogger(__name__)


class PermanentError(Exception):
    def __init__(self, status_code: int, reason: str, attempts: int):
        super().__init__(f"HTTP {status_code}: {reason}")
        self.status_code, self.reason, self.attempts = status_code, reason, attempts


class RetriesExhausted(Exception):
    def __init__(self, last_error: str, attempts: int):
        super().__init__(f"no result after {attempts} attempts; last error: {last_error}")
        self.attempts = attempts


def service_reason(response: httpx.Response) -> str:
    """The `error` message that both services put in their JSON error bodies."""
    try:
        return str(response.json()["error"])
    except (ValueError, KeyError, TypeError):
        return response.text


def backoff_seconds(attempt: int) -> float:
    delay = min(config.RETRY_BACKOFF_MAX_SECONDS, config.RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
    return delay * random.uniform(0.5, 1.0)  # jitter, so parallel workers do not retry in step


def post_with_retries(
    client: httpx.Client, url: str, *, max_attempts: int, label: str, **request
) -> tuple[httpx.Response, int]:
    """POST until a 2xx response; return it with the number of attempts it took."""
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post(url, **request)
        except httpx.ConnectTimeout:  # unreachable service, not a slow one
            error = f"could not connect within {client.timeout.connect:g} s"
        except httpx.ReadTimeout:
            error = f"no response within {client.timeout.read:g} s"
        except httpx.TransportError as exc:  # refused or dropped connection, write or pool timeout
            error = f"request failed: {exc!r}"
        else:
            if response.is_success:
                return response, attempt
            if response.is_client_error:
                raise PermanentError(response.status_code, service_reason(response), attempt)
            error = f"HTTP {response.status_code}: {service_reason(response)}"

        if attempt < max_attempts:
            delay = backoff_seconds(attempt)
            log.warning("%s: attempt %d/%d failed (%s); retrying in %.1f s", label, attempt, max_attempts, error, delay)
            time.sleep(delay)
        else:
            log.warning("%s: attempt %d/%d failed (%s); giving up", label, attempt, max_attempts, error)
    raise RetriesExhausted(error, max_attempts)
