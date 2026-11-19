"""Per-execution JSONL storage. No node-wide quota or privileged state access."""

from contextlib import contextmanager
import fcntl
import json
import os
import re
from pathlib import Path
import stat
from uuid import UUID

from .process_log_contract import identity, relative_directory, RECORD_BYTES

DEFAULTS = dict(
    segment_bytes=4 * 1024**2, scope_bytes=16 * 1024**2, execution_bytes=256 * 1024**2
)
MAX_INDEX = 8 * 1024**2


def policy(value=None):
    result = {**DEFAULTS, **(value or {})}
    if set(result) != set(DEFAULTS) or any(type(n) is not int for n in result.values()):
        raise ValueError("invalid log policy")
    if (
        not 65536
        <= result["segment_bytes"]
        <= result["scope_bytes"]
        <= result["execution_bytes"]
        <= 1024**4
    ):
        raise ValueError("invalid log limits")
    return result


def read_json(fd, name, limit=MAX_INDEX):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        st = os.fstat(handle)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
            raise ValueError("invalid log index")
        data = os.read(handle, limit + 1)
        if len(data) > limit:
            raise ValueError("oversized log index")
        return json.loads(data)
    finally:
        os.close(handle)


def atomic(fd, value, name="manifest.json"):
    if name not in ("manifest.json", "prune.json"):
        raise ValueError("invalid metadata name")
    temporary = name.removesuffix(".json") + ".tmp"
    data = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > MAX_INDEX:
        raise ValueError("log index exhausted")
    try:
        os.unlink(temporary, dir_fd=fd)
    except FileNotFoundError:
        pass
    handle = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
        dir_fd=fd,
    )
    try:
        if os.fstat(handle).st_nlink != 1:
            raise ValueError("linked log index")
        with os.fdopen(handle, "wb", closefd=False) as out:
            out.write(data)
            out.flush()
            os.fsync(handle)
    finally:
        os.close(handle)
    os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


@contextmanager
def locked(root, exclusive=True, wait=False):
    # Ancestors need search permission, not permission to list sibling executions.
    # O_PATH still pins each directory; O_DIRECTORY | O_NOFOLLOW rejects links.
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY)
    lock = None
    try:
        path = Path(root)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("invalid log root")
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = readable
        lock = os.open(
            ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        if not stat.S_ISREG(os.fstat(lock).st_mode) or os.fstat(lock).st_nlink != 1:
            raise ValueError("invalid log lock")
        fcntl.flock(
            lock,
            (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            | (0 if wait else fcntl.LOCK_NB),
        )
        yield fd
    finally:
        if lock is not None:
            os.close(lock)
        os.close(fd)


def initialize(root, execution, generation, limits=None):
    identity(execution, "task")
    UUID(generation)
    limits = policy(limits)
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with locked(root) as fd:
        try:
            previous = read_json(fd, "manifest.json")
        except FileNotFoundError:
            previous = None
        if previous is not None:
            if (
                previous["execution_id"] != execution
                or previous["generation"] != generation
            ):
                raise ValueError("log identity changed")
            return
        atomic(
            fd,
            dict(
                version=1,
                execution_id=execution,
                generation=generation,
                policy=limits,
                scopes={},
            ),
        )


def scope_fd(rootfd, scope, episode, create=False):
    current = os.dup(rootfd)
    try:
        for part in relative_directory(scope, episode).split("/"):
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def recover_pruning(fd, index):
    """Finish committed pruning, or retain victims if the index did not commit.

    Caller owns the execution lock. A durable intent is written before replacing
    the index; old files are removed only after the new index is committed.
    """
    try:
        pending = read_json(fd, "prune.json")
    except FileNotFoundError:
        return
    if (
        pending.get("execution_id") != index["execution_id"]
        or pending.get("generation") != index["generation"]
    ):
        raise ValueError("prune identity changed")
    victims = pending.get("victims")
    if not isinstance(victims, list) or len(victims) > 10003:
        raise ValueError("invalid prune intent")
    for victim in victims:
        key = identity(index["execution_id"], victim["scope"], victim["episode_id"])
        name = victim["name"]
        if not isinstance(name, str) or not re.fullmatch(r"log-[0-9]{6,}\.jsonl", name):
            raise ValueError("invalid prune segment")
        entry = index["scopes"].get(key)
        if entry is None:
            raise ValueError("prune scope missing")
        if any(segment["name"] == name for segment in entry["segments"]):
            continue  # The old index still owns this file: commit failed.
        directory = scope_fd(fd, victim["scope"], victim["episode_id"])
        try:
            try:
                st = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue  # A previous cleanup removed it before interruption.
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_nlink != 1
                or (st.st_dev, st.st_ino) != (victim["dev"], victim["ino"])
            ):
                raise ValueError("prune segment replaced")
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)
    os.unlink("prune.json", dir_fd=fd)
    os.fsync(fd)


class Store:
    def __init__(self, config, scope, episode=0):
        self.root = config["root"]
        self.execution = config["execution_id"]
        self.generation = config["generation"]
        self.key = identity(self.execution, scope, episode)
        self.scope = scope
        self.episode = episode
        with locked(self.root) as fd:
            index = self.index(fd)
            if self.key in index["scopes"]:
                raise ValueError("scope already created; refusing duplicate capture")
            if len(index["scopes"]) >= 10003:
                raise ValueError("scope count exhausted")
            directory = scope_fd(fd, scope, episode, True)
            os.close(directory)
            index["scopes"][self.key] = dict(
                scope=scope,
                episode_id=episode,
                status="open",
                segments=[],
                first_sequence=0,
                next_sequence=0,
                dropped_bytes=0,
                truncated=False,
                serial=0,
            )
            self.save(fd, index)

    def index(self, fd):
        value = read_json(fd, "manifest.json")
        if (
            value["execution_id"] != self.execution
            or value["generation"] != self.generation
        ):
            raise ValueError("log identity changed")
        policy(value["policy"])
        recover_pruning(fd, value)
        return value

    def save(self, fd, index):
        # Reserve metadata in the execution budget including atomic replacement.
        size = len(json.dumps(index, separators=(",", ":")).encode())
        if size * 2 > min(MAX_INDEX * 2, index["policy"]["execution_bytes"] // 4):
            raise ValueError("log metadata budget exhausted")
        atomic(fd, index)

    def write(self, record):
        if len(record) > RECORD_BYTES:
            raise ValueError("oversized log record")
        row = json.loads(record)
        with locked(self.root, wait=True) as fd:
            index = self.index(fd)
            entry = index["scopes"][self.key]
            limits = index["policy"]
            if entry["status"] != "open" or row["sequence"] < entry["next_sequence"]:
                raise ValueError("invalid log sequence")
            segments = entry["segments"]
            if (
                not segments
                or segments[-1]["size"] + len(record) > limits["segment_bytes"]
            ):
                entry["serial"] += 1
                segments.append(
                    dict(
                        name=f"log-{entry['serial']:06d}.jsonl",
                        first=row["sequence"],
                        next=row["sequence"],
                        size=0,
                    )
                )
            directory = scope_fd(fd, self.scope, self.episode)
            try:
                seg = segments[-1]
                handle = os.open(
                    seg["name"],
                    os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
                    0o644,
                    dir_fd=directory,
                )
                try:
                    st = os.fstat(handle)
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        raise ValueError("invalid log segment")
                    if "ino" in seg and (st.st_dev, st.st_ino) != (
                        seg["dev"],
                        seg["ino"],
                    ):
                        raise ValueError("log segment replaced")
                    # Discard only the uncommitted suffix after an interrupted write.
                    if st.st_size < seg["size"]:
                        raise ValueError("log segment truncated")
                    os.ftruncate(handle, seg["size"])
                    os.lseek(handle, seg["size"], os.SEEK_SET)
                    view = memoryview(record)
                    while view:
                        count = os.write(handle, view)
                        if count <= 0:
                            raise OSError("short log write")
                        view = view[count:]
                    seg.update(
                        size=seg["size"] + len(record),
                        next=row["sequence"] + 1,
                        dev=st.st_dev,
                        ino=st.st_ino,
                    )
                finally:
                    os.close(handle)
            finally:
                os.close(directory)
            entry["next_sequence"] = row["sequence"] + 1

            victims = []

            def remove(owner):
                victim = owner["segments"].pop(0)
                victims.append(
                    {
                        **victim,
                        "scope": owner["scope"],
                        "episode_id": owner["episode_id"],
                    }
                )
                owner["truncated"] = True
                owner["first_sequence"] = (
                    owner["segments"][0]["first"]
                    if owner["segments"]
                    else owner["next_sequence"]
                )

            while sum(s["size"] for s in segments) > limits["scope_bytes"] - 4096:
                remove(entry)

            def total():
                return sum(
                    s["size"] for e in index["scopes"].values() for s in e["segments"]
                )

            while total() > limits["execution_bytes"] * 3 // 4:
                candidates = [e for e in index["scopes"].values() if e["segments"]]
                owner = next(
                    (e for e in candidates if e["status"] != "open"), candidates[0]
                )
                remove(owner)
            if segments:
                entry["first_sequence"] = segments[0]["first"]
            if victims:
                atomic(
                    fd,
                    dict(
                        execution_id=self.execution,
                        generation=self.generation,
                        victims=victims,
                    ),
                    "prune.json",
                )
            self.save(fd, index)
            if victims:
                recover_pruning(fd, index)

    def close(self, state):
        with locked(self.root) as fd:
            index = self.index(fd)
            entry = index["scopes"][self.key]
            entry.update(
                status="incomplete" if state["incomplete"] else "closed",
                dropped_bytes=state["dropped_bytes"],
            )
            self.save(fd, index)
