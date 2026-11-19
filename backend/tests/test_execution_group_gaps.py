"""Execution-group recovery, contract and storage gaps, with real children."""

import json
import os
from pathlib import Path
import sys
import uuid

import pytest
from runner import host_episode
from runner.process_log_store import (
    Store,
    initialize,
    locked,
    read_json,
    atomic,
    policy,
    scope_fd,
)
from runner.process_log_capture import RecordEncoder


@pytest.mark.parametrize("logging", [False, True])
@pytest.mark.parametrize("error_policy", ["stop", "continue"])
@pytest.mark.parametrize(
    "fault", ["success", "false", "missing", "multiple", "invalid", "exit"]
)
def test_host_episode_controller_real_children(
    tmp_path, monkeypatch, capsys, logging, error_policy, fault
):
    work, out = tmp_path / "work", tmp_path / "output"
    work.mkdir()
    out.mkdir()
    monkeypatch.setenv("MINI_EVAL_WORK_DIR", str(work))
    monkeypatch.setenv("MINI_EVAL_OUTPUT_DIR", str(out))
    script = tmp_path / "business.py"
    script.write_text(
        """import os,json,sys
from pathlib import Path
config=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
i=int(os.environ['MINI_EVAL_EPISODE_ID'])
assert config['episodes']==1
assert config['seed']==40+i-1
assert os.environ['FLAG']=='literal'
print('business-'+str(i),flush=True)
result={'episode_count':1,'success_rate':1,'runtime':.1}
"""
        + (
            {
                "success": "",
                "false": "if i==2:result['success_rate']=0\n",
                "missing": "if i==2:sys.exit(0)\n",
                "multiple": "if i==2:print(json.dumps({'final':result}))\n",
                "invalid": "if i==2:result['episode_count']=2\n",
                "exit": "if i==2:sys.exit(8)\n",
            }[fault]
        )
        + "print(json.dumps({'final':result}))\n"
    )
    config = dict(
        task_name="case",
        episodes=3,
        seed=40,
        endpoint="",
        service_mode="none",
        parameters={},
        template=dict(
            execution_mode="host",
            integration_mode="direct",
            task_type="evaluation",
            host_runtime=dict(
                command_mode="exec",
                environment={"FLAG": "literal"},
                episode_launch="per_episode",
                episode_error_policy=error_policy,
            ),
        ),
    )
    if logging:
        root = tmp_path / "logs"
        execution = str(uuid.uuid4())
        generation = str(uuid.uuid4())
        initialize(root, execution, generation)
        config["_process_logs"] = dict(
            root=str(root), execution_id=execution, generation=generation
        )
    failed = fault not in ("success", "false")
    if failed:
        with pytest.raises(RuntimeError):
            host_episode.main(
                config,
                [sys.executable, str(script)],
                str(tmp_path),
                {**os.environ, "FLAG": "literal"},
            )
    else:
        result = host_episode.main(
            config,
            [sys.executable, str(script)],
            str(tmp_path),
            {**os.environ, "FLAG": "literal"},
        )
        assert result["success_rate"] == (2 / 3 if fault == "false" else 1)
    count = 2 if failed and error_policy == "stop" else 3
    events = [
        json.loads(line) for line in (work / "episodes.jsonl").read_text().splitlines()
    ]
    assert [e["status"] for e in events] == [
        state
        for i in range(1, count + 1)
        for state in ("STARTED", "ERROR" if failed and i == 2 else "COMPLETED")
    ]
    assert all(
        e["exit_code"] == (8 if fault == "exit" and e["episode_id"] == 2 else 0)
        for e in events
        if e["status"] != "STARTED"
    )
    output = capsys.readouterr().out
    assert output.count("MINI_EVAL_EVENT ") == 2 * count
    assert sum('"final"' in line for line in output.splitlines()) == int(not failed)


def log_store(tmp_path):
    cfg = dict(
        root=str(tmp_path / "logs"),
        execution_id=str(uuid.uuid4()),
        generation=str(uuid.uuid4()),
    )
    initialize(cfg["root"], cfg["execution_id"], cfg["generation"])
    store = Store(cfg, "business")
    encoder = RecordEncoder()
    store.write(next(encoder.feed("stdout", b"first\n", final=True)))
    return cfg, store, encoder


@pytest.mark.parametrize(
    "damage",
    [
        "generation",
        "execution",
        "truncated",
        "replaced",
        "hardlink",
        "replay",
        "closed",
        "oversized",
    ],
)
def test_store_rejects_corruption_without_overwriting_other_files(tmp_path, damage):
    cfg, store, encoder = log_store(tmp_path)
    segment = next((Path(cfg["root"]) / "business").glob("*.jsonl"))
    if damage in ("generation", "execution"):
        with locked(cfg["root"]) as fd:
            index = read_json(fd, "manifest.json")
            index["generation" if damage == "generation" else "execution_id"] = str(
                uuid.uuid4()
            )
            atomic(fd, index)
    elif damage == "truncated":
        segment.write_bytes(b"")
    elif damage == "replaced":
        segment.rename(segment.with_suffix(".old"))
        segment.write_bytes(b"unrelated")
    elif damage == "hardlink":
        os.link(segment, tmp_path / "linked")
    elif damage == "closed":
        store.close({"incomplete": False, "dropped_bytes": 0})
    data = next(encoder.feed("stdout", b"next\n", final=True))
    if damage == "replay":
        data = json.dumps({**json.loads(data), "sequence": 0}).encode()
    if damage == "oversized":
        data = b"x" * 8193
    before = segment.read_bytes()
    with pytest.raises(ValueError):
        store.write(data)
    assert segment.read_bytes() == before


@pytest.mark.parametrize(
    "limits",
    [
        {"other": 1},
        {"segment_bytes": True},
        {"segment_bytes": 65535},
        {"scope_bytes": 1},
    ],
)
def test_store_policy_rejects_invalid_budgets(limits):
    with pytest.raises(ValueError):
        policy(limits)


def test_store_reinitialization_is_idempotent_but_identity_bound(tmp_path):
    cfg, store, _ = log_store(tmp_path)
    initialize(cfg["root"], cfg["execution_id"], cfg["generation"])
    with pytest.raises(ValueError):
        initialize(cfg["root"], cfg["execution_id"], str(uuid.uuid4()))
    with locked(cfg["root"]) as fd:
        assert read_json(fd, "manifest.json")["scopes"][store.key]["next_sequence"] == 1
        with pytest.raises(ValueError):
            store.save(
                fd, {"policy": {"execution_bytes": 65536}, "padding": "x" * 20000}
            )


@pytest.mark.parametrize(
    "damage",
    [
        "relative",
        "traversal",
        "lock-hardlink",
        "scope-link",
        "index-hardlink",
        "index-large",
    ],
)
def test_store_rejects_unsafe_metadata_paths(tmp_path, damage):
    cfg, store, _ = log_store(tmp_path)
    root = Path(cfg["root"])
    if damage in ("relative", "traversal"):
        with pytest.raises(ValueError):
            with locked(
                "relative" if damage == "relative" else str(root / ".." / "logs")
            ):
                pass
    elif damage == "lock-hardlink":
        os.link(root / ".lock", tmp_path / "linked-lock")
        with pytest.raises(ValueError):
            with locked(root):
                pass
    elif damage == "scope-link":
        (root / "service").symlink_to(root / "business")
        with locked(root) as fd:
            with pytest.raises(OSError):
                scope_fd(fd, "service", 0, True)
    else:
        if damage == "index-hardlink":
            os.link(root / "manifest.json", tmp_path / "linked-index")
        with locked(root) as fd:
            with pytest.raises(ValueError):
                read_json(
                    fd,
                    "manifest.json",
                    limit=8 if damage == "index-large" else 8 * 1024**2,
                )


def test_short_disk_write_reports_error_and_recovers_uncommitted_tail(
    tmp_path, monkeypatch
):
    cfg, store, encoder = log_store(tmp_path)
    data = next(encoder.feed("stdout", b"retry-record\n", final=True))
    original = os.write
    count = 0

    def short_write(fd, buffer):
        nonlocal count
        count += 1
        return original(fd, buffer[:5]) if count == 1 else 0

    with monkeypatch.context() as m:
        m.setattr(os, "write", short_write)
        with pytest.raises(OSError, match="short log write"):
            store.write(data)
    store.write(data)
    segment = next((Path(cfg["root"]) / "business").glob("*.jsonl"))
    rows = [json.loads(line) for line in segment.read_text().splitlines()]
    assert [r["text"] for r in rows] == ["first\n", "retry-record\n"]


def test_rotation_index_commit_failure_keeps_previous_segments_readable(
    tmp_path, monkeypatch
):
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

    def record(i):
        return (
            json.dumps(
                dict(
                    sequence=i, time="now", stream="stdout", text="x" * 7400, gap=False
                )
            )
            + "\n"
        ).encode()

    for i in range(8):
        store.write(record(i))
    with locked(cfg["root"]) as fd:
        before = read_json(fd, "manifest.json")

    def disk_failure(*args):
        raise OSError("index commit failed")

    with monkeypatch.context() as m:
        m.setattr(store, "save", disk_failure)
        with pytest.raises(OSError):
            store.write(record(8))
    # The old committed index must not point at already deleted segments.
    for segment in before["scopes"][store.key]["segments"]:
        assert (Path(cfg["root"]) / "business" / segment["name"]).exists()
    store.write(record(8))
    with locked(cfg["root"]) as fd:
        after = read_json(fd, "manifest.json")
    assert after["scopes"][store.key]["truncated"]
    assert after["scopes"][store.key]["next_sequence"] == 9
