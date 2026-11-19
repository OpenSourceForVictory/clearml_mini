"""Apply node log intent outside heartbeat threads; keep the last durable budget."""
import json
from pathlib import Path
from runner.process_log_contract import validate_policy
from .storage import atomic_json


class LogPolicy:
    def __init__(self,worker):
        self.worker=worker
        self.path=Path(str(worker.path)+'.log-policy')
        self.applied=None
        try:
            value=json.loads(self.path.read_text())
            validate_policy(value['policy'])
            if type(value['revision']) is not int or value['revision']<0:raise ValueError('invalid revision')
            self.applied=value
        except (OSError,ValueError,KeyError,TypeError):
            pass

    def sync(self):
        path=f'worker/slots/{self.worker.slot_id}/log-policy'
        desired=self.worker.api.post(path)
        try:
            policy=validate_policy(desired['policy'])
            revision=desired['revision']
            if type(revision) is not int or revision<0:raise ValueError('invalid revision')
            value={'revision':revision,'policy':policy}
            if value!=self.applied:
                atomic_json(self.path,value)
                self.applied=value
            self.worker.config['process_log_policy']={k:v for k,v in policy.items() if k!='node_bytes'}
            self.worker.config['process_log_policy_revision']=revision
        except (OSError,ValueError,KeyError,TypeError):
            if type(desired.get('revision')) is int and desired['revision']>=0:
                self.worker.api.post(path,{'revision':desired['revision'],'status':'failed','error':'log policy validation or persistence failed'})
            return
        self.worker.api.post(path,{'revision':revision,'status':'applied','policy':policy})
