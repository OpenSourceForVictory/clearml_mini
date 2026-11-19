"""Bounded diagnostic capture primitives; no protocol parsing or quota ownership.

The caller supplies a per-execution bounded sink. Production capture uses
process_log_run/process_log_store; no node-wide quota channel is enabled.
"""

import codecs
from collections import deque
from datetime import datetime, timezone
import json
import threading
from .process_log_contract import PAGE_BYTES, RECORD_BYTES


def encoded(value):
    return (
        json.dumps(
            value, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode()
        + b"\n"
    )


class RecordEncoder:
    def __init__(self, sequence=0):
        if type(sequence) is not int or not 0 <= sequence < 2**63:
            raise ValueError("invalid sequence")
        self.sequence = sequence
        self.decoders = {
            s: codecs.getincrementaldecoder("utf-8")("replace")
            for s in ("stdout", "stderr", "system")
        }

    def feed(self, stream, data, final=False):
        if (
            stream not in self.decoders
            or not isinstance(data, bytes)
            or len(data) > PAGE_BYTES
        ):
            raise ValueError("invalid or oversized output block")
        text = self.decoders[stream].decode(data, final=final)
        # Each character expands to at most 12 ASCII bytes (surrogate pair).
        for begin in range(0, len(text), 512):
            if self.sequence >= 2**63 - 1:
                raise ValueError("sequence exhausted")
            row = {
                "sequence": self.sequence,
                "time": datetime.now(timezone.utc).isoformat(),
                "stream": stream,
                "text": text[begin : begin + 512],
                "gap": False,
            }
            value = encoded(row)
            if len(value) > RECORD_BYTES:
                raise ValueError("record exceeds format bound")
            self.sequence += 1
            yield value


class BoundedWriter:
    """Nonblocking producer, bounded bytes, conservative accounting on sink failure.

    Own one instance per business process. Callback failures affect diagnostics only.
    A permanently stuck filesystem call may keep a daemon thread alive, but close()
    never joins it indefinitely. No retries can duplicate an uncertain write.
    """

    def __init__(self, sink, queue_bytes=1024**2, tail_bytes=8192):
        if (
            type(queue_bytes) is not int
            or queue_bytes < RECORD_BYTES
            or type(tail_bytes) is not int
            or not 0 <= tail_bytes <= 8192
        ):
            raise ValueError("invalid capture bounds")
        self.sink = sink
        self.capacity = queue_bytes
        self.tail_limit = tail_bytes
        self.queue = deque()
        self.bytes = 0
        self.inflight = 0
        self.dropped_bytes = 0
        self.stderr_tail = b""
        self.error = None
        self.stopping = False
        self.cv = threading.Condition()
        self.thread = threading.Thread(
            target=self._loop, name="process-log-writer", daemon=True
        )
        self.thread.start()

    def offer(self, record):
        if not isinstance(record, bytes) or len(record) > RECORD_BYTES:
            raise ValueError("invalid record block")
        row = json.loads(record)
        if (
            not isinstance(row, dict)
            or row.get("stream") not in ("stdout", "stderr", "system")
            or not isinstance(row.get("text"), str)
        ):
            raise ValueError("invalid log record")
        with self.cv:
            if row["stream"] == "stderr" and self.tail_limit:
                self.stderr_tail = (
                    self.stderr_tail + row["text"].encode("utf-8", errors="replace")
                )[-self.tail_limit :]
            if self.stopping or self.error or self.bytes + len(record) > self.capacity:
                self.dropped_bytes += len(record)
                return False
            self.queue.append(record)
            self.bytes += len(record)
            self.cv.notify()
            return True

    def _loop(self):
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.queue or self.stopping)
                if not self.queue:
                    return
                data = self.queue.popleft()
                self.inflight = len(data)
            try:
                self.sink(data)
            except Exception:
                # Do not expose arbitrary sink exception text (paths/credentials).
                with self.cv:
                    self.error = "write_failed"
                    self.dropped_bytes += self.bytes
                    self.queue.clear()
                    self.bytes = 0
                    self.inflight = 0
                    self.cv.notify_all()
                return
            with self.cv:
                self.bytes -= len(data)
                self.inflight = 0
                self.cv.notify_all()

    def snapshot(self):
        with self.cv:
            return dict(
                buffered_bytes=self.bytes,
                dropped_bytes=self.dropped_bytes,
                error=self.error,
                incomplete=bool(self.dropped_bytes or self.error or self.bytes),
                stderr_tail=self.stderr_tail.decode("utf-8", errors="replace"),
            )

    def close(self, timeout=2):
        if type(timeout) not in (int, float) or not 0 <= timeout <= 2:
            raise ValueError("invalid drain timeout")
        with self.cv:
            self.stopping = True
            self.cv.notify_all()
        self.thread.join(timeout)
        if self.thread.is_alive():
            with self.cv:
                self.error = self.error or "drain_timeout"
                # Queued bytes are definitely discarded; inflight remains uncertain.
                self.dropped_bytes += self.bytes - self.inflight
                self.queue.clear()
                self.bytes = self.inflight
        return self.snapshot()
