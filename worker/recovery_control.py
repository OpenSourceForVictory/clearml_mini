"""Validate/persist a control reply before granting local tracking authority.

Called by the negotiated supervisor, not the legacy heartbeat loop. Transport
receipts are deliberately not control replies. Persistence failure is fatal to
this decision; the caller must not act on an uncommitted acknowledgement.
"""
from .disconnection import DisconnectionClock


class RecoveryControl:
    def __init__(self, clock, directive='observe', reason=None):
        if directive not in ('observe', 'track', 'stop', 'inspect', 'retain'):
            raise ValueError('invalid recovery directive')
        self.clock, self.directive, self.reason = clock, directive, reason

    def snapshot(self):
        return {'clock': self.clock.snapshot(), 'directive': self.directive, 'reason': self.reason}

    @classmethod
    def restore(cls, record, *, execution_id, generation, boot_id):
        clock = DisconnectionClock.restore(record['clock'], execution_id=execution_id,
                                          generation=generation, boot_id=boot_id)
        return cls(clock, record['directive'], record['reason'])

    def apply(self, reply, *, sequence, requested_at, received_at, persist):
        if not isinstance(reply, dict) or reply.get('action') not in ('track', 'stop', 'inspect', 'retain'):
            raise ValueError('not a recovery control response')
        if (type(sequence) is not int or sequence <= 0 or type(reply.get('sequence')) is not int
                or reply['sequence'] != sequence
                or reply.get('execution_id') != self.clock.execution_id
                or reply.get('generation') != self.clock.generation
                or type(reply.get('duplicate')) is not bool):
            raise ValueError('recovery response identity or sequence mismatch')
        clock = DisconnectionClock.restore(self.clock.snapshot(), execution_id=self.clock.execution_id,
                                          generation=self.clock.generation, boot_id=self.clock.boot_id)
        directive, reason = self.directive, self.reason
        # Local cutoff cannot be reversed by a late tracking response. Existing
        # stop, process completion or identity conflict also cannot be reversed.
        local_stop = clock.check(received_at)
        if local_stop:
            directive, reason = 'stop', local_stop
        elif directive in ('stop', 'inspect', 'retain'):
            return False
        elif reply['action'] == 'track':
            if reply['duplicate'] or not clock.confirm(execution_id=self.clock.execution_id,
                    generation=self.clock.generation, sequence=sequence,
                    requested_at=requested_at, received_at=received_at):
                return False
            directive, reason = 'track', None
        else:
            directive, reason = reply['action'], reply.get('reason')
        candidate = {'clock':clock.snapshot(), 'directive':directive, 'reason':reason}
        persist(candidate)
        self.clock, self.directive, self.reason = clock, directive, reason
        return True
