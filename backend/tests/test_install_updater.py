import json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.tests.test_updater_executor import fixture
from scripts import install_worker_updater


def test_installer_keeps_existing_nfs_unit_and_worker_credentials(tmp_path,monkeypatch):
    updater,_,credential,_,_=fixture(tmp_path)
    home=tmp_path/'home';home.mkdir();units=home/'.config/systemd/user';units.mkdir(parents=True)
    original=units/'mini-eval-worker-nfs.service';original.write_text('RequiresMountsFor=/mnt/eval-share\n')
    config=tmp_path/'updater-config.json';config.write_text(json.dumps(updater.config))
    before=credential.read_bytes()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    monkeypatch.setattr(sys,'argv',['install-updater','--config',str(config)])
    install_worker_updater.main()
    assert original.read_text()=='RequiresMountsFor=/mnt/eval-share\n' and credential.read_bytes()==before
    assert (units/'mini-eval-updater.service').is_file()
    assert (home/'.local/share/mini-eval-updater/updater.py').is_file()
    with pytest.raises(SystemExit):install_worker_updater.main()
    monkeypatch.setattr(sys,'argv',['install-updater','--config',str(config),'--update'])
    monkeypatch.setattr(install_worker_updater.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0))
    with pytest.raises(SystemExit):install_worker_updater.main()
