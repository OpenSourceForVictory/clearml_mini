"""Historical log mailbox worker, independent of execution leases and heartbeats."""

import fcntl
import json
import logging
import os
from pathlib import Path
import threading
import uuid

from runner.process_log_store import initialize, locked, read_json, scope_fd
from runner.process_log_contract import identity
from .process_log_reader import Segment, read_page
from .storage import atomic_json
from .retention import open_path, private_dir

LOG = logging.getLogger(__name__)


def registry(worker):
    path = Path(
        str(worker.config.get("_supervisor_state", worker.path)) + ".process-logs"
    )
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def prepare(worker, task, root, container=False):
    try:
        return prepare_checked(worker, task, root, container)
    except Exception:
        LOG.warning(
            "process log setup unavailable; retaining legacy execution logs",
            exc_info=False,
        )
        return None


def prepare_checked(worker, task, root, container=False):
    if not task.get("process_logs_v1"):
        return None
    path = registry(worker) / (str(uuid.UUID(task["execution_id"])) + ".json")
    if path.exists():
        item = json.loads(path.read_text())
    else:
        generation = str(uuid.uuid4())
        initialize(
            root,
            task["execution_id"],
            generation,
            worker.config.get("process_log_policy"),
        )
        from runner.process_log_store import Store
        from runner.process_log_capture import RecordEncoder

        startup = Store(
            dict(
                root=str(root), execution_id=task["execution_id"], generation=generation
            ),
            "task",
        )
        for record in RecordEncoder().feed(
            "system", b"process log capture prepared\n", final=True
        ):
            startup.write(record)
        if container:
            Path(root).chmod(0o777)
            (Path(root) / ".lock").chmod(0o666)
        st = Path(root).stat()
        item = dict(
            execution_id=task["execution_id"],
            generation=generation,
            root=str(root),
            root_identity=[st.st_dev, st.st_ino],
            slot_id=worker.slot_id,
            catalogs={},
        )
        atomic_json(path, item)
    # Register an execution-level identity while the lease is live. Later closed
    # scope discovery may then register without resurrecting the execution.
    worker.api.post(
        f"worker/executions/{task['execution_id']}/log-catalog",
        dict(scope="task", generation=item["generation"], root_path=item["root"]),
    )
    return dict(
        execution_id=item["execution_id"],
        generation=item["generation"],
        root="/tmp/mini-eval-logs-" + item["execution_id"]
        if container
        else item["root"],
    )


class LogService:
    def __init__(self, worker):
        self.worker = worker
        self.stopping = threading.Event()
        self.directory = registry(worker)

    def start(self):
        self.thread = threading.Thread(
            target=self.loop, name="historical-process-logs", daemon=True
        )
        self.thread.start()

    def close(self):
        self.stopping.set()
        if hasattr(self, "thread"):
            self.thread.join(timeout=2)

    def read_index(self, item):
        with locked(item["root"], exclusive=False) as fd:
            st = os.fstat(fd)
            if [st.st_dev, st.st_ino] != item["root_identity"]:
                raise ValueError("log root replaced")
            index = read_json(fd, "manifest.json")
            if (
                index["execution_id"] != item["execution_id"]
                or index["generation"] != item["generation"]
            ):
                raise ValueError("log generation changed")
            if not isinstance(index["scopes"], dict) or len(index["scopes"]) > 10003:
                raise ValueError("invalid log index")
            return index

    def sync(self, path, item):
        try:
            active = item["execution_id"] in getattr(self.worker, "children", {})
            if not active:
                with locked(item["root"]) as fd:
                    st = os.fstat(fd)
                    if [st.st_dev, st.st_ino] != item["root_identity"]:
                        raise ValueError("log root replaced")
                    index = read_json(fd, "manifest.json")
                    if (
                        index["execution_id"] != item["execution_id"]
                        or index["generation"] != item["generation"]
                    ):
                        raise ValueError("log generation changed")
                    changed = False
                    for e in index["scopes"].values():
                        if e["status"] == "open":
                            e["status"] = (
                                "closed" if e["scope"] == "task" else "incomplete"
                            )
                            changed = True
                    if changed:
                        from runner.process_log_store import atomic

                        atomic(fd, index)
            index = self.read_index(item)
            synced = 0
            for key, entry in index["scopes"].items():
                if key != identity(
                    item["execution_id"], entry["scope"], entry["episode_id"]
                ):
                    raise ValueError("scope identity changed")
                body = {
                    k: entry[k]
                    for k in (
                        "scope",
                        "episode_id",
                        "status",
                        "first_sequence",
                        "next_sequence",
                        "dropped_bytes",
                        "truncated",
                    )
                }
                body.update(generation=item["generation"], root_path=item["root"])
                if item["catalogs"].get(key) == body:
                    continue
                self.worker.api.post(
                    f"worker/executions/{item['execution_id']}/log-catalog", body
                )
                item["catalogs"][key] = body
                synced += 1
                if synced >= 16:
                    break
            atomic_json(path, item)
        except FileNotFoundError:
            if Path(item["root"]).exists():
                raise ValueError("log index missing")
            # A trusted registered directory disappeared (including staging).
            for body in [
                dict(
                    scope="task", generation=item["generation"], root_path=item["root"]
                ),
                *item["catalogs"].values(),
            ]:
                self.worker.api.post(
                    f"worker/executions/{item['execution_id']}/log-catalog",
                    {**body, "status": "cleaned"},
                )
            path.unlink(missing_ok=True)

    def read(self, item, request):
        # Share the execution deletion lock, not a global lock or DB connection.
        runs = Path(item["root"]).parent.parent
        parent = open_path(runs)
        lock = None
        try:
            locks = private_dir(parent, ".retention-locks")
            try:
                lock = os.open(
                    item["execution_id"],
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=locks,
                )
            finally:
                os.close(locks)
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with locked(item["root"], exclusive=False) as fd:
                st = os.fstat(fd)
                if [st.st_dev, st.st_ino] != item["root_identity"]:
                    raise ValueError("root identity changed")
                index = read_json(fd, "manifest.json")
                if (
                    index["generation"] != request["generation"]
                    or index["execution_id"] != item["execution_id"]
                ):
                    raise ValueError("generation changed")
                key = identity(
                    item["execution_id"], request["scope"], request["episode_id"]
                )
                entry = index["scopes"].get(key)
                if entry is None:
                    return "missing", {}
                segments = [
                    Segment(
                        **{
                            k: s[k]
                            for k in ("name", "dev", "ino", "first", "next", "size")
                        }
                    )
                    for s in entry["segments"]
                ]
                directory = scope_fd(fd, request["scope"], request["episode_id"])
                try:
                    query = {k: v for k, v in request["query"].items() if k != "log_id"}
                    page = read_page(
                        directory,
                        item["execution_id"],
                        request["scope"],
                        request["generation"],
                        segments,
                        episode_id=request["episode_id"],
                        **query,
                    )
                finally:
                    os.close(directory)
                page.update(
                    closed=entry["status"] != "open",
                    truncated=entry["truncated"],
                    dropped_bytes=entry["dropped_bytes"],
                )
                return "ready", page
        finally:
            if lock is not None:
                os.close(lock)
            os.close(parent)

    def cycle(self):
        for path in self.directory.glob("*.json"):
            if self.stopping.is_set():
                return
            try:
                self.sync(path, json.loads(path.read_text()))
            except Exception as exc:
                if getattr(exc, "status", None) == 404:
                    path.unlink(missing_ok=True)
                else:
                    LOG.warning("process log catalog sync deferred", exc_info=False)
        if not any(self.directory.glob("*.json")):
            return False
        requests = self.worker.api.post(
            "worker/log-reads/claim", {"slot_id": self.worker.slot_id}
        )["requests"]
        for request in requests:
            try:
                execution = str(uuid.UUID(request["execution_id"]))
                item = json.loads((self.directory / (execution + ".json")).read_text())
                if item["slot_id"] != self.worker.slot_id:
                    raise ValueError("slot mismatch")
                status, page = self.read(item, request)
            except BlockingIOError:
                status, page = "busy", {}
            except FileNotFoundError:
                status, page = "cleaned", {}
            except Exception:
                status, page = "failed", {}
            self.worker.api.post(
                f"worker/log-reads/{request['id']}/reply",
                dict(
                    claim=request["claim"],
                    generation=request["generation"],
                    status=status,
                    page=page,
                ),
            )

        return bool(requests)

    def loop(self):
        while not self.stopping.is_set():
            try:
                self.cycle()
            except Exception:
                LOG.warning("process log reads deferred", exc_info=False)
            self.stopping.wait(2 if self.worker.children else 5)
