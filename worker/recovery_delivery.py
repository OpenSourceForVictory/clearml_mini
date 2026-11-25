"""One bounded delivery pass; supervision must never wait for this queue.

Only the negotiated recovery execution path may call this helper. A receipt ACK
is not permission to resume a process, refresh its clock or discard its journal.
"""
from .reliable_outbox import OutboxConflict


class RecoveryDelivery:
    def __init__(self, api, outbox, *, apply_results=False):
        self.api, self.outbox = api, outbox
        self.apply_results = apply_results

    def step(self, limit=16):
        delivered = 0
        for record in self.outbox.pending(limit):
            kind = record['kind']
            endpoint = 'control' if kind == 'control' else 'results'
            body = {'generation': record['generation'], 'sequence': record['sequence'],
                    'body': record['body']}
            if kind != 'control':
                body['kind'] = kind
                if self.apply_results:
                    body['apply'] = True
            response = self.api.post(
                f"worker/executions/{record['execution_id']}/recovery/{endpoint}", body)
            if not isinstance(response, dict) or response.get('digest') != record['digest']:
                raise OutboxConflict('recovery receipt ACK does not match submitted data')
            if kind != 'control' and self.apply_results and response.get('reconciled') is not True:
                raise OutboxConflict('recovery result has not been applied')
            self.outbox.acknowledge(record['execution_id'], record['generation'], kind,
                                    record['sequence'], record['digest'])
            delivered += 1
        return delivered
