"""Persistable execution-control clock for the negotiated recovery protocol.

No wall clock or HTTP transport lives here. Callers must persist each returned
snapshot before acknowledging local state changes. This is not enabled by the
legacy lease protocol.
"""
import math


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid monotonic timestamp')
    return value


def _sequence(value):
    if type(value) is not int or value < 0:
        raise ValueError('invalid control sequence')
    return value


class DisconnectionClock:
    def __init__(self, execution_id, generation, boot_id, tolerance, requested_at):
        if not all(isinstance(v, str) and v for v in (execution_id, generation, boot_id)):
            raise ValueError('execution, generation and boot identity required')
        if tolerance is not None and (type(tolerance) is not int or tolerance <= 0):
            raise ValueError('disconnect tolerance must be positive seconds or null')
        self.execution_id, self.generation, self.boot_id = execution_id, generation, boot_id
        self.tolerance = tolerance
        self.confirmed_at = _time(requested_at)
        self.sequence = 0
        self.stop_reason = None

    def snapshot(self):
        return {'version': 1, 'execution_id': self.execution_id,
                'generation': self.generation, 'boot_id': self.boot_id,
                'disconnect_tolerance_seconds': self.tolerance,
                'confirmed_at': self.confirmed_at, 'sequence': self.sequence,
                'stop_reason': self.stop_reason}

    @classmethod
    def restore(cls, record, *, execution_id, generation, boot_id):
        if type(record.get('version')) is not int or record['version'] != 1:
            raise ValueError('unsupported disconnection clock version')
        if (record['execution_id'], record['generation']) != (execution_id, generation):
            raise ValueError('disconnection clock belongs to another execution')
        clock = cls(execution_id, generation, record['boot_id'],
                    record['disconnect_tolerance_seconds'], record['confirmed_at'])
        clock.sequence = _sequence(record['sequence'])
        if record['stop_reason'] not in (None, 'disconnection_timeout', 'node_restarted'):
            raise ValueError('invalid persisted stop reason')
        clock.stop_reason = record['stop_reason']
        if clock.boot_id != boot_id:
            clock.stop_reason = clock.stop_reason or 'node_restarted'
        return clock

    def check(self, now):
        now = _time(now)
        if self.stop_reason:
            return self.stop_reason
        if now < self.confirmed_at:
            raise ValueError('monotonic clock moved backwards')
        if self.tolerance is not None and now >= self.confirmed_at + self.tolerance:
            self.stop_reason = 'disconnection_timeout'
        return self.stop_reason

    def confirm(self, *, execution_id, generation, sequence, requested_at, received_at):
        requested_at, received_at = _time(requested_at), _time(received_at)
        sequence = _sequence(sequence)
        if requested_at > received_at:
            raise ValueError('control response precedes request')
        if (execution_id, generation) != (self.execution_id, self.generation):
            return False
        if self.check(received_at):
            return False  # A late ACK never reverses a local stop decision.
        if sequence <= self.sequence or requested_at < self.confirmed_at:
            return False
        self.sequence, self.confirmed_at = sequence, requested_at
        return True
