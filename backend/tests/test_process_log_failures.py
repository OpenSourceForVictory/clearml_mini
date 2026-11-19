"""Fault and recovery checks at the process/Worker log boundary."""

import io
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest
from runner import process_log_run as capture
from runner.process_log_store import Store, atomic, locked, read_json
from worker.process_logs import LogService, prepare, registry


def fixture(tmp_path):
    calls = []
    worker = SimpleNamespace(
        config={},
        path=tmp_path / "state",
        slot_id="slot",
        children={},
        api=SimpleNamespace(
            post=lambda path, body: calls.append((path, body)) or {"requests": []}
        ),
    )
    task = {"execution_id": str(uuid.uuid4()), "process_logs_v1": True}
    worker.children[task["execution_id"]] = object()
    settings = prepare(worker, task, tmp_path / "runs" / task["execution_id"] / "logs")
    path = registry(worker) / (task["execution_id"] + ".json")
    item = json.loads(path.read_text())
    return worker, task, settings, path, item, calls


def change(settings, fn):
    with locked(settings["root"]) as fd:
        index = read_json(fd, "manifest.json")
        fn(index)
        atomic(fd, index)


def code_run(settings, code, **kwargs):
    return capture.run(
        [sys.executable, "-c", code],
        config={"_process_logs": settings},
        scope="business",
        **kwargs,
    )


def test_capture_requires_negotiation():
    with pytest.raises(ValueError, match="negotiated"):
        capture.run([], config={}, scope="business")


def test_capture_missing_root_keeps_output_and_exit(tmp_path, capsys):
    _, _, settings, _, _, _ = fixture(tmp_path)
    shutil.rmtree(settings["root"])
    assert (
        code_run(
            settings,
            "import sys;print('still stdout');print('still stderr',file=sys.stderr);sys.exit(9)",
        )
        == 9
    )
    out = capsys.readouterr()
    assert (
        "still stdout" in out.out
        and "still stderr" in out.err
        and "unavailable" in out.err
    )


@pytest.mark.parametrize("launch_failure", [False, True])
def test_capture_close_failure_preserves_business_outcome(
    tmp_path, monkeypatch, capsys, launch_failure
):
    _, _, settings, _, _, _ = fixture(tmp_path)

    def fail(*args):
        raise OSError("injected disk failure")

    monkeypatch.setattr(Store, "close", fail)
    if launch_failure:
        with pytest.raises(FileNotFoundError):
            capture.run(
                ["/no/such/executable"],
                config={"_process_logs": settings},
                scope="business",
            )
    else:
        assert code_run(settings, "raise SystemExit(7)") == 7
        assert "close incomplete" in capsys.readouterr().err


def test_capture_disk_write_failure_keeps_exit_and_marks_gap(tmp_path, monkeypatch):
    _, _, settings, _, _, _ = fixture(tmp_path)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(Store, "write", fail)
    assert code_run(settings, "import sys;print('must drain');sys.exit(5)") == 5
    with locked(settings["root"]) as fd:
        entries = read_json(fd, "manifest.json")["scopes"]
    entry = next(e for e in entries.values() if e["scope"] == "business")
    assert entry["status"] == "incomplete" and entry["dropped_bytes"] > 0


def test_capture_callback_long_lines_tail_and_text_stream(tmp_path, monkeypatch):
    _, _, settings, _, _, _ = fixture(tmp_path)
    output, error = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", error)
    lines = []
    code = "import os;os.write(1,b'x'*70000+b'\\nsmall\\nunterminated');os.write(2,b'error-tail')"
    assert code_run(settings, code, on_line=lines.append) == 0
    assert lines == ["small\n", "unterminated"]
    assert "oversized stdout line" in output.getvalue()
    assert error.getvalue().startswith("error-tail")
    # A slow file system may also emit the bounded-close diagnostic.
    assert error.getvalue()[len("error-tail") :] in (
        "",
        "process log close incomplete\n",
    )


def test_callback_exception_reaps_running_child(tmp_path):
    _, _, settings, _, _, _ = fixture(tmp_path)
    pid = []

    def callback(line):
        pid.append(int(line))
        raise ValueError("consumer failed")

    with pytest.raises(ValueError, match="consumer failed"):
        code_run(
            settings,
            "import os,time;print(os.getpid(),flush=True);time.sleep(60)",
            on_line=callback,
        )
    with pytest.raises(ProcessLookupError):
        os.kill(pid[0], 0)


def test_prepare_reuses_registration_after_restart(tmp_path):
    worker, task, settings, path, item, calls = fixture(tmp_path)
    again = prepare(worker, task, Path(settings["root"]))
    assert again == settings
    assert json.loads(path.read_text())["root_identity"] == item["root_identity"]
    assert len(calls) == 2


def test_sync_inactive_scopes_and_idempotent_catalogs(tmp_path):
    worker, task, settings, path, item, calls = fixture(tmp_path)
    Store(settings, "episode", 1)  # interrupted before close
    worker.children.clear()
    service = LogService(worker)
    service.sync(path, item)
    statuses = {body["scope"]: body["status"] for _, body in calls[1:]}
    assert statuses == {"task": "closed", "episode": "incomplete"}
    before = len(calls)
    service.sync(path, json.loads(path.read_text()))
    assert len(calls) == before


def test_sync_batches_catalogs_without_losing_later_scopes(tmp_path):
    worker, _, settings, path, item, calls = fixture(tmp_path)
    for i in range(1, 19):
        Store(settings, "episode", i)
    service = LogService(worker)
    calls.clear()
    service.sync(path, item)
    assert len(calls) == 16
    service.sync(path, json.loads(path.read_text()))
    assert len(calls) == 19
    assert len(json.loads(path.read_text())["catalogs"]) == 19


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize(
    "damage", ["root", "generation", "execution", "scopes", "scope_key"]
)
def test_sync_rejects_replaced_identity(tmp_path, active, damage):
    worker, _, settings, path, item, _ = fixture(tmp_path)
    if not active:
        worker.children.clear()
    if damage == "root":
        item["root_identity"] = [-1, -1]
    elif damage in ("generation", "execution"):
        change(
            settings,
            lambda index: index.update(
                {
                    ("execution_id" if damage == "execution" else damage): str(
                        uuid.uuid4()
                    )
                }
            ),
        )
    elif damage == "scopes":
        # read_index independently validates the manifest before interpreting scopes.
        change(settings, lambda index: index.update(scopes=[]))
        with pytest.raises(ValueError, match="invalid log index"):
            LogService(worker).read_index(item)
        return
    else:

        def corrupt(index):
            index["scopes"]["wrong"] = index["scopes"].pop(next(iter(index["scopes"])))

        change(settings, corrupt)
    with pytest.raises(ValueError):
        LogService(worker).sync(path, item)


def test_sync_cleaned_directory_vs_missing_index(tmp_path):
    worker, _, settings, path, item, calls = fixture(tmp_path)
    service = LogService(worker)
    service.sync(path, item)
    (Path(settings["root"]) / "manifest.json").unlink()
    with pytest.raises(ValueError, match="index missing"):
        service.sync(path, item)
    assert path.exists()
    shutil.rmtree(settings["root"])
    service.sync(path, item)
    assert not path.exists() and calls[-1][1]["status"] == "cleaned"


@pytest.mark.parametrize("damage", ["root", "generation", "execution", "missing_scope"])
def test_read_rejects_identity_or_reports_missing_scope(tmp_path, damage):
    worker, _, settings, _, item, _ = fixture(tmp_path)
    request = dict(
        scope="business", episode_id=0, generation=settings["generation"], query={}
    )
    if damage == "root":
        item["root_identity"] = [-1, -1]
    elif damage == "generation":
        request["generation"] = str(uuid.uuid4())
    elif damage == "execution":
        change(settings, lambda index: index.update(execution_id=str(uuid.uuid4())))
    service = LogService(worker)
    if damage == "missing_scope":
        assert service.read(item, request) == ("missing", {})
    else:
        with pytest.raises(ValueError):
            service.read(item, request)


def test_real_retention_lock_blocks_read_and_recovers(tmp_path):
    import fcntl

    worker, task, settings, _, item, _ = fixture(tmp_path)
    request = dict(
        scope="task", episode_id=0, generation=settings["generation"], query={}
    )
    service = LogService(worker)
    assert service.read(item, request)[0] == "ready"
    lock = (
        Path(settings["root"]).parent.parent / ".retention-locks" / task["execution_id"]
    )
    with lock.open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            service.read(item, request)
    assert service.read(item, request)[0] == "ready"


@pytest.mark.parametrize(
    "error,expected",
    [
        (BlockingIOError, "busy"),
        (FileNotFoundError, "cleaned"),
        (ValueError, "failed"),
        (None, "ready"),
    ],
)
def test_mailbox_read_failures_are_bounded_replies(
    tmp_path, monkeypatch, error, expected
):
    worker, task, settings, _, item, calls = fixture(tmp_path)
    request = dict(
        execution_id=task["execution_id"],
        scope="task",
        episode_id=0,
        generation=settings["generation"],
        query={},
        id="read",
        claim="nonce",
    )

    def post(path, body):
        calls.append((path, body))
        return {"requests": [request]} if path.endswith("/claim") else {}

    worker.api.post = post
    service = LogService(worker)
    if error:

        def fail(*args):
            raise error("injected")

        monkeypatch.setattr(service, "read", fail)
    assert service.cycle() is True
    assert calls[-1][0] == "worker/log-reads/read/reply"
    assert calls[-1][1]["status"] == expected
    assert calls[-1][1]["claim"] == "nonce"


def test_mailbox_slot_mismatch_never_reads_files(tmp_path, monkeypatch):
    worker, task, settings, path, item, calls = fixture(tmp_path)
    item["slot_id"] = "other"
    path.write_text(json.dumps(item))
    request = dict(
        execution_id=task["execution_id"],
        generation=settings["generation"],
        id="read",
        claim="nonce",
    )

    def post(url, body):
        calls.append((url, body))
        return {"requests": [request]} if url.endswith("/claim") else {}

    worker.api.post = post
    service = LogService(worker)
    monkeypatch.setattr(service, "read", lambda *a: pytest.fail("cross-slot read"))
    assert service.cycle()
    assert calls[-1][1]["status"] == "failed"


@pytest.mark.parametrize("status", [404, 503])
def test_catalog_api_outage_retry_vs_deleted_execution(
    tmp_path, monkeypatch, status, caplog
):
    worker, _, _, path, _, calls = fixture(tmp_path)
    service = LogService(worker)

    class ApiError(Exception):
        pass

    def fail(*args):
        error = ApiError("private details must not leak")
        error.status = status
        raise error

    monkeypatch.setattr(service, "sync", fail)
    assert service.cycle() is False
    assert path.exists() == (status != 404)
    if status == 503:
        assert "catalog sync deferred" in caplog.text
    assert "private details" not in caplog.text


def test_stopped_service_does_not_contact_api(tmp_path):
    worker, _, _, _, _, calls = fixture(tmp_path)
    service = LogService(worker)
    service.close()
    before = len(calls)
    service.cycle()
    assert len(calls) == before


def test_background_thread_survives_api_failure_and_closes(
    tmp_path, monkeypatch, caplog
):
    worker, _, _, _, _, _ = fixture(tmp_path)
    service = LogService(worker)
    reached = threading.Event()

    def fail():
        reached.set()
        raise OSError("secret API detail")

    monkeypatch.setattr(service, "cycle", fail)
    service.start()
    assert reached.wait(2)
    service.close()
    assert not service.thread.is_alive()
    assert "reads deferred" in caplog.text and "secret API detail" not in caplog.text


def test_slow_sink_does_not_delay_business_exit_indefinitely(
    tmp_path, monkeypatch, capsys
):
    import time

    _, _, settings, _, _, _ = fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = Store.write

    def slow(store, record):
        entered.set()
        assert release.wait(10)
        return original(store, record)

    monkeypatch.setattr(Store, "write", slow)
    begin = time.monotonic()
    try:
        assert code_run(settings, "print('slow sink output');raise SystemExit(6)") == 6
        assert entered.is_set()
        assert time.monotonic() - begin < 5
        with locked(settings["root"]) as fd:
            entry = next(
                e
                for e in read_json(fd, "manifest.json")["scopes"].values()
                if e["scope"] == "business"
            )
        assert entry["status"] == "incomplete"
    finally:
        release.set()


def test_api_failure_keeps_unsent_catalog_for_next_cycle(tmp_path):
    worker, _, settings, path, item, calls = fixture(tmp_path)
    service = LogService(worker)
    original = worker.api.post

    def unavailable(*args):
        raise OSError("API offline")

    worker.api.post = unavailable
    with pytest.raises(OSError):
        service.sync(path, item)
    assert json.loads(path.read_text())["catalogs"] == {}
    worker.api.post = original
    # A fresh service reconstructs its state from the private persisted record.
    LogService(worker).sync(path, json.loads(path.read_text()))
    assert len(json.loads(path.read_text())["catalogs"]) == 1
