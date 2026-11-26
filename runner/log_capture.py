"""Bounded output capture shared by host supervision and injected container runners."""
import codecs
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import uuid

SLOTS = 256
BLOCK = 2048


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


class Ring:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.next = json.loads((self.root/'cursor.json').read_text())['next']
        except FileNotFoundError:
            self.next = 0
        self.generation = uuid.uuid4().hex
        try:
            self.stderr_tail = json.loads((self.root/'stderr-tail.json').read_text())['content']
        except FileNotFoundError:
            self.stderr_tail = ''
        if self.next:
            self.write('system', '[log collector restarted]\n', gap=True)

    def write(self, stream, content, gap=False):
        raw = content.encode('utf-8')
        if stream == 'stderr':
            self.stderr_tail = (self.stderr_tail.encode('utf-8')+raw)[-7168:].decode('utf-8', errors='ignore')
            atomic(self.root/'stderr-tail.json', {'content': self.stderr_tail})
        while raw or gap:
            # Leave any partial UTF-8 character for the next record.
            data = raw[:BLOCK].decode('utf-8', errors='ignore')
            size = len(data.encode('utf-8'))
            atomic(self.root/f'{self.next % SLOTS}.json',
                   {'offset': self.next, 'generation': self.generation,
                    'stream': stream, 'content': data, 'gap': gap})
            self.next += 1
            atomic(self.root/'cursor.json', {'next': self.next})
            raw = raw[size:]
            gap = False


def read_ring(root, offset):
    root = Path(root)
    try:
        end = json.loads((root/'cursor.json').read_text())['next']
    except FileNotFoundError:
        return None, offset
    start = max(offset, end-SLOTS)
    gap = start != offset
    # Bounded scan; concurrent rotation is represented as a gap, never old data.
    for index in range(start, end):
        try:
            row = json.loads((root/f'{index % SLOTS}.json').read_text())
        except FileNotFoundError:
            gap = True
            continue
        if row['offset'] != index:
            gap = True
            continue
        return {'content': row['content'], 'stream': row['stream'],
                'gap': gap or row['gap']}, index+1
    return None, end


class ProtocolCapture:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.tail = b''
        self.discard = False
        self.events = 0
        self.event_path = self.root/'events.jsonl'
        if self.event_path.exists():
            self.events = self.event_path.stat().st_size

    def feed(self, data):
        for piece in data.splitlines(keepends=True):
            if not self.discard:
                self.tail += piece
                if len(self.tail) > 65536:
                    self.tail = b''
                    self.discard = True
            if piece.endswith(b'\n'):
                if not self.discard:
                    self.line(self.tail)
                self.tail = b''
                self.discard = False

    def line(self, line):
        try:
            if line.startswith(b'MINI_EVAL_EVENT '):
                encoded = line[len(b'MINI_EVAL_EVENT '):]
                value = json.loads(encoded)
                if isinstance(value, dict):
                    # Separate event budget: at most 32 MiB, fail visibly instead
                    # of silently losing protocol events as diagnostic logs rotate.
                    if self.events+len(encoded) > 32*1024*1024:
                        raise RuntimeError('protocol event journal limit exceeded')
                    with self.event_path.open('ab') as f:
                        f.write(encoded)
                    self.events += len(encoded)
            else:
                value = json.loads(line)
                if isinstance(value, dict) and isinstance(value.get('final'), dict):
                    atomic(self.root/'final.json', value)
        except (ValueError, UnicodeError):
            pass


def capture(command, root, cwd=None, env=None, tee=False, ring=True):
    root = Path(root)
    logs = Ring(root/'logs') if ring else None
    protocol = ProtocolCapture(root)
    stderr_tail = ''
    diagnostic_error = protocol_error = False
    decoders = {name: codecs.getincrementaldecoder('utf-8')(errors='replace') for name in ('stdout','stderr')}
    with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
        selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
        try:
            while selector.get_map():
                for key, _ in selector.select(1):
                    data = os.read(key.fileobj.fileno(), BLOCK)
                    stream = key.data
                    if not data:
                        selector.unregister(key.fileobj)
                        remaining = decoders[stream].decode(b'', final=True)
                        if remaining and logs: logs.write(stream, remaining)
                        continue
                    if stream == 'stdout' and not protocol_error:
                        try:
                            protocol.feed(data)
                        except (OSError, RuntimeError) as exc:
                            # The child must keep draining even if its durable
                            # protocol source cannot be written. The missing
                            # result remains visible to result validation.
                            protocol_error = True
                            print(f'protocol capture unavailable: {type(exc).__name__}', file=sys.stderr)
                    if stream == 'stderr' and not ring:
                        stderr_tail = (stderr_tail+decoders[stream].decode(data)).encode('utf-8')[-7168:].decode('utf-8', errors='ignore')
                        if not diagnostic_error:
                            try: atomic(root/'stderr-tail.json', {'content': stderr_tail})
                            except OSError as exc:
                                diagnostic_error = True
                                print(f'diagnostic capture unavailable: {type(exc).__name__}', file=sys.stderr)
                    if logs and not diagnostic_error:
                        try: logs.write(stream, decoders[stream].decode(data))
                        except OSError as exc:
                            diagnostic_error = True
                            print(f'diagnostic capture unavailable: {type(exc).__name__}', file=sys.stderr)
                    if tee:
                        target = sys.stdout.buffer if stream == 'stdout' else sys.stderr.buffer
                        target.write(data); target.flush()
            if protocol.tail and not protocol.discard and not protocol_error:
                try: protocol.line(protocol.tail+b'\n')
                except (OSError, RuntimeError) as exc:
                    print(f'protocol capture unavailable: {type(exc).__name__}', file=sys.stderr)
            code = child.wait()
            try: atomic(root/'exit.json', {'code': code})
            except OSError as exc:
                print(f'exit record unavailable: {type(exc).__name__}', file=sys.stderr)
            return code
        finally:
            selector.close()
            if child.poll() is None:
                child.kill(); child.wait()


def main():
    # The Worker supplies this path; no changes to business argv or environment.
    root = sys.argv[1]
    command = sys.argv[2:]
    raise SystemExit(capture(command, root, tee=True, ring=False))


if __name__ == '__main__': main()
