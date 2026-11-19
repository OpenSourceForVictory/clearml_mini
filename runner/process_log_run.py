"""Capture a real business process without coupling its status to diagnostics."""

import os
import selectors
import subprocess
import sys
import time
from .process_log_capture import RecordEncoder, BoundedWriter
from .process_log_store import Store


def run(command, *, config, scope, episode=0, cwd=None, env=None, on_line=None):
    settings = config.get("_process_logs")
    if not settings:
        raise ValueError("capture requires negotiated process logs")
    store = None
    writer = None
    encoder = RecordEncoder()
    incomplete = False
    try:
        store = Store(settings, scope, episode)
        writer = BoundedWriter(store.write)
    except (OSError, ValueError, KeyError):
        print(
            "process log unavailable; business execution continues",
            file=sys.stderr,
            flush=True,
        )
    tail = b""
    discard = False
    try:
        child = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except BaseException:
        if writer:
            state = writer.close()
            state["incomplete"] = True
            try:
                store.close(state)
            except (OSError, ValueError):
                pass
        raise
    selector = selectors.DefaultSelector()
    for stream in ("stdout", "stderr"):
        selector.register(getattr(child, stream), selectors.EVENT_READ, stream)
    ended = None
    try:
        while selector.get_map():
            if child.poll() is not None:
                ended = ended or time.monotonic()
                if time.monotonic() - ended >= 2:
                    incomplete = True
                    break
            for key, _ in selector.select(0.1):
                data = os.read(key.fileobj.fileno(), 4096)
                stream = key.data
                for row in encoder.feed(stream, data, final=not data):
                    if writer:
                        writer.offer(row)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                if stream == "stdout" and on_line:
                    for piece in data.splitlines(keepends=True):
                        if not discard:
                            tail += piece
                            if len(tail) > 65536:
                                tail = b""
                                discard = True
                        if piece.endswith(b"\n"):
                            if not discard:
                                on_line(tail.decode("utf-8", errors="replace"))
                            else:
                                print(
                                    "[oversized stdout line retained only in diagnostic log]",
                                    flush=True,
                                )
                            tail = b""
                            discard = False
                else:
                    target = sys.stdout if stream == "stdout" else sys.stderr
                    if hasattr(target, "buffer"):
                        target.buffer.write(data)
                        target.buffer.flush()
                    else:
                        target.write(data.decode("utf-8", errors="replace"))
                        target.flush()
        if tail and not discard and on_line:
            on_line(tail.decode("utf-8", errors="replace"))
        return child.wait()
    finally:
        selector.close()
        child.stdout.close()
        child.stderr.close()
        if child.poll() is None:
            child.kill()
            child.wait()
        if writer:
            state = writer.close()
            state["incomplete"] = state["incomplete"] or incomplete
            try:
                store.close(state)
            except (OSError, ValueError, KeyError):
                print("process log close incomplete", file=sys.stderr, flush=True)
