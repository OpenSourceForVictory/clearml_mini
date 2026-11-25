"""Retry metadata and interruptible delays; HTTP calls themselves are never replayed here."""
import math
import random
import time
import urllib.error
from email.utils import parsedate_to_datetime


class APIError(Exception):
    def __init__(self, status, message, retry_after=None):
        self.status = status
        self.retry_after = parse_retry_after(retry_after)
        super().__init__(f'API {status}: {message}')


def parse_retry_after(value, now=None):
    """Accept HTTP delay-seconds or an HTTP date; discard malformed hints."""
    if value is None:
        return None
    value = str(value).strip()
    try:
        if value.isascii() and value.isdecimal():
            seconds = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = max(0, date.timestamp() - (time.time() if now is None else now))
        return seconds if math.isfinite(seconds) else None
    except (ValueError, TypeError, OverflowError):
        return None


def transient(error):
    # Do not reinterpret an arbitrary HTTP error (notably 401/409) as a
    # transport error merely because HTTPError inherits URLError.
    if isinstance(error, (APIError, urllib.error.HTTPError)):
        return (error.status if isinstance(error, APIError) else error.code) in (429, 502, 503, 504)
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))


class Backoff:
    """One caller-owned retry series, bounded by cancellation and current lease.

    wait returns False at cancellation/deadline. The owner must then stop retrying
    and apply its lifecycle rules. It must renew the lease between attempts or
    have an independent renewal thread; waiting never renews a lease.
    """
    def __init__(self, base=.25, maximum=2.0, max_total=None):
        if not (math.isfinite(base) and math.isfinite(maximum) and 0 < base <= maximum):
            raise ValueError('invalid backoff bounds')
        if max_total is not None and (not math.isfinite(max_total) or max_total <= 0):
            raise ValueError('invalid total backoff bound')
        self.base, self.maximum, self.max_total = base, maximum, max_total
        self.attempt = 0

    def reset(self):
        self.attempt = 0

    def wait(self, error, stop, deadline=None):
        if not transient(error):
            raise error
        if stop.is_set() or (deadline is not None and time.monotonic() >= deadline):
            return False
        ceiling = min(self.maximum, self.base * 2 ** min(self.attempt, 16))
        self.attempt = min(self.attempt + 1, 16)
        hint = getattr(error, 'retry_after', None)
        # Keep jitter after Retry-After; taking max(jitter, hint) synchronizes
        # every early retry when the server returns the same one-second hint.
        # A caller that caps the whole sleep must retain the server minimum
        # separately (RetryWindow) before retrying that same endpoint.
        delay = (hint or 0) + random.uniform(ceiling / 2, ceiling)
        if self.max_total is not None:
            delay = min(delay, self.max_total)
        if deadline is not None:
            delay = min(delay, max(0, deadline - time.monotonic()))
        if stop.wait(delay):
            return False
        return deadline is None or time.monotonic() < deadline


class RetryWindow:
    """Remember a server retry floor while a caller does other useful work.

    A short Backoff(max_total=...) sleep must not cause the next request to
    violate a longer Retry-After. The caller checks remaining() before that
    same request; heartbeats and local supervision can continue meanwhile.
    """
    def __init__(self):
        self.not_before = 0.0

    def defer(self, error):
        hint = getattr(error, 'retry_after', None)
        if transient(error) and hint is not None and hint > 0:
            self.not_before = max(self.not_before,
                                  time.monotonic() + hint + random.uniform(.125, .25))

    def remaining(self):
        return max(0.0, self.not_before - time.monotonic())

    def reset(self):
        self.not_before = 0.0
