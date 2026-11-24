"""One bounded delivery pass; supervision must never wait for this queue.

Only the negotiated recovery execution path may call this helper. A receipt ACK
is not permission to resume a process, refresh its clock or discard its journal.
"""
from .reliable_outbox import OutboxConflict


class RecoveryDelivery:
    def __init__(self, api, outbox):
        self.api, self.outbox = api, outbox

    def step(self, limit=16):
        delivered = 0
        for record in self.outbox.pending(limit):
            kind = record['kind']
            endpoint = 'control' if kind == 'control' else 'results'
            body = {'generation': record['generation'], 'sequence': record['sequence'],
                    'body': record['body']}
            if kind != 'control':
                body['kind'] = kind
            response = self.api.post(
                f"worker/executions/{record['execution_id']}/recovery/{endpoint}", body)
            if not isinstance(response, dict) or response.get('digest') != record['digest']:
                raise OutboxConflict('recovery receipt ACK does not match submitted data')
            self.outbox.acknowledge(record['execution_id'], record['generation'], kind,
                                    record['sequence'], record['digest'])
            delivered += 1
        return delivered
