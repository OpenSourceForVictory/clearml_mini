import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest
from runner.process_log_store import Store, initialize, locked, read_json
from runner.process_log_capture import RecordEncoder
from runner.process_log_run import run
from worker.process_logs import LogService
from backend.tests.test_execution import env, node, submit, claim


def setup_log(tmp_path, limits=None):
    execution = str(uuid.uuid4())
    generation = str(uuid.uuid4())
    root = tmp_path / "runs" / execution / "logs"
    initialize(root, execution, generation, limits)
    return dict(root=str(root), execution_id=execution, generation=generation)


def index(config):
    with locked(config["root"], False) as fd:
        return read_json(fd, "manifest.json")


@pytest.mark.parametrize(
    "scope,episode", [("business", 0), ("episode", 1), ("service", 0)]
)
def test_real_process_files_and_scoped_read(tmp_path, scope, episode, capsys):
    config = setup_log(tmp_path)
    code = run(
        [
            sys.executable,
            "-c",
            "import sys;print('hello');print('bad',file=sys.stderr);sys.exit(7)",
        ],
        config={"_process_logs": config},
        scope=scope,
        episode=episode,
    )
    assert code == 7
    idx = index(config)
    entry = next(iter(idx["scopes"].values()))
    assert entry["status"] == "closed"
    st = Path(config["root"]).stat()
    worker = SimpleNamespace(config={}, path=tmp_path / "state")
    service = LogService(worker)
    item = {**config, "root_identity": [st.st_dev, st.st_ino]}
    status, page = service.read(
        item,
        dict(
            scope=scope, episode_id=episode, generation=config["generation"], query={}
        ),
    )
    assert status == "ready"
    assert (
        "".join(r["text"] for r in page["records"] if r["stream"] == "stdout")
        == "hello\n"
    )
    assert (
        "".join(r["text"] for r in page["records"] if r["stream"] == "stderr")
        == "bad\n"
    )
    assert page["closed"]


def test_store_rotation_execution_budget_and_uncommitted_tail(tmp_path):
    config = setup_log(
        tmp_path, dict(segment_bytes=65536, scope_bytes=131072, execution_bytes=262144)
    )
    for episode in range(1, 4):
        store = Store(config, "episode", episode)
        encoder = RecordEncoder()
        for _ in range(60):
            for row in encoder.feed("stdout", b"x" * 4096):
                store.write(row)
        store.close(dict(incomplete=False, dropped_bytes=0))
    idx = index(config)
    assert (
        sum(s["size"] for e in idx["scopes"].values() for s in e["segments"])
        <= 262144 * 3 // 4
    )
    assert any(e["truncated"] for e in idx["scopes"].values())
    assert (
        sum(p.stat().st_size for p in Path(config["root"]).rglob("*") if p.is_file())
        < 262144
    )
    with pytest.raises(ValueError, match="already created"):
        Store(config, "episode", 3)
    store = Store(config, "business")
    encoder = RecordEncoder()
    store.write(next(encoder.feed("stdout", b"committed")))
    entry = index(config)["scopes"][store.key]
    file = Path(config["root"]) / "business" / entry["segments"][-1]["name"]
    with file.open("ab") as out:
        out.write(b"partial write")
    store.write(next(encoder.feed("stdout", b"next")))
    assert b"partial write" not in file.read_bytes()


def test_missing_executable_marks_scope_incomplete(tmp_path):
    config = setup_log(tmp_path)
    with pytest.raises(FileNotFoundError):
        run(
            ["/no-such-mini-eval-command"],
            config={"_process_logs": config},
            scope="business",
        )
    assert next(iter(index(config)["scopes"].values()))["status"] == "incomplete"


@pytest.mark.parametrize("integration", ["direct", "sdk-command", "command"])
@pytest.mark.parametrize(
    "failure, error_policy",
    [
        ("none", "stop"),
        ("exit", "stop"),
        ("exit", "continue"),
        ("missing", "stop"),
        ("missing", "continue"),
    ],
)
def test_host_per_episode_real_entry_and_protocol(
    tmp_path, integration, failure, error_policy
):
    config = setup_log(tmp_path)
    work = tmp_path / "work"
    out = tmp_path / "output"
    work.mkdir()
    out.mkdir()
    script = tmp_path / "business.py"
    script.write_text(
        "import os,json,sys\nfrom pathlib import Path\ni=os.environ['MINI_EVAL_EPISODE_ID']\nprint('stdout-'+i);print('stderr-'+i,file=sys.stderr)\n"
        + (
            "if i=='2':sys.exit(7)\n"
            if failure == "exit"
            else "if i=='2':sys.exit(0)\n"
            if failure == "missing"
            else ""
        )
        + "Path(os.environ['MINI_EVAL_RESULT']).write_text('{\"success\":true}')\n"
        + (
            "print(json.dumps({'final':{'episode_count':1,'success_rate':1,'runtime':.1}}))\n"
            if integration != "sdk-command"
            else ""
        )
    )
    task = dict(
        contract_version=1,
        service_mode="none",
        endpoint="",
        task_name="case",
        episodes=3,
        seed=7,
        parameters={},
        template=dict(
            execution_mode="host",
            integration_mode="direct" if integration == "command" else integration,
            task_type="command" if integration == "command" else "evaluation",
            host_runtime=dict(
                command_mode="exec",
                episode_launch="per_episode",
                episode_error_policy=error_policy,
            ),
            command=[sys.executable, str(script)],
        ),
        _resolved_command=[sys.executable, str(script)],
        _resolved_environment=dict(
            MINI_EVAL_WORK_DIR=str(work), MINI_EVAL_OUTPUT_DIR=str(out)
        ),
        _resolved_cwd=str(tmp_path),
        _process_logs=config,
    )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(task))
    result = subprocess.run(
        [sys.executable, "-m", "runner.cs_entry"],
        env={
            **os.environ,
            "PYTHONPATH": str(Path.cwd()),
            "MINI_EVAL_INPUT": str(path),
            "MINI_EVAL_WORK_DIR": str(work),
            "MINI_EVAL_OUTPUT_DIR": str(out),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    failed = failure == "exit" or failure == "missing" and integration != "command"
    assert (result.returncode != 0) == failed, result.stderr
    count = 2 if failed and error_policy == "stop" else 3
    entries = index(config)["scopes"]
    assert len(entries) == count and all(
        e["status"] == "closed" for e in entries.values()
    )
    events = [
        json.loads(line) for line in (work / "episodes.jsonl").read_text().splitlines()
    ]
    expected = [
        state
        for i in range(1, count + 1)
        for state in ("STARTED", "ERROR" if i == 2 and failed else "COMPLETED")
    ]
    assert [e["status"] for e in events] == expected
    assert len([line for line in result.stdout.splitlines() if '"final"' in line]) == (
        0 if failed else 1
    )
    if failed:
        assert events[3]["exit_code"] == (7 if failure == "exit" else 0)
    for e in entries.values():
        data = "".join(
            (
                Path(config["root"]) / "episodes" / f"{e['episode_id']:06d}" / s["name"]
            ).read_text()
            for s in e["segments"]
        )
        assert (
            f"stdout-{e['episode_id']}" in data and f"stderr-{e['episode_id']}" in data
        )


def test_api_worker_file_mailbox_end_to_end(env, tmp_path):
    main, client, auth = env
    token, slots = node(env)
    job = submit(env)
    task = claim(env, token, slots[0])
    root = tmp_path / "runs" / task["execution_id"] / "logs"

    def post(path, body=None):
        result = client.post("/api/v1/" + path, headers=token, json=body)
        assert result.status_code < 300, result.text
        return result.json()

    worker = SimpleNamespace(
        config={},
        path=tmp_path / "state",
        slot_id=slots[0],
        api=SimpleNamespace(post=post),
        children={task["execution_id"]: object()},
    )
    from worker.process_logs import prepare

    task["process_logs_v1"] = True
    config = prepare(worker, task, root)
    assert (
        run(
            [sys.executable, "-c", "print('from real process')"],
            config={"_process_logs": config},
            scope="business",
        )
        == 0
    )
    service = LogService(worker)
    service.cycle()
    base = f"jobs/{job}/attempts/{task['run_id']}"
    entries = client.get(
        "/api/v1/" + base + "/log-catalog", headers=auth["alice"]
    ).json()["items"]
    entry = next(e for e in entries if e["scope"] == "business")
    ticket = client.post(
        "/api/v1/" + base + "/log-reads",
        headers=auth["alice"],
        json={"log_id": entry["id"]},
    ).json()
    service.cycle()
    page = client.get(
        "/api/v1/log-reads/" + ticket["read_id"], headers=auth["alice"]
    ).json()
    assert page["status"] == "ready"
    assert "from real process" in "".join(r["text"] for r in page["page"]["records"])


def test_host_backend_injects_log_snapshot(tmp_path, monkeypatch):
    from backend.tests.test_host_backend import worker as make_worker
    from worker.host_backend import HostBackend

    (tmp_path / "business.py").write_text("print(1)")
    w = make_worker(tmp_path, ["python3", "business.py"])
    w.slot_id = "slot"
    w.api = SimpleNamespace(post=lambda *args: {})
    w.state["task"]["process_logs_v1"] = True
    monkeypatch.setattr("worker.host_backend.probe", lambda *a: None)
    backend = HostBackend(w)
    backend.create(w.state["task"])
    config = json.loads(
        (
            w.workspace / "runs" / w.state["task"]["execution_id"] / "input.json"
        ).read_text()
    )
    assert config["_process_logs"]["root"].endswith("/logs")
    assert config["_process_logs"]["execution_id"] == w.state["task"]["execution_id"]


def test_store_rejects_symlink_and_hardlink_temp_without_touching_target(tmp_path):
    config = setup_log(tmp_path)
    target = tmp_path / "secret"
    target.write_text("unchanged")
    temporary = Path(config["root"]) / "manifest.tmp"
    os.link(target, temporary)
    Store(config, "business")
    assert target.read_text() == "unchanged"


def test_parent_exits_without_waiting_for_descendant_pipe(tmp_path):
    import time

    config = setup_log(tmp_path)
    started = time.monotonic()
    # Descendant only sleeps briefly, no lingering resource beyond this test.
    code = run(
        [
            sys.executable,
            "-c",
            "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(3)']);print('parent done')",
        ],
        config={"_process_logs": config},
        scope="business",
    )
    assert code == 0 and time.monotonic() - started < 3
    assert next(iter(index(config)["scopes"].values()))["status"] == "incomplete"


def test_terminal_scope_discovery_and_slot_routing(env, tmp_path):
    from backend.app.core import Run, now
    from datetime import timedelta

    main, c, auth = env
    token, slots = node(env)
    job = submit(env)
    task = claim(env, token, slots[0])
    generation = str(uuid.uuid4())
    root = "/runs/" + task["execution_id"] + "/logs"
    route = "/api/v1/worker/executions/" + task["execution_id"] + "/log-catalog"
    assert (
        c.post(
            route,
            headers=token,
            json=dict(scope="task", generation=generation, root_path=root),
        ).status_code
        == 200
    )
    with main.SessionLocal() as s:
        r = s.get(Run, task["run_id"])
        r.status = "FAILED"
        r.lease_expires_at = now() - timedelta(seconds=1)
        s.commit()
    body = dict(
        scope="business", generation=generation, root_path=root, status="closed"
    )
    entry = c.post(route, headers=token, json=body)
    assert entry.status_code == 200, entry.text
    assert (
        c.post(
            route, headers=token, json={**body, "scope": "service", "status": "open"}
        ).status_code
        == 409
    )
    assert (
        c.post(
            route,
            headers=token,
            json={**body, "scope": "service", "root_path": "/elsewhere"},
        ).status_code
        == 409
    )
    base = f"/api/v1/jobs/{job}/attempts/{task['run_id']}/log-reads"
    assert (
        c.post(
            base,
            headers=auth["alice"],
            json={"log_id": entry.json()["id"], "limit_bytes": 1024},
        ).status_code
        == 400
    )
    assert (
        c.post(
            base, headers=auth["alice"], json={"log_id": entry.json()["id"]}
        ).status_code
        == 202
    )
    assert (
        c.post(
            "/api/v1/worker/log-reads/claim",
            headers=token,
            json={"slot_id": "wrong-slot"},
        ).status_code
        == 404
    )
    got = c.post(
        "/api/v1/worker/log-reads/claim", headers=token, json={"slot_id": slots[0]}
    ).json()
    assert len(got["requests"]) == 1


def test_container_log_mount_identity_and_setup_failure(tmp_path):
    from worker.process_logs import prepare

    calls = []
    worker = SimpleNamespace(
        config={},
        path=tmp_path / "state",
        slot_id="slot",
        api=SimpleNamespace(post=lambda path, body: calls.append((path, body))),
    )
    task = {"execution_id": str(uuid.uuid4()), "process_logs_v1": True}
    root = tmp_path / "runs" / task["execution_id"] / "logs"
    settings = prepare(worker, task, root, container=True)
    assert settings["root"] == "/tmp/mini-eval-logs-" + task["execution_id"]
    assert calls[0][1]["root_path"] == str(root)
    assert root.stat().st_mode & 0o777 == 0o777
    assert (root / ".lock").stat().st_mode & 0o777 == 0o666
    other = {**task, "execution_id": str(uuid.uuid4())}
    worker.config["process_log_policy"] = {"execution_bytes": 0}
    assert prepare(worker, other, tmp_path / "invalid", container=True) is None
    assert prepare(worker, {**task, "process_logs_v1": False}, root) is None


@pytest.mark.parametrize("execution_mode", ["host", "docker"])
@pytest.mark.parametrize("integration", ["direct", "sdk-command", "command"])
@pytest.mark.parametrize(
    "failure,error_policy",
    [
        ("none", "stop"),
        ("exit", "stop"),
        ("exit", "continue"),
        ("missing", "stop"),
        ("missing", "continue"),
    ],
)
def test_process_log_launch_modes(
    tmp_path, execution_mode, integration, failure, error_policy
):
    """Real host/container entrypoints; SDK-command owns its episode loop.

    Container direct/ordinary commands run once with the full episode count.
    Host per_episode is deliberately NOT implied by running inside a container.
    """
    image = os.environ.get("PROCESS_LOG_TEST_IMAGE")
    if execution_mode == "docker" and not image:
        pytest.skip("set PROCESS_LOG_TEST_IMAGE for real Docker integration")
    config = setup_log(tmp_path)
    work, out = tmp_path / "work", tmp_path / "output"
    work.mkdir()
    out.mkdir()
    per_episode = integration == "sdk-command"
    script = tmp_path / "business.py"
    script.write_text(
        "import os,json,sys\nfrom pathlib import Path\n"
        "data=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())\n"
        "print('input-episodes='+str(data['episodes']))\n"
        "print('stderr-business',file=sys.stderr)\n"
        "i=os.environ.get('MINI_EVAL_EPISODE_ID','2')\n"
        + (
            "if i=='2':sys.exit(7)\n"
            if failure == "exit"
            else "if i=='2':sys.exit(0)\n"
            if failure == "missing"
            else ""
        )
        + (
            "Path(os.environ['MINI_EVAL_RESULT']).write_text('{\"success\":true}')\n"
            if per_episode
            else "print(json.dumps({'final':{'episode_count':3,'success_rate':1,'runtime':.1}}))\n"
        )
    )
    python = "python3" if execution_mode == "docker" else sys.executable
    task = dict(
        contract_version=1,
        service_mode="none",
        endpoint="",
        task_name="case",
        episodes=3,
        seed=7,
        parameters={},
        template=dict(
            execution_mode=execution_mode,
            integration_mode="direct" if integration == "command" else integration,
            task_type="command" if integration == "command" else "evaluation",
            host_runtime=dict(
                command_mode="exec",
                episode_launch="whole_task",
                episode_error_policy=error_policy,
            ),
            command=[python, str(script)],
        ),
        _resolved_command=[python, str(script)],
        _resolved_environment={},
        _resolved_cwd=str(tmp_path),
        _process_logs=config,
    )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(task))
    environment = dict(
        PYTHONPATH=str(Path.cwd()),
        MINI_EVAL_INPUT=str(path),
        MINI_EVAL_WORK_DIR=str(work),
        MINI_EVAL_OUTPUT_DIR=str(out),
    )
    if execution_mode == "docker":
        # Match non-root container permissions, while letting pytest clean artifacts.
        tmp_path.chmod(0o777)
        for p in (work, out, Path(config["root"])):
            p.chmod(0o777)
        (Path(config["root"]) / ".lock").chmod(0o666)
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--entrypoint",
            "python3",
            "--workdir",
            "/adapter",
            "--mount",
            f"type=bind,source={tmp_path},target={tmp_path}",
            "--mount",
            f"type=bind,source={Path.cwd() / 'runner'},target=/adapter/runner,readonly",
        ]
        environment["PYTHONPATH"] = "/adapter"
        for k, v in environment.items():
            command.extend(["-e", k + "=" + v])
        command.extend([image, "-m", "runner.cs_entry"])
    else:
        command = [python, "-m", "runner.cs_entry"]
    result = subprocess.run(
        command,
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        timeout=30,
    )
    failed = failure == "exit" or per_episode and failure == "missing"
    assert (result.returncode != 0) == failed, result.stderr
    entries = list(index(config)["scopes"].values())
    if per_episode:
        count = 2 if failed else 3
        assert len(entries) == count
        assert {e["scope"] for e in entries} == {"episode"}
        events = [
            json.loads(x) for x in (work / "episodes.jsonl").read_text().splitlines()
        ]
        assert len(events) == count * 2
        assert sum(e["status"] == "ERROR" for e in events) == int(failed)
    else:
        assert len(entries) == 1 and entries[0]["scope"] == "business"
        assert "input-episodes=3" in result.stdout
        assert not (work / "episodes.jsonl").exists()
        assert result.returncode == (7 if failure == "exit" else 0)
        # Missing protocol finals are not reclassified as OS process failures.
        assert sum('"final"' in x for x in result.stdout.splitlines()) == int(
            failure == "none"
        )
    assert all(e["status"] == "closed" for e in entries)


@pytest.mark.parametrize(
    "execution_mode,launch,error_policy",
    [
        ("host", "whole_task", "stop"),
        ("host", "whole_task", "continue"),
        ("host", "per_episode", "stop"),
        ("host", "per_episode", "continue"),
        ("docker", "whole_task", "stop"),
    ],
)
@pytest.mark.parametrize(
    "outcome", ["success", "business_false", "exception", "invalid_result", "exit"]
)
def test_native_sdk_process_logs(
    tmp_path, execution_mode, launch, error_policy, outcome
):
    """Exercise the actual EvaluationClient API, not the command adapter."""
    image = os.environ.get("PROCESS_LOG_TEST_IMAGE")
    if execution_mode == "docker" and not image:
        pytest.skip("set PROCESS_LOG_TEST_IMAGE for real Docker integration")
    config = setup_log(tmp_path)
    work, out = tmp_path / "work", tmp_path / "output"
    work.mkdir()
    out.mkdir()
    per_episode = launch == "per_episode"
    script = tmp_path / "native_client.py"
    script.write_text(
        """import json,os,sys
from pathlib import Path
from runner.sdk import EvaluationClient,EpisodeResult,run
class Client(EvaluationClient):
 def setup(self, context):
  print('NATIVE_SETUP', flush=True)
 def run_episode(self, task, episode, context):
  parent=int(os.environ.get('MINI_EVAL_EPISODE_ID',episode.id))
  print('NATIVE_CALL '+json.dumps({'parent':parent,'episode':episode.id,'seed':episode.seed,'episodes':task['episodes'],'business':context.business,'marker':os.environ.get('SDK_MARKER')}),flush=True)
  print('NATIVE_STDERR '+str(parent),file=sys.stderr,flush=True)
  if parent==2:
"""
        + {
            "success": "   pass\n",
            "business_false": "   return EpisodeResult(False)\n",
            "exception": "   raise RuntimeError('native test exception')\n",
            "invalid_result": "   return None\n",
            "exit": "   raise SystemExit(7)\n",
        }[outcome]
        + """  return EpisodeResult(True)
 def teardown(self):
  print('NATIVE_TEARDOWN',flush=True)
run(Client(),json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text()))
"""
    )
    python = "python3" if execution_mode == "docker" else sys.executable
    task = dict(
        contract_version=1,
        service_mode="none",
        endpoint="",
        task_name="native",
        episodes=3,
        seed=2147483647,
        parameters={"label": "native-param"},
        template=dict(
            execution_mode=execution_mode,
            integration_mode="direct",  # Native SDK programs emit the direct protocol.
            task_type="evaluation",
            host_runtime=dict(
                command_mode="exec",
                episode_launch=launch,
                episode_error_policy=error_policy,
            ),
            command=[python, str(script)],
        ),
        _resolved_command=[python, str(script)],
        _resolved_environment={},
        _resolved_cwd=str(tmp_path),
        _process_logs=config,
    )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(task))
    environment = dict(
        PYTHONPATH=str(Path.cwd()),
        MINI_EVAL_INPUT=str(path),
        MINI_EVAL_WORK_DIR=str(work),
        MINI_EVAL_OUTPUT_DIR=str(out),
        SDK_MARKER="native-env",
    )
    command = [python, "-m", "runner.cs_entry"]
    if execution_mode == "docker":
        tmp_path.chmod(0o777)
        for p in (work, out, Path(config["root"])):
            p.chmod(0o777)
        (Path(config["root"]) / ".lock").chmod(0o666)
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--entrypoint",
            "python3",
            "--workdir",
            "/adapter",
            "--mount",
            f"type=bind,source={tmp_path},target={tmp_path}",
            "--mount",
            f"type=bind,source={Path.cwd() / 'runner'},target=/adapter/runner,readonly",
        ]
        environment["PYTHONPATH"] = "/adapter"
        for k, v in environment.items():
            command.extend(["-e", k + "=" + v])
        command.extend([image, "-m", "runner.cs_entry"])
    result = subprocess.run(
        command,
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        timeout=30,
    )
    failed = outcome in ("exception", "invalid_result", "exit")
    continuing = per_episode and error_policy == "continue"
    count = 2 if failed and not continuing else 3
    assert (result.returncode != 0) == failed, result.stderr
    if outcome == "exit" and not per_episode:
        assert result.returncode == 7
    calls = [
        json.loads(line.removeprefix("NATIVE_CALL "))
        for line in result.stdout.splitlines()
        if line.startswith("NATIVE_CALL ")
    ]
    assert [c["parent"] for c in calls] == list(range(1, count + 1)), result.stderr
    assert [c["seed"] for c in calls] == [2147483647, 0, 1][:count]
    assert [c["episode"] for c in calls] == (
        [1] * count if per_episode else list(range(1, count + 1))
    )
    assert all(c["episodes"] == (1 if per_episode else 3) for c in calls)
    assert all(c["marker"] == "native-env" for c in calls)
    assert all(c["business"] == {"label": "native-param"} for c in calls)
    events = [
        json.loads(line) for line in (work / "episodes.jsonl").read_text().splitlines()
    ]
    # SystemExit bypasses the native SDK's Exception handler; the outer host
    # per-episode adapter still records the observed process exit as ERROR.
    expected = []
    for i in range(1, count + 1):
        expected.append("STARTED")
        if i == 2 and outcome == "exit" and not per_episode:
            continue
        expected.append("ERROR" if i == 2 and failed else "COMPLETED")
    assert [e["status"] for e in events] == expected
    published = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("MINI_EVAL_EVENT ")
    ]
    assert len(published) == len(events)  # no duplicated child events
    if outcome == "business_false":
        assert events[3]["status"] == "COMPLETED" and events[3]["success"] is False
    finals = [
        json.loads(line)["final"]
        for line in result.stdout.splitlines()
        if line.startswith("{") and '"final"' in line
    ]
    assert len(finals) == (0 if failed else 1)
    if finals:
        assert finals[0]["episode_count"] == 3
        assert finals[0]["success_rate"] == (
            2 / 3 if outcome == "business_false" else 1
        )
    entries = list(index(config)["scopes"].values())
    assert len(entries) == (count if per_episode else 1)
    assert {e["scope"] for e in entries} == (
        {"episode"} if per_episode else {"business"}
    )
    from runner.process_log_store import relative_directory

    for entry in entries:
        rows = [
            json.loads(line)
            for seg in entry["segments"]
            for line in (
                Path(config["root"])
                / relative_directory(entry["scope"], entry["episode_id"])
                / seg["name"]
            )
            .read_text()
            .splitlines()
        ]
        assert entry["status"] == "closed"
        assert any(
            r["stream"] == "stderr" and "NATIVE_STDERR" in r["text"] for r in rows
        )
        assert any(r["stream"] == "stdout" and "NATIVE_CALL" in r["text"] for r in rows)
