"""Legacy container collection remains bounded and closes failed copy processes."""
import io
import tarfile
from types import SimpleNamespace
import pytest
from worker import artifacts


def archive(entries):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as output:
        for name,kind,data in entries:
            item=tarfile.TarInfo(name)
            if kind=='directory':item.type=tarfile.DIRTYPE;output.addfile(item)
            else:item.size=len(data);output.addfile(item,io.BytesIO(data))
    return stream.getvalue()


def copy_process(monkeypatch,data,stderr=b'',exit_code=0,alive=False):
    state=SimpleNamespace(killed=False,cancelled=False,waits=0)
    class Process:
        def __init__(self):
            self.stdout=io.BytesIO(data);self.stderr=io.BytesIO(stderr)
        def poll(self):return None if alive and not state.killed else exit_code
        def kill(self):state.killed=True
        def wait(self,**kwargs):state.waits+=1;return exit_code
    class Timer:
        def __init__(self,seconds,callback):assert seconds==20
        def start(self):pass
        def cancel(self):state.cancelled=True
    process=Process()
    monkeypatch.setattr(artifacts.subprocess,'Popen',lambda *a,**k:process)
    monkeypatch.setattr(artifacts.threading,'Timer',Timer)
    return process,state


@pytest.mark.parametrize('entries,message',[
    ([('other/file','file',b'x')],'invalid artifact archive root'),
    ([('mini-eval-output/../bad','directory',b'')],'invalid artifact directory'),
    ([('mini-eval-output/x','file',b'x')]*2,'duplicate artifact name'),
    ([('mini-eval-output/d','directory',b'')]*129,'too many entries'),
])
def test_invalid_archive_closes_and_kills_copy(monkeypatch,entries,message):
    process,state=copy_process(monkeypatch,archive(entries),alive=True)
    with pytest.raises(RuntimeError,match=message):artifacts.read_artifacts('container')
    assert state.killed and state.cancelled and state.waits==1
    assert process.stdout.closed and process.stderr.closed


@pytest.mark.parametrize('missing',[True,False])
def test_unreadable_archive_distinguishes_missing_output(monkeypatch,missing):
    process,state=copy_process(monkeypatch,b'not a tar archive',stderr=b'Could not find the file' if missing else b'docker unavailable')
    if missing:assert artifacts.read_artifacts('container')==[]
    else:
        with pytest.raises(RuntimeError,match='could not read client artifacts'):artifacts.read_artifacts('container')
    assert state.cancelled and process.stdout.closed and process.stderr.closed


def test_nonzero_copy_exit_does_not_return_partial_files(monkeypatch):
    process,state=copy_process(monkeypatch,archive([('mini-eval-output/x','file',b'x')]),exit_code=1)
    with pytest.raises(RuntimeError,match='copy failed'):artifacts.read_artifacts('container')
    assert state.waits==2 and state.cancelled and process.stdout.closed


def test_truncated_file_is_rejected_even_with_valid_header(monkeypatch):
    process,state=copy_process(monkeypatch,b'')
    item=tarfile.TarInfo('mini-eval-output/x');item.size=4
    class Archive:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def __iter__(self):return iter([item])
        def extractfile(self,item):return io.BytesIO(b'x')
    monkeypatch.setattr(artifacts.tarfile,'open',lambda **kw:Archive())
    with pytest.raises(RuntimeError,match='truncated artifact'):artifacts.read_artifacts('container')
    assert state.cancelled and process.stdout.closed


def test_manifest_cleanup_failure_is_best_effort(tmp_path,monkeypatch):
    from runner import artifact_manifest
    monkeypatch.setattr(artifact_manifest.os,'unlink',lambda *a,**k:(_ for _ in ()).throw(OSError('read only')))
    assert artifact_manifest.register(tmp_path,1) is False
    # The leftover hard link is deliberately untrusted; retry must not rebind it.
    assert artifact_manifest.register(tmp_path,1) is False
    assert artifact_manifest.register(tmp_path,2) is False
