"""Launch upstream CLI with bounded WebSocket receives, without changing upstream."""
import os
from pathlib import Path
import runpy
import sys


def main():
    import websockets.sync.client
    original = websockets.sync.client.connect
    timeout = float(os.environ['COSMOS_REQUEST_TIMEOUT'])
    class TimedConnection:
        def __init__(self, connection): self.connection = connection
        def recv(self, *args, **kwargs):
            kwargs.setdefault('timeout', timeout)
            return self.connection.recv(*args, **kwargs)
        def __getattr__(self, name): return getattr(self.connection, name)
    def connect(*args, **kwargs):
        kwargs.setdefault('open_timeout', timeout)
        kwargs.setdefault('close_timeout', min(timeout, 5))
        return TimedConnection(original(*args, **kwargs))
    websockets.sync.client.connect = connect
    entry = Path(sys.argv[1]).resolve()
    sys.argv = [str(entry), *sys.argv[2:]]
    runpy.run_path(str(entry), run_name='__main__')


if __name__ == '__main__': main()
