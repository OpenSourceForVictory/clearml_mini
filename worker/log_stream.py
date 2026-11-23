"""Independent bounded diagnostic transport; never mutates the execution journal."""
import codecs
import json
import logging
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time
from runner.log_capture import Ring, read_ring, atomic, BLOCK
from .backpressure import Backoff, transient

LOG = logging.getLogger('mini-eval-worker')


class DockerCollector:
    def __init__(self, name, root):
        self.name, self.root = name, Path(root)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name='docker-log-capture')
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            ring = Ring(self.root)
            ring.write('system', '[Docker logs: bounded catch-up; earlier output may be unavailable]\n', gap=True)
            try:
                with subprocess.Popen(['docker','logs','--follow','--tail','200',self.name],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
                    selector=selectors.DefaultSelector()
                    decoders={k:codecs.getincrementaldecoder('utf-8')(errors='replace') for k in ('stdout','stderr')}
                    selector.register(child.stdout,selectors.EVENT_READ,'stdout')
                    selector.register(child.stderr,selectors.EVENT_READ,'stderr')
                    try:
                        while selector.get_map() and not self.stop.is_set():
                            for key,_ in selector.select(.2):
                                data=os.read(key.fileobj.fileno(),BLOCK)
                                if not data:
                                    selector.unregister(key.fileobj)
                                content=decoders[key.data].decode(data,final=not data)
                                if content:ring.write(key.data,content)
                        if self.stop.is_set() and child.poll() is None:child.terminate()
                        code=child.wait(timeout=2)
                        if code==0:return
                    finally:
                        selector.close()
                        if child.poll() is None:child.kill();child.wait()
            except Exception as exc:
                LOG.warning('log collector unavailable: %s',type(exc).__name__)
            if self.stop.wait(2):return

    def close(self):
        self.stop.set();self.thread.join(timeout=3)


class Sender:
    def __init__(self, api, execution, root, journal, stderr_source=None):
        self.api,self.execution,self.root,self.journal=api,execution,Path(root),Path(journal)
        self.stderr_source = Path(stderr_source) if stderr_source else self.root/'stderr-tail.json'
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        try:self.state=json.loads(self.journal.read_text())
        except FileNotFoundError:self.state={}
        if self.state.get('execution') != execution:
            self.state={'execution':execution,'offset':0,'sequence':0,'pending':None}
        self.stop=threading.Event();self.finishing=threading.Event();self.drained=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True,name='execution-log-upload')
        self.thread.start()

    def save(self):
        temporary=self.journal.with_suffix(self.journal.suffix+'.tmp')
        with temporary.open('w') as f:
            os.chmod(temporary,0o600)
            json.dump(self.state,f);f.flush();os.fsync(f.fileno())
        os.replace(temporary,self.journal)

    def prepare(self):
        offset=self.state['offset']
        skipped=False
        if self.finishing.is_set():
            tails={'stdout':'','stderr':'','system':''}
            cursor=offset
            while True:
                row,end=read_ring(self.root,cursor)
                if row is None:break
                stream=row['stream'];limit=1024 if stream=='system' else 7168
                data=(tails[stream]+row['content']).encode('utf-8')
                skipped=skipped or row['gap'] or len(data)>limit
                tails[stream]=data[-limit:].decode('utf-8',errors='ignore')
                cursor=end
            # Docker can deliver buffered stdout after the final stderr line.
            # Retain that diagnostic even when stdout evicts the shared ring.
            try:
                stderr_tail=json.loads(getattr(self,'stderr_source',self.root/'stderr-tail.json').read_text())['content']
            except FileNotFoundError:
                stderr_tail=''
            if cursor==offset and (not stderr_tail or self.state.get('final_stderr')==stderr_tail):return False
            if stderr_tail and stderr_tail != tails['stderr']:
                tails['stderr']=stderr_tail
                skipped=True
            self.state['final_stderr']=stderr_tail
            content=''.join('['+key+'] '+value+'\n' for key,value in tails.items() if value)
            self.state['pending']={'sequence':self.state['sequence'],'content':content,
                                   'stream':'system','gap':skipped}
            self.state['pending_offset']=cursor
            self.save();return True
        row,next_offset=read_ring(self.root,offset)
        if row is None:return False
        row['gap']=row['gap'] or skipped
        # Coalesce adjacent records of the same stream into one <=16 KiB upload.
        while len(row['content'].encode()) < 14336:
            following,end=read_ring(self.root,next_offset)
            if not following or following['stream']!=row['stream']:break
            size=len((row['content']+following['content']).encode())
            if size>16384:break
            row['content']+=following['content'];row['gap']|=following['gap'];next_offset=end
        self.state['pending']={'sequence':self.state['sequence'],**row}
        self.state['pending_offset']=next_offset
        self.save();return True

    def run(self):
        delay=2
        retry=Backoff()
        while not self.stop.is_set():
            try:
                if not self.state['pending'] and not self.prepare():
                    if self.finishing.is_set():self.drained.set()
                    self.stop.wait(.2);continue
                result=self.api.post('worker/executions/'+self.execution+'/logs-v2',self.state['pending'])
                if self.stop.is_set():return
                if result.get('resync'):
                    self.state['sequence']=result['next_sequence']
                    self.state['pending']['sequence']=result['next_sequence']
                    self.state['pending']['gap']=True
                else:
                    self.state['offset']=self.state.pop('pending_offset')
                    self.state['sequence']=result['next_sequence']
                    self.state['pending']=None
                self.save();delay=2;retry.reset()
            except Exception as exc:
                if getattr(exc,'status',None) in (400,401,403,404,409,413,422):
                    LOG.warning('execution log upload stopped: HTTP %s',exc.status)
                    return
                LOG.warning('execution log upload deferred: %s',type(exc).__name__)
                if transient(exc):
                    retry.wait(exc,self.stop)
                    continue
                delay=min(30,delay*2)
            self.stop.wait(delay)

    def finish(self, timeout=5):
        self.finishing.set()
        return self.drained.wait(timeout)

    def close(self):
        self.stop.set()
        # HTTP is bounded separately; the execution control path never waits
        # for a stuck transport. No execution journal writes occur here.
        self.thread.join(timeout=.1)
