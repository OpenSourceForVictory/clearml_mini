"""Node Agent coordinator. Worker leases remain independent of this control loop."""
import argparse
import fcntl
import json
from pathlib import Path
import signal
import threading
import uuid
from . import VERSION
from .state import State
from .transport import Transport, Superseded
from .monitor import Monitor
from .programs import Programs


class Agent:
    def __init__(self, config, transport=None, programs=None, monitor=None):
        self.config=dict(config)
        receipt=Path(__file__).resolve().parents[1]/'.worker-release.json'
        if receipt.exists():self.config['digest']=json.loads(receipt.read_text())['sha256']
        self.state=State(config['state_dir'])
        self.api=transport or Transport(config['api_url'],config['token'])
        self.programs=programs or Programs(config['services'])
        self.monitor=monitor or Monitor(config['state_dir'])
        self.boot_id=uuid.uuid4().hex;self.session=None;self.sequence=0
        self.status='waiting';self.stop=threading.Event();self.state.event('started')
        self.controller=None;self.program_status=[]
        self.desired=self.state.value.get('desired',{'heartbeat_seconds':5,'sample_seconds':15,'programs':'running'})

    def step(self):
        if not self.session:
            r=self.api.post('register',{'boot_id':self.boot_id,'version':VERSION,'digest':self.config.get('digest','')})
            self.session=r['session']
        self.sequence+=1
        r=self.api.post('heartbeat',{'session':self.session,'sequence':self.sequence,
            'applied_revision':self.state.value['revision'],'config_status':self.status,
            'samples':self.monitor.sample(self.desired['sample_seconds']),
            'programs':self.program_status,'events':list(self.state.value['events'])})
        self.state.acknowledge(r['event_ack'])
        if not self.controller or not self.controller.is_alive():
            self.controller=threading.Thread(target=self.coordinate,args=(r,),daemon=True)
            self.controller.start()

    def coordinate(self,r):
        try:self._coordinate(r)
        except Superseded:self.stop.set()
        except Exception:self.status='failed'

    def _coordinate(self,r):
        self.program_status=self.programs.inspect()
        desired=r['desired'];revision=r['revision']
        if self.stop.is_set():return
        if r['upgrade_blocked']:
            self.status='waiting';return
        if desired['programs']=='stopped':
            if r['busy']:
                self.status='waiting';return
            permit=self.api.post('stop-permit',{'session':self.session,'revision':revision})
            if not permit['allowed']:
                self.status='waiting';return
        # Same lock will be held by the independent updater across code replacement.
        with (self.state.root/'control.lock').open('a') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                self.status='waiting';return
            ready=self.programs.reconcile(desired['programs'],self.state.event)
        if not ready:
            self.status='failed';return
        if self.state.value.get('desired')!=desired or self.state.value['revision']!=revision:
            with self.state.mutex:
                self.state.value.update({'desired':desired,'revision':revision});self.state.event('config_applied')
        self.program_status=self.programs.inspect()
        self.desired=desired;self.status='applied'

    def run(self):
        try:
            while not self.stop.is_set():
                try:self.step()
                except Superseded:self.stop.set();break
                except Exception:pass  # Raw network/command errors may contain credentials.
                self.stop.wait(self.desired['heartbeat_seconds'])
        finally:
            if self.controller:self.controller.join(timeout=10)
            self.state.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);a=p.parse_args()
    agent=Agent(json.loads(a.config.read_text()))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:agent.stop.set())
    agent.run()


if __name__=='__main__':main()
