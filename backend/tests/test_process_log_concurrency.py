"""Isolated process/lock and bounded-load checks, not production benchmarks."""

import json
import multiprocessing
import os
from pathlib import Path
import threading
import time
import uuid
import pytest
from types import SimpleNamespace
from worker.process_logs import LogService
from runner.process_log_store import atomic

from runner.process_log_store import Store, initialize, locked, read_json
from runner.process_log_capture import BoundedWriter, encoded


def settings(tmp_path):
    cfg = dict(
        root=str(tmp_path / "logs"),
        execution_id=str(uuid.uuid4()),
        generation=str(uuid.uuid4()),
    )
    initialize(
        cfg["root"],
        cfg["execution_id"],
        cfg["generation"],
        dict(segment_bytes=65536, scope_bytes=131072, execution_bytes=524288),
    )
    return cfg


def write_scope(store, gate):
    episode = store.episode
    gate.wait(10)
    for i in range(32):
        store.write(
            encoded(
                dict(
                    sequence=i,
                    time="test",
                    stream="stdout",
                    text=f"{episode}:{i} " + ("x" * 6000),
                    gap=False,
                )
            )
        )


def test_parallel_scope_writes_share_one_execution_budget(tmp_path):
    cfg = settings(tmp_path)
    stores = [Store(cfg, "episode", episode) for episode in range(1, 5)]
    context = multiprocessing.get_context("fork")
    gate = context.Event()
    children = [
        context.Process(target=write_scope, args=(store, gate)) for store in stores
    ]
    try:
        for child in children:
            child.start()
        gate.set()
        for child in children:
            child.join(30)
            assert child.exitcode == 0
        with locked(cfg["root"]) as fd:
            index = read_json(fd, "manifest.json")
        assert len(index["scopes"]) == 4
        total = 0
        for entry in index["scopes"].values():
            assert entry["next_sequence"] == 32
            seq = []
            for segment in entry["segments"]:
                path = (
                    Path(cfg["root"])
                    / "episodes"
                    / f"{entry['episode_id']:06d}"
                    / segment["name"]
                )
                assert path.stat().st_size == segment["size"]
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                assert all(
                    row["text"].startswith(str(entry["episode_id"]) + ":")
                    for row in rows
                )
                seq.extend(row["sequence"] for row in rows)
                total += path.stat().st_size
            assert seq == sorted(set(seq))
        assert total <= index["policy"]["execution_bytes"] * 3 // 4
        assert (
            sum(p.stat().st_size for p in Path(cfg["root"]).rglob("*") if p.is_file())
            <= index["policy"]["execution_bytes"]
        )
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)


def test_sustained_output_slow_consumer_stays_memory_bounded():
    entered, release = threading.Event(), threading.Event()

    def slow(record):
        entered.set()
        assert release.wait(10)

    writer = BoundedWriter(slow)
    row = encoded(
        dict(sequence=0, time="test", stream="stderr", text="x" * 6000, gap=False)
    )
    started = time.monotonic()
    try:
        writer.offer(row)
        assert entered.wait(2)
        for _ in range(4096):
            writer.offer(row)
        assert writer.bytes <= writer.capacity
        assert sum(map(len, writer.queue)) + writer.inflight == writer.bytes
        assert len(writer.stderr_tail) <= 8192
        assert writer.dropped_bytes > 20 * 1024**2
        assert time.monotonic() - started < 5
    finally:
        release.set()
        state = writer.close()
    assert state["incomplete"] and state["dropped_bytes"] > 0


def crash_rotation(store, point):
    from runner import process_log_store as module

    if point == "before-index":
        store.save = lambda *args: os._exit(91)
    else:
        unlink = module.os.unlink

        def interrupted(name, *args, **kwargs):
            if (point == "after-index" and name.startswith("log-")) or (
                point == "after-unlink" and name == "prune.json"
            ):
                os._exit(91)
            return unlink(name, *args, **kwargs)

        module.os.unlink = interrupted
    store.write(
        encoded(
            dict(sequence=8, time="test", stream="stdout", text="x" * 7400, gap=False)
        )
    )
    os._exit(92)


@pytest.mark.parametrize("point", ["before-index", "after-index", "after-unlink"])
def test_actual_writer_crash_recovers_pending_rotation(tmp_path, point):
    cfg = dict(
        root=str(tmp_path / "logs"),
        execution_id=str(uuid.uuid4()),
        generation=str(uuid.uuid4()),
    )
    initialize(
        cfg["root"],
        cfg["execution_id"],
        cfg["generation"],
        dict(segment_bytes=65536, scope_bytes=65536, execution_bytes=262144),
    )
    store = Store(cfg, "business")
    for i in range(8):
        store.write(
            encoded(
                dict(
                    sequence=i, time="test", stream="stdout", text="x" * 7400, gap=False
                )
            )
        )
    context = multiprocessing.get_context("fork")
    child = context.Process(target=crash_rotation, args=(store, point))
    child.start()
    child.join(10)
    try:
        assert child.exitcode == 91
        assert (Path(cfg["root"]) / "prune.json").exists()
        st = Path(cfg["root"]).stat()
        item = {**cfg, "root_identity": [st.st_dev, st.st_ino]}
        service = LogService(SimpleNamespace(config={}, path=tmp_path / "state"))
        index = service.read_index(item)
        entry = index["scopes"][store.key]
        assert entry["next_sequence"] == (8 if point == "before-index" else 9)
        assert not (Path(cfg["root"]) / "prune.json").exists()
        for seg in entry["segments"]:
            path = Path(cfg["root"]) / "business" / seg["name"]
            assert path.stat().st_size == seg["size"]
            assert len(path.read_bytes().splitlines()) == seg["next"] - seg["first"]
        # Further writes remain possible, without restarting business execution.
        seq = entry["next_sequence"]
        store.write(
            encoded(
                dict(
                    sequence=seq,
                    time="test",
                    stream="stdout",
                    text="after recovery",
                    gap=False,
                )
            )
        )
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)


@pytest.mark.parametrize(
    "damage",
    [
        "identity",
        "victims-type",
        "too-many",
        "traversal",
        "scope-missing",
        "replaced",
        "hardlink",
        "symlink",
    ],
)
def test_pruning_recovery_rejects_tampered_intent(tmp_path, damage):
    from runner.process_log_store import recover_pruning

    cfg = settings(tmp_path)
    Store(cfg, "business")
    victim = Path(cfg["root"]) / "business" / "log-999999.jsonl"
    victim.write_text("must preserve")
    st = victim.stat()
    row = dict(
        scope="business", episode_id=0, name=victim.name, dev=st.st_dev, ino=st.st_ino
    )
    pending = dict(
        execution_id=cfg["execution_id"], generation=cfg["generation"], victims=[row]
    )
    if damage == "identity":
        pending["generation"] = str(uuid.uuid4())
    elif damage == "victims-type":
        pending["victims"] = {}
    elif damage == "too-many":
        pending["victims"] = [row] * 10004
    elif damage == "traversal":
        row["name"] = "../manifest.json"
    elif damage == "scope-missing":
        row["scope"] = "service"
    elif damage == "replaced":
        row["ino"] = -1
    elif damage == "hardlink":
        os.link(victim, tmp_path / "linked")
    elif damage == "symlink":
        victim.unlink()
        outside = tmp_path / "outside"
        outside.write_text("must preserve")
        victim.symlink_to(outside)
    with locked(cfg["root"]) as fd:
        index = read_json(fd, "manifest.json")
        atomic(fd, pending, "prune.json")
        with pytest.raises(ValueError):
            recover_pruning(fd, index)
    assert victim.read_text() == "must preserve"


def test_inflight_read_finishes_before_cleanup_and_next_read_is_missing(
    tmp_path, monkeypatch
):
    import fcntl
    import shutil
    from worker import process_logs

    # Use the real registered path/lock layout while pausing page construction.
    cfg = dict(
        root=str(tmp_path / "runs" / str(uuid.uuid4()) / "logs"),
        execution_id="",
        generation=str(uuid.uuid4()),
    )
    cfg["execution_id"] = Path(cfg["root"]).parent.name
    initialize(cfg["root"], cfg["execution_id"], cfg["generation"])
    store = Store(cfg, "business")
    store.write(
        encoded(
            dict(sequence=0, time="test", stream="stdout", text="retained", gap=False)
        )
    )
    st = Path(cfg["root"]).stat()
    item = {**cfg, "root_identity": [st.st_dev, st.st_ino]}
    service = LogService(SimpleNamespace(config={}, path=tmp_path / "state"))
    request = dict(
        scope="business", episode_id=0, generation=cfg["generation"], query={}
    )
    entered, release = threading.Event(), threading.Event()
    replies = []
    original = process_logs.read_page

    def pause(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(process_logs, "read_page", pause)
    thread = threading.Thread(
        target=lambda: replies.append(service.read(item, request))
    )
    thread.start()
    try:
        assert entered.wait(2)
        path = (
            Path(cfg["root"]).parent.parent / ".retention-locks" / cfg["execution_id"]
        )
        with path.open("r+") as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release.set()
            thread.join(3)
            assert not thread.is_alive()
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            shutil.rmtree(Path(cfg["root"]).parent)
        assert replies[0][0] == "ready"
        with pytest.raises(FileNotFoundError):
            service.read(item, request)
    finally:
        release.set()
        thread.join(3)


def test_ten_thousand_scope_history_sync_has_bounded_batch(tmp_path):
    from worker.storage import atomic_json

    cfg = dict(
        root=str(tmp_path / "runs" / str(uuid.uuid4()) / "logs"),
        execution_id="",
        generation=str(uuid.uuid4()),
    )
    cfg["execution_id"] = Path(cfg["root"]).parent.name
    initialize(cfg["root"], cfg["execution_id"], cfg["generation"])
    store = Store(cfg, "episode", 10000)
    store.write(
        encoded(
            dict(
                sequence=0, time="test", stream="stdout", text="last episode", gap=False
            )
        )
    )
    store.close({"incomplete": False, "dropped_bytes": 0})
    with locked(cfg["root"]) as fd:
        index = read_json(fd, "manifest.json")
        last = index["scopes"][store.key]
        empty = {**last, "segments": [], "next_sequence": 0}
        index["scopes"] = {
            f"{cfg['execution_id']}/episode/{i}": {**empty, "episode_id": i}
            for i in range(1, 10000)
        }
        index["scopes"][store.key] = last
        atomic(fd, index)
    calls = []
    worker = SimpleNamespace(
        config={},
        path=tmp_path / "state",
        slot_id="slot",
        children={},
        api=SimpleNamespace(post=lambda path, body: calls.append(body)),
    )
    service = LogService(worker)
    st = Path(cfg["root"]).stat()
    item = {
        **cfg,
        "root_identity": [st.st_dev, st.st_ino],
        "slot_id": "slot",
        "catalogs": {},
    }
    path = service.directory / (cfg["execution_id"] + ".json")
    atomic_json(path, item)
    service.sync(path, item)
    assert len(calls) == 16
    assert len(json.loads(path.read_text())["catalogs"]) == 16
    status, page = service.read(
        item,
        dict(scope="episode", episode_id=10000, generation=cfg["generation"], query={}),
    )
    assert status == "ready" and page["records"][0]["text"] == "last episode"
    assert len(json.dumps(page).encode()) <= 65536
    with locked(cfg["root"]) as fd:
        index = read_json(fd, "manifest.json")
        for i in range(10001, 10005):
            index["scopes"][f"{cfg['execution_id']}/episode/{i}"] = {
                **empty,
                "episode_id": i,
            }
        atomic(fd, index)
    with pytest.raises(ValueError, match="scope count"):
        Store(cfg, "task")
    with pytest.raises(ValueError, match="invalid log index"):
        service.read_index(item)
