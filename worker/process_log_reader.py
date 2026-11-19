"""Bounded JSONL paging from a registered scope fd, never a requested pathname.

The caller owns the historical-root registry and execution cleanup lock. Segment
identities below must come from that registry/index, not a business-supplied
manifest. This standalone reader is not yet connected to the Worker loop.
"""

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import re
import stat
import time
from runner.process_log_contract import (
    identity,
    relative_directory,
    absolute_directory,
    PAGE_BYTES,
    RECORD_BYTES,
)


@dataclass(frozen=True)
class Segment:
    name: str
    dev: int
    ino: int
    first: int
    next: int
    size: int


@contextmanager
def registered_scope(root, root_identity, scope, episode_id=0):
    """Traverse using no-follow fds and pin the registered root inode."""
    absolute_directory(root)
    relative = relative_directory(scope, episode_id)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.split("/")[1:]:
            if not part:
                continue
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != tuple(root_identity):
            raise ValueError("registered root changed")
        for part in relative.split("/"):
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            if os.fstat(new).st_dev != current.st_dev:
                os.close(new)
                raise ValueError("scope crosses filesystem")
            os.close(fd)
            fd = new
        yield fd
    finally:
        os.close(fd)


def _token(key, generation, segment, sequence, offset):
    return base64.urlsafe_b64encode(
        json.dumps(
            [key, generation, segment.name, segment.ino, sequence, offset],
            separators=(",", ":"),
        ).encode()
    ).decode()


def _position(value, key, generation):
    try:
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError()
        v = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if (
            not isinstance(v, list)
            or len(v) != 6
            or v[:2] != [key, generation]
            or not isinstance(v[2], str)
        ):
            raise ValueError()
        if any(type(n) is not int or not 0 <= n < 2**63 for n in v[3:]):
            raise ValueError()
        return v[2:]
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid or superseded cursor") from exc


def read_page(
    scope_fd,
    execution_id,
    scope,
    generation,
    segments,
    *,
    episode_id=0,
    cursor=None,
    direction="forward",
    stream="all",
    limit_bytes=PAGE_BYTES,
    until=None,
):
    from uuid import UUID

    key = identity(execution_id, scope, episode_id)
    if str(UUID(generation)) != generation:
        raise ValueError("invalid generation")
    if direction not in ("forward", "tail", "backward") or stream not in (
        "all",
        "stdout",
        "stderr",
        "system",
    ):
        raise ValueError("invalid query")
    if type(limit_bytes) is not int or not 16384 <= limit_bytes <= PAGE_BYTES:
        raise ValueError("page must be 16–64 KiB")
    if until is not None and (type(until) is not int or not 0 <= until < 2**63):
        raise ValueError("invalid boundary")
    # Index is bounded by the execution budget; reject malformed identities early.
    if len(segments) > 4096:
        raise ValueError("oversized segment index")
    previous = 0
    names = set()
    for seg in segments:
        if not re.fullmatch(r"log-[0-9]{6,20}\.jsonl", seg.name) or seg.name in names:
            raise ValueError("invalid segment name")
        if (
            any(
                type(n) is not int or n < 0
                for n in (seg.dev, seg.ino, seg.first, seg.next, seg.size)
            )
            or not previous <= seg.first <= seg.next < 2**63
        ):
            raise ValueError("invalid segment range")
        previous = seg.next
        names.add(seg.name)
    first = segments[0].first if segments else 0
    last = segments[-1].next if segments else 0
    position = _position(cursor, key, generation) if cursor else None
    gap = False
    if not segments:
        return dict(
            records=[],
            next_cursor=None,
            previous_cursor=None,
            has_more=False,
            first_available=first,
            next_sequence=last,
            gap=bool(position),
            source="node",
        )
    if position:
        name, inode, seq, offset = position
        selected = next(
            (i for i, s in enumerate(segments) if s.name == name and s.ino == inode),
            None,
        )
        if selected is None:
            if seq >= first:
                raise ValueError("segment identity changed")
            gap = True
            position = None
    if position:
        seg = segments[selected]
        if (
            not seg.first <= seq <= seg.next
            or offset > seg.size
            or (offset == 0 and seq != seg.first)
            or (offset == seg.size and seq != seg.next)
        ):
            raise ValueError("cursor outside segment")
    else:
        selected = (
            len(segments) - 1 if direction in ("tail", "backward") and not gap else 0
        )
        seg = segments[selected]
        offset = seg.size if direction in ("tail", "backward") and not gap else 0
        seq = seg.next if offset else seg.first
    reverse = direction in ("tail", "backward") and not gap
    if selected > 0 and seg.first > segments[selected - 1].next:
        gap = True
    records = []
    spent = 2048
    scanned = 0
    deadline = time.monotonic() + 2
    start_seq, start_offset = seq, offset
    fd = os.open(seg.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=scope_fd)
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or (st.st_dev, st.st_ino) != (seg.dev, seg.ino)
            or st.st_size < seg.size
        ):
            raise ValueError("segment replaced or truncated")
        while scanned < PAGE_BYTES and time.monotonic() < deadline:
            if not reverse and until is not None and seq >= until:
                break
            if (reverse and offset == 0) or (not reverse and offset >= seg.size):
                break
            if reverse:
                begin = max(0, offset - RECORD_BYTES)
                data = os.pread(fd, offset - begin, begin)
                if not data.endswith(b"\n"):
                    raise ValueError("partial log record")
                split = data.rfind(b"\n", 0, len(data) - 1)
                line = data[split + 1 :]
                new_offset = begin + split + 1
                expected = seq - 1
                if split < 0 and begin != 0:
                    raise ValueError("oversized log record")
            else:
                data = os.pread(fd, min(RECORD_BYTES, seg.size - offset), offset)
                end = data.find(b"\n")
                if end < 0:
                    raise ValueError("partial or oversized log record")
                line = data[: end + 1]
                new_offset = offset + len(line)
                expected = seq
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("invalid JSONL record") from exc
            if (
                not isinstance(row, dict)
                or type(row.get("sequence")) is not int
                or not seg.first <= row["sequence"] < seg.next
                or (
                    row["sequence"] > expected
                    if reverse
                    else row["sequence"] < expected
                )
                or row.get("stream") not in ("stdout", "stderr", "system")
                or not isinstance(row.get("text"), str)
                or not isinstance(row.get("time"), str)
                or len(row["time"]) > 64
                or type(row.get("gap")) is not bool
            ):
                raise ValueError("record identity or format mismatch")
            # Only expose bounded format fields, never arbitrary manifest content.
            row = {k: row[k] for k in ("sequence", "time", "stream", "text", "gap")}
            matched = (stream == "all" or row["stream"] == stream) and (
                until is None or row["sequence"] < until
            )
            size = len(json.dumps(row, ensure_ascii=True).encode()) + 2
            if scanned + len(line) > PAGE_BYTES:
                break
            if matched and spent + size > limit_bytes:
                break
            if matched:
                records.append(row)
                spent += size
            gap = gap or row["gap"] or row["sequence"] != expected
            scanned += len(line)
            offset = new_offset
            seq = row["sequence"] if reverse else row["sequence"] + 1
        if (offset == 0 and seq != seg.first) or (
            offset == seg.size and seq != seg.next
        ):
            raise ValueError("segment range does not match records")
        after = os.fstat(fd)
        if after.st_nlink != 1 or after.st_size < seg.size:
            raise ValueError("segment removed during read")
    finally:
        os.close(fd)
    if reverse:
        records.reverse()
    low_seq, low_offset = (seq, offset) if reverse else (start_seq, start_offset)
    high_seq, high_offset = (start_seq, start_offset) if reverse else (seq, offset)
    prev = _token(key, generation, seg, low_seq, low_offset)
    nex = _token(key, generation, seg, high_seq, high_offset)
    if low_offset == 0 and selected > 0:
        old = segments[selected - 1]
        prev = _token(key, generation, old, old.next, old.size)
    if high_offset >= seg.size and selected + 1 < len(segments):
        new = segments[selected + 1]
        nex = _token(key, generation, new, new.first, 0)
    result = dict(
        records=records,
        next_cursor=nex,
        previous_cursor=prev,
        has_more=(low_offset > 0 or selected > 0)
        if reverse
        else (
            (until is None or high_seq < until)
            and (high_offset < seg.size or selected + 1 < len(segments))
        ),
        first_available=first,
        next_sequence=last,
        gap=gap,
        source="node",
    )
    if len(json.dumps(result, ensure_ascii=True).encode()) > limit_bytes:
        raise ValueError("page budget exceeded")
    return result
