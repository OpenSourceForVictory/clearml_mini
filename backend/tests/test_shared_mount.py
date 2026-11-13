import json
from types import SimpleNamespace
import pytest
from scripts.check_shared_mount import check


@pytest.mark.parametrize('failure',[None,'missing','source','filesystem','readonly','marker','symlink'])
def test_shared_volume_guard(tmp_path,monkeypatch,failure):
    root=tmp_path/'share';root.mkdir();marker=root/'.mini-eval-volume-id';marker.write_text('expected\n')
    mount={'target':str(root),'source':'server:/share','fstype':'nfs4','options':'rw,hard'}
    if failure=='source':mount['source']='wrong:/share'
    if failure=='filesystem':mount['fstype']='ext4'
    if failure=='readonly':mount['options']='ro,hard'
    if failure=='marker':marker.write_text('different')
    if failure=='symlink':marker.unlink();marker.symlink_to(tmp_path/'elsewhere')
    monkeypatch.setattr('scripts.check_shared_mount.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=1 if failure=='missing' else 0,stdout=json.dumps({'filesystems':[mount]})))
    if failure:
        with pytest.raises(ValueError):check(root,'server:/share','nfs4','expected')
    else:check(root,'server:/share','nfs4','expected')

@pytest.mark.parametrize('actual_uuid',['expected','other',None])
def test_local_volume_uses_uuid_not_device_name(tmp_path,monkeypatch,actual_uuid):
    (tmp_path/'.mini-eval-volume-id').write_text('expected\n')
    mount={'target':str(tmp_path),'source':'/dev/sdz1','uuid':actual_uuid,'fstype':'ext4','options':'rw'}
    monkeypatch.setattr('scripts.check_shared_mount.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps({'filesystems':[mount]})))
    if actual_uuid=='expected':check(tmp_path,'UUID=expected','ext4','expected')
    else:
        with pytest.raises(ValueError):check(tmp_path,'UUID=expected','ext4','expected')
