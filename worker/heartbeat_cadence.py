"""Base liveness cadence; never governs execution leases or task polling."""
import copy
import math
import random
import time


class HeartbeatCadence:
    fields = ('software', 'storage', 'runtime', 'host')

    def __init__(self, config, *, clock=None, jitter=None):
        self.interval = self._seconds(config, 'worker_heartbeat_seconds', 5, 2, 10)
        self.report_interval = self._seconds(config, 'worker_report_seconds', 20, 10, 30)
        self.clock, self.jitter = clock or time.monotonic, jitter or random.uniform
        self.next_at = 0
        self.full_at = 0
        self.signature = None

    @staticmethod
    def _seconds(config, key, default, minimum, maximum):
        value = config.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f'{key} must be a finite number between {minimum} and {maximum}')
        return value

    def _signature(self, body):
        result = {key: copy.deepcopy(body[key]) for key in self.fields if key in body}
        # Capacity fluctuates with disk use; it is still refreshed in full reports.
        if isinstance(result.get('storage'), dict):
            result['storage'].pop('capacity', None)
        # Stale/failed local sampling must invalidate resources promptly.
        result['telemetry_failed'] = body.get('telemetry_failed', False)
        return result

    def prepare(self, body):
        at = self.clock()
        changed = self.signature != self._signature(body)
        if not changed and at < self.next_at:
            return None
        # Coalesce periodic full reports with the next base heartbeat. A full
        # deadline must not create a second request just after a lightweight one.
        full = changed or at >= self.full_at
        return copy.deepcopy(body if full else {key: value for key, value in body.items() if key not in self.fields})

    def accepted(self, original):
        at = self.clock()
        # Only successful HTTP responses advance receipt state.
        if self.signature != self._signature(original) or at >= self.full_at:
            self.signature = self._signature(original)
            self.full_at = at + self.report_interval
        self.next_at = at + self.interval * self.jitter(.9, 1.1)

    def delay(self):
        return max(0, min(2, self.next_at - self.clock()))
