"""Business users may traverse execution parents without listing sibling runs."""
import os
import sys
import uuid

import pytest

from runner.process_log_run import run
from runner.process_log_store import initialize, locked, read_json


@pytest.mark.skipif(os.geteuid() == 0, reason="requires real non-root DAC enforcement")
@pytest.mark.parametrize("scope,episode", [("service", 0), ("business", 0), ("episode", 2)])
def test_capture_through_search_only_ancestors(tmp_path, scope, episode):
    workspace = tmp_path / "workspace"
    runs = workspace / "runs"
    root = runs / str(uuid.uuid4()) / "logs"
    execution, generation = root.parent.name, str(uuid.uuid4())
    initialize(root, execution, generation)
    workspace.chmod(0o100)
    runs.chmod(0o100)
    try:
        # Sibling execution names remain hidden to the business identity.
        with pytest.raises(PermissionError):
            list(runs.iterdir())
        code = run(
            [sys.executable, "-c", "import sys;print('out');print('err',file=sys.stderr)"],
            config={"_process_logs": dict(root=str(root), execution_id=execution, generation=generation)},
            scope=scope,
            episode=episode,
        )
        assert code == 0
        with locked(root, False) as fd:
            manifest = read_json(fd, "manifest.json")
        entry = manifest["scopes"][f"{execution}/{scope}/{episode}"]
        assert entry["status"] == "closed"
        assert entry["next_sequence"] == 2
    finally:
        workspace.chmod(0o700)
        runs.chmod(0o700)


@pytest.mark.parametrize("kind", ["ancestor", "root", "file"])
def test_traversal_still_rejects_links_and_non_directories(tmp_path, kind):
    actual = tmp_path / "actual"
    root = actual / "logs"
    initialize(root, str(uuid.uuid4()), str(uuid.uuid4()))
    alias = tmp_path / "alias"
    if kind == "ancestor":
        alias.symlink_to(actual, target_is_directory=True)
        path = alias / "logs"
    elif kind == "root":
        alias.symlink_to(root, target_is_directory=True)
        path = alias
    else:
        alias.write_text("not a directory")
        path = alias / "logs"
    with pytest.raises(OSError):
        with locked(path):
            pytest.fail("untrusted path was followed")
    assert not (tmp_path / ".lock").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="requires real non-root DAC enforcement")
def test_traversal_does_not_bypass_missing_search_permission(tmp_path):
    parent = tmp_path / "private"
    root = parent / "logs"
    initialize(root, str(uuid.uuid4()), str(uuid.uuid4()))
    parent.chmod(0o400)
    try:
        with pytest.raises(PermissionError):
            with locked(root):
                pytest.fail("search permission was bypassed")
    finally:
        parent.chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="requires real non-root DAC enforcement")
def test_final_log_directory_still_requires_read_permission(tmp_path):
    root = tmp_path / "logs"
    initialize(root, str(uuid.uuid4()), str(uuid.uuid4()))
    root.chmod(0o300)
    try:
        with pytest.raises(PermissionError):
            with locked(root):
                pytest.fail("log root read permission was bypassed")
    finally:
        root.chmod(0o700)
