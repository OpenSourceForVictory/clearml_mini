"""Allowlisted systemd adapter; never accept remote unit names or shell commands."""
import re
import subprocess
import time
from worker.host_environments import user_environment


class Programs:
    def __init__(self, units, run=subprocess.run):
        if not units or len(units) > 64: raise ValueError('explicit Worker services required')
        if len({(x['scope'],x['name']) for x in units}) != len(units): raise ValueError('duplicate service')
        for unit in units:
            if set(unit) != {'scope','name'} or unit['scope'] not in ('user','system') or not re.fullmatch(r'mini-eval-worker[\w@.\-]*\.service',unit['name']):
                raise ValueError('unapproved Worker service')
        self.units, self.run = units, run
        self.next_start = {}

    def command(self, unit, action):
        if action not in ('is-active','start','stop'): raise ValueError('invalid service action')
        return self.run(['systemctl']+(['--user'] if unit['scope']=='user' else [])+[action,unit['name']],
            env=user_environment(), capture_output=True, text=True, timeout=8)

    def inspect(self):
        result=[]
        for unit in self.units:
            try:
                value=self.command(unit,'is-active').stdout.strip()
                if value not in ('active','inactive','failed','activating','deactivating'):value='unknown'
            except Exception:value='unknown'
            result.append({'name':unit['name'],'state':value})
        return result

    def reconcile(self, target, event):
        ready=True
        for unit, state in zip(self.units,self.inspect()):
            desired='active' if target=='running' else 'inactive'
            if state['state']==desired:continue
            if state['state'] in ('unknown','activating','deactivating'):
                ready=False;continue
            action='start' if target=='running' else 'stop'
            if action=='start':
                if time.monotonic()<self.next_start.get(unit['name'],0):
                    ready=False;continue
                self.next_start[unit['name']]=time.monotonic()+30
            try:
                if self.command(unit,action).returncode:raise RuntimeError('service action failed')
                event('program_started' if action=='start' else 'program_stopped')
            except Exception:
                event('program_failed');ready=False
        actual=self.inspect()
        return ready and all(x['state']==('active' if target=='running' else 'inactive') for x in actual)
