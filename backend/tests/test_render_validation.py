"""SAPIEN result validation with a fake renderer; not real GPU rendering evidence."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from runner import robotwin_render


@pytest.mark.parametrize('mode',['valid','shape','nonfinite','uniform','physics'])
def test_renderer_validates_pixels_and_physics(monkeypatch,capsys,mode):
    pixels=MagicMock();pixels.shape=(1,1,4) if mode=='shape' else (64,64,4)
    pixels.__getitem__.return_value.std.return_value=0 if mode=='uniform' else .2
    numpy=SimpleNamespace(isfinite=lambda data:SimpleNamespace(all=lambda:mode!='nonfinite'))
    scene=MagicMock();actor=scene.create_actor_builder.return_value.build.return_value
    actor.get_pose.return_value=SimpleNamespace(p=[0,0,1 if mode=='physics' else .1])
    scene.add_camera.return_value.get_picture.return_value=pixels
    engine=MagicMock();engine.create_scene.return_value=scene
    sapien=SimpleNamespace(Engine=lambda:engine,SapienRenderer=MagicMock(),SceneConfig=MagicMock(),Pose=MagicMock(),
        physx=SimpleNamespace(PhysxRigidDynamicComponent=object))
    monkeypatch.setitem(sys.modules,'numpy',numpy)
    monkeypatch.setitem(sys.modules,'sapien',SimpleNamespace(core=sapien))
    monkeypatch.setitem(sys.modules,'sapien.core',sapien)
    if mode=='valid':
        result=robotwin_render.evaluate(2)
        assert result['episode_count']==2 and result['success_rate']==1
        assert scene.step.call_count==240
        assert len(capsys.readouterr().out.splitlines())==2
    else:
        with pytest.raises(RuntimeError,match={'shape':'invalid image','nonfinite':'invalid image','uniform':'uniform image','physics':'ground contact'}[mode]):
            robotwin_render.evaluate(1)
        assert not capsys.readouterr().out


def test_render_cli_labels_environment_check(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['render','--job-id','test','--model-ref','sapien-render','--benchmark','robotwin-render','--episodes','2'])
    def evaluate(episodes):assert episodes==2;return {'episode_count':2,'success_rate':1,'runtime':.1}
    monkeypatch.setattr(robotwin_render,'evaluate',evaluate)
    robotwin_render.main();records=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]['kind']=='environment-check' and records[1]['final']['episode_count']==2
