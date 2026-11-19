"""Standalone primitives only: no production collector or quota channel enabled."""

import base64
from dataclasses import replace
import errno
import json
import os
import subprocess
import sys
import threading
from uuid import uuid4

import pytest
from runner.process_log_capture import RecordEncoder, BoundedWriter, encoded
from runner.process_log_contract import PAGE_BYTES, RECORD_BYTES
from worker.process_log_reader import registered_scope, read_page, Segment


def records(text="hello", stream="stdout", sequence=0):
    return list(RecordEncoder(sequence).feed(stream, text.encode(), final=True))


def test_encoding_split_utf8_invalid_and_no_newline():
    encoder = RecordEncoder()
    assert list(encoder.feed("stdout", b"\xf0\x9f")) == []
    rows = list(encoder.feed("stderr", b"error\xff", final=True))
    rows += list(encoder.feed("stdout", b"\x98\x80" + b"a" * 20000, final=True))
    assert [json.loads(r)["sequence"] for r in rows] == list(range(len(rows)))
    assert json.loads(rows[0])["text"] == "error\ufffd"
    assert "".join(json.loads(r)["text"] for r in rows[1:]) == "😀" + "a" * 20000
    worst = records("😀" * 16000)
    assert all(len(r) <= RECORD_BYTES for r in worst)
    assert all(json.loads(r)["time"] and json.loads(r)["gap"] is False for r in worst)


@pytest.mark.parametrize("value", [-1, True, 2**63])
def test_encoder_invalid_sequence(value):
    with pytest.raises(ValueError):
        RecordEncoder(value)


def test_encoder_bounds():
    with pytest.raises(ValueError):
        list(RecordEncoder().feed("bad", b""))
    with pytest.raises(ValueError):
        list(RecordEncoder().feed("stdout", b"a" * (PAGE_BYTES + 1)))
    with pytest.raises(ValueError):
        list(RecordEncoder(2**63 - 1).feed("stdout", b"a"))


def test_writer_order_tail_and_close():
    output = []
    writer = BoundedWriter(output.append, tail_bytes=3)
    rows = records("abcd", "stderr") + records("next")
    for row in rows:
        assert writer.offer(row)
    state = writer.close()
    assert output == rows
    assert state == dict(
        buffered_bytes=0,
        dropped_bytes=0,
        error=None,
        incomplete=False,
        stderr_tail="bcd",
    )
    assert not writer.offer(rows[0])
    assert writer.snapshot()["incomplete"]


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EROFS, errno.EACCES])
def test_sink_failure_keeps_draining_real_child_and_exit(code):
    def fail(_):
        raise OSError(code, "private path must not leak")

    writer = BoundedWriter(fail)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys;sys.stdout.write('x'*200000);sys.exit(7)"],
        stdout=subprocess.PIPE,
    )
    encoder = RecordEncoder()
    try:
        while block := child.stdout.read(4096):
            for row in encoder.feed("stdout", block):
                writer.offer(row)
        assert child.wait(timeout=5) == 7
    finally:
        child.stdout.close()
        if child.poll() is None:
            child.kill()
            child.wait()
    state = writer.close()
    assert state["error"] == "write_failed" and state["dropped_bytes"] > 0
    assert state["buffered_bytes"] == 0 and state["incomplete"]


def test_slow_sink_queue_overflow_and_bounded_drain():
    started = threading.Event()
    release = threading.Event()

    def slow(_):
        started.set()
        release.wait(5)

    writer = BoundedWriter(slow, queue_bytes=RECORD_BYTES)
    row = records("😀" * 512, "stderr")[0]
    try:
        assert writer.offer(row)
        assert started.wait(2)
        assert not writer.offer(row)
        assert writer.snapshot()["buffered_bytes"] <= RECORD_BYTES
        state = writer.close(timeout=0)
        assert state["error"] == "drain_timeout" and state["incomplete"]
        assert state["dropped_bytes"] == len(row)
    finally:
        release.set()
        writer.thread.join(2)
    assert not writer.thread.is_alive()


@pytest.mark.parametrize(
    "kwargs", [{"queue_bytes": 1}, {"tail_bytes": -1}, {"tail_bytes": 9000}]
)
def test_writer_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        BoundedWriter(lambda _: None, **kwargs)


def test_writer_input_bounds():
    writer = BoundedWriter(lambda _: None)
    try:
        for data in [b"[]", b"{}", b"a" * (RECORD_BYTES + 1)]:
            with pytest.raises(ValueError):
                writer.offer(data)
        for timeout in [-1, True, 3, float("nan")]:
            with pytest.raises(ValueError):
                writer.close(timeout)
    finally:
        writer.close()


@pytest.fixture
def log(tmp_path):
    root = tmp_path / "logs"
    scope = root / "business"
    scope.mkdir(parents=True)
    execution, generation = str(uuid4()), str(uuid4())

    def write(rows, name="log-000001.jsonl"):
        file = scope / name
        file.write_bytes(b"".join(rows))
        st = file.stat()
        return Segment(
            name,
            st.st_dev,
            st.st_ino,
            json.loads(rows[0])["sequence"],
            json.loads(rows[-1])["sequence"] + 1,
            st.st_size,
        )

    def read(segments, **kwargs):
        st = root.stat()
        with registered_scope(str(root), (st.st_dev, st.st_ino), "business") as fd:
            return read_page(
                fd,
                kwargs.pop("execution_id", execution),
                "business",
                kwargs.pop("generation", generation),
                segments,
                **kwargs,
            )

    return root, scope, write, read


def test_paging_forward_backward_filter_and_budget(log):
    _, _, write, read = log
    seg = write(records("😀" * 16000))
    cursor = None
    seen = []
    for _ in range(100):
        page = read([seg], cursor=cursor, limit_bytes=16384)
        assert len(json.dumps(page, ensure_ascii=True).encode()) <= 16384
        seen.extend(r["sequence"] for r in page["records"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert seen == list(range(seg.next))
    page = read([seg], direction="tail", limit_bytes=16384)
    reverse = []
    for _ in range(100):
        reverse = page["records"] + reverse
        if not page["has_more"]:
            break
        page = read(
            [seg],
            direction="backward",
            cursor=page["previous_cursor"],
            limit_bytes=16384,
        )
    assert [r["sequence"] for r in reverse] == seen
    assert all("time" in r and "gap" in r for r in reverse)
    empty = read([seg], stream="stderr")
    assert empty["records"] == [] and empty["has_more"]
    assert (
        read([seg], stream="stderr", cursor=empty["next_cursor"])["next_cursor"]
        != empty["next_cursor"]
    )
    assert all(r["sequence"] < 2 for r in read([seg], until=2)["records"])


def test_rotation_and_empty_index(log):
    _, _, write, read = log
    a = write(records("a"))
    b = write(records("b", sequence=1), "log-000002.jsonl")
    page = read([a, b])
    assert page["has_more"]
    assert read([a, b], cursor=page["next_cursor"])["records"][0]["text"] == "b"
    assert (
        read(
            [a, b],
            direction="backward",
            cursor=read([a, b], direction="tail")["previous_cursor"],
        )["records"][0]["text"]
        == "a"
    )
    old = page["previous_cursor"]
    rotated = read([b], cursor=old)
    assert rotated["gap"] and rotated["first_available"] == 1
    assert read([], cursor=old)["gap"]
    assert not read([])["has_more"]


def test_cursor_binding_and_tampering(log):
    _, _, write, read = log
    seg = write(records())
    token = read([seg])["next_cursor"]
    for kwargs in [{"generation": str(uuid4())}, {"execution_id": str(uuid4())}]:
        with pytest.raises(ValueError):
            read([seg], cursor=token, **kwargs)
    for value in ["!", "a" * 513, base64.urlsafe_b64encode(b"{}").decode()]:
        with pytest.raises(ValueError):
            read([seg], cursor=value)
    value = json.loads(base64.urlsafe_b64decode(token))
    value[-2] = 0
    with pytest.raises(ValueError):
        read(
            [seg], cursor=base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
        )
    with pytest.raises(ValueError):
        read([replace(seg, ino=seg.ino + 1)], cursor=token)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "replace", "truncate"])
def test_replaced_files_rejected(log, kind):
    _, scope, write, read = log
    seg = write(records())
    file = scope / seg.name
    if kind == "hardlink":
        os.link(file, scope / "alias")
    elif kind == "truncate":
        file.write_bytes(b"")
    else:
        file.rename(scope / "old")
        if kind == "symlink":
            file.symlink_to(scope / "old")
        elif kind == "fifo":
            os.mkfifo(file)
        else:
            file.write_bytes((scope / "old").read_bytes())
    with pytest.raises((ValueError, OSError)):
        read([seg])


def test_root_and_scope_symlinks_and_wrong_identity(log):
    root, scope, _, _ = log
    st = root.stat()
    with pytest.raises(ValueError):
        with registered_scope(str(root), (st.st_dev, st.st_ino + 1), "business"):
            pass
    scope.rmdir()
    scope.symlink_to(root)
    with pytest.raises(OSError):
        with registered_scope(str(root), (st.st_dev, st.st_ino), "business"):
            pass
    with pytest.raises(ValueError):
        with registered_scope(
            str(root) + "/../logs", (st.st_dev, st.st_ino), "business"
        ):
            pass


@pytest.mark.parametrize(
    "mutation", ["partial", "json", "sequence", "stream", "time", "gap"]
)
def test_corrupt_records(log, mutation):
    _, scope, write, read = log
    row = json.loads(records()[0])
    if mutation in ("sequence", "stream", "time", "gap"):
        row[mutation] = {"sequence": 5, "stream": "bad", "time": 1, "gap": "false"}[
            mutation
        ]
    seg = write(records())
    content = encoded(row)
    if mutation == "partial":
        content = content[:-1]
    if mutation == "json":
        content = b"not json\n"
    (scope / seg.name).write_bytes(content)
    seg = replace(seg, size=len(content))
    with pytest.raises(ValueError):
        read([seg])
    with pytest.raises(ValueError):
        read([seg], direction="tail")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit_bytes": 0},
        {"limit_bytes": 65537},
        {"stream": "bad"},
        {"direction": "bad"},
        {"until": True},
    ],
)
def test_query_validation(log, kwargs):
    _, _, write, read = log
    with pytest.raises(ValueError):
        read([write(records())], **kwargs)


def test_index_validation_and_removal_during_read(log, monkeypatch):
    _, scope, write, read = log
    seg = write(records())
    for index in [
        [replace(seg, name="../secret")],
        [seg, seg],
        [replace(seg, first=-1)],
        [seg] * 4097,
    ]:
        with pytest.raises(ValueError):
            read(index)
    original = os.pread

    def remove(fd, n, offset):
        data = original(fd, n, offset)
        (scope / seg.name).unlink()
        return data

    monkeypatch.setattr(os, "pread", remove)
    with pytest.raises(ValueError, match="removed"):
        read([seg])


def test_queue_loss_sequences_are_readable_and_report_gap(log):
    _, _, write, read = log
    # A full queue can drop encoded records; retained sequences remain monotonic.
    seg = write(records("before", sequence=0) + records("after", sequence=3))
    for direction in ["forward", "tail"]:
        page = read([seg], direction=direction)
        assert [r["sequence"] for r in page["records"]] == [0, 3]
        assert page["gap"]
    # Repeated or decreasing sequence numbers are corruption, not a loss gap.
    seg = write(records("one") + records("duplicate"))
    with pytest.raises(ValueError):
        read([seg])


def test_index_range_matches_contents_and_segment_gap(log):
    _, _, write, read = log
    a = write(records("one"))
    with pytest.raises(ValueError, match="range"):
        read([replace(a, next=2)])
    b = write(records("later", sequence=3), "log-000002.jsonl")
    page = read([a, b])
    assert read([a, b], cursor=page["next_cursor"])["gap"]
    assert read([a, b], direction="tail")["gap"]
