"""Bounded host output capture. systemd owns lifetime and process-tree cleanup."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from .storage import atomic_json

LIMIT=262144

def main():
    config=json.loads(Path(sys.argv[1]).read_text());root=Path(sys.argv[1]).parent
    child=subprocess.Popen(config['command'],cwd=config['cwd'],env=config['environment'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    selector=selectors.DefaultSelector();selector.register(child.stdout,selectors.EVENT_READ,'stdout');selector.register(child.stderr,selectors.EVENT_READ,'stderr')
    handles={k:(root/(k+'.log')).open('wb') for k in ('stdout','stderr')};sizes={'stdout':0,'stderr':0};tail=b''
    try:
        while selector.get_map():
            for key,_ in selector.select(1):
                data=os.read(key.fileobj.fileno(),8192)
                if not data:selector.unregister(key.fileobj);continue
                name=key.data;chunk=data[:max(0,LIMIT-sizes[name])]
                handles[name].write(chunk);handles[name].flush();sizes[name]+=len(chunk)
                if name=='stdout':
                    tail+=data
                    while b'\n' in tail:
                        line,tail=tail.split(b'\n',1)
                        try:
                            value=json.loads(line)
                            if isinstance(value,dict) and isinstance(value.get('final'),dict):atomic_json(root/'final.json',value)
                        except (ValueError,UnicodeError):pass
                    if len(tail)>65536:tail=b''
        code=child.wait();atomic_json(root/'exit.json',{'code':code});return code
    finally:
        selector.close()
        for f in handles.values():f.close()
        child.stdout.close();child.stderr.close()

if __name__=='__main__':raise SystemExit(main())
