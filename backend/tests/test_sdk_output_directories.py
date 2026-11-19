"""SDK output paths remain task-relative and contexts never mutate across rounds."""
import os

import pytest

from runner.sdk import Context, EpisodeResult, EvaluationClient, run


@pytest.mark.parametrize('nested', [False, True])
def test_episode_directory_context_and_legacy_root(tmp_path, monkeypatch, nested):
    monkeypatch.delenv('MINI_EVAL_WORK_DIR', raising=False)
    monkeypatch.setenv('MINI_EVAL_OUTPUT_DIR', str(tmp_path))
    before = dict(os.environ)
    seen = []

    class Client(EvaluationClient):
        def setup(self, context):
            assert context.output_dir == tmp_path
            with pytest.raises(RuntimeError, match='run_episode'):
                _ = context.episode_output_dir

        def run_episode(self, task, episode, context):
            seen.append(context)
            folder = context.episode_output_dir
            assert folder == (tmp_path if nested else tmp_path / f'episode-{episode.id}')
            file = folder / 'report.json'
            file.write_text(str(episode.seed))
            return EpisodeResult(True, artifacts=[context.artifact_path(file)])

    config = {'episodes': 1 if nested else 3, 'seed': 100}
    if nested:
        config['_parent_episode_id'] = 7
    run(Client(), config)
    assert len({id(c) for c in seen}) == len(seen)
    assert all(c.output_dir == tmp_path for c in seen)
    assert seen[0].episode_output_dir == (tmp_path if nested else tmp_path/'episode-1')
    assert (seen[0].episode_output_dir/'report.json').read_text() == '100'
    assert dict(os.environ) == before


def test_legacy_artifacts_are_not_rebased(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('MINI_EVAL_WORK_DIR', raising=False)

    class Client(EvaluationClient):
        def run_episode(self, task, episode, context):
            (context.output_dir/'old.json').write_text('{}')
            return EpisodeResult(True, artifacts=['old.json'])

    run(Client(), {'episodes': 1, 'seed': 0}, tmp_path)
    assert '"artifacts":["old.json"]' in capsys.readouterr().out
    assert not (tmp_path/'episode-1').exists()  # Unused capability adds no files to old collectors.


@pytest.mark.parametrize('path', ['../outside', '/outside', '', '.'])
def test_artifact_reference_rejects_escape(tmp_path, path):
    with pytest.raises(ValueError):
        Context({}, tmp_path).artifact_path(path)


def test_artifact_reference_rejects_symlink_escape(tmp_path):
    (tmp_path/'link').symlink_to('/tmp', target_is_directory=True)
    with pytest.raises(ValueError):
        Context({}, tmp_path).artifact_path('link/outside.json')


def test_artifact_reference_relative_and_unicode(tmp_path):
    context = Context({}, tmp_path)
    assert context.artifact_path('episode-1/report.json') == 'episode-1/report.json'


def test_episode_directory_refuses_symlink(tmp_path):
    target = tmp_path/'actual'
    target.mkdir()
    link = tmp_path/'episode-1'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        _ = Context({}, tmp_path, link).episode_output_dir


def test_context_is_stable_after_business_exception(tmp_path, monkeypatch):
    monkeypatch.delenv('MINI_EVAL_WORK_DIR', raising=False)
    saved = []

    class Client(EvaluationClient):
        def run_episode(self, task, episode, context):
            saved.append(context)
            (context.episode_output_dir/'failure.txt').write_text('diagnostic')
            raise RuntimeError('business failed')

    with pytest.raises(RuntimeError, match='business failed'):
        run(Client(), {'episodes': 2, 'seed': 0}, tmp_path)
    assert (saved[0].episode_output_dir/'failure.txt').read_text() == 'diagnostic'


def test_episode_failure_continue_preserves_each_directory(tmp_path, monkeypatch):
    from runner.episode_contract import EpisodeExecutionError
    monkeypatch.delenv('MINI_EVAL_WORK_DIR', raising=False)
    seen = []

    class Client(EvaluationClient):
        def run_episode(self, task, episode, context):
            seen.append(context)
            (context.episode_output_dir/'report.txt').write_text(str(episode.id))
            if episode.id == 1:
                raise RuntimeError('first failed')
            return EpisodeResult(True)

    config = {'episodes': 3, 'seed': 5, 'template': {'execution_mode': 'host',
              'host_runtime': {'episode_launch': 'per_episode', 'episode_error_policy': 'continue'}}}
    with pytest.raises(EpisodeExecutionError, match='1 of 3'):
        run(Client(), config, tmp_path)
    assert [(c.episode_output_dir/'report.txt').read_text() for c in seen] == ['1', '2', '3']


def test_episode_context_preserves_input_paths_and_reporting(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('MINI_EVAL_WORK_DIR', raising=False)
    monkeypatch.setenv('MINI_EVAL_ASSETS_DIR', str(tmp_path/'assets'))
    monkeypatch.setenv('MINI_EVAL_CACHE_DIR', str(tmp_path/'cache'))
    context = Context({'episodes': 1, 'seed': 1, 'parameters': {'name': 'demo'}, 'endpoint': 'http://server:80'}, tmp_path/'output')
    assert context.business == {'name': 'demo'}
    assert context.service_endpoint == 'http://server:80'
    assert context.work_dir == tmp_path/'work'
    assert context.assets_dir == tmp_path/'assets'
    assert context.cache_dir == tmp_path/'cache'
    context.report('saving')
    assert 'saving' in capsys.readouterr().out
    assert Context({'input_version': 1, 'platform': {}, 'business': {}}, tmp_path).service_endpoint is None
    with pytest.raises(NotImplementedError):
        EvaluationClient().run_episode({}, None, context)


def test_sdk_entrypoint_receives_episode_context(tmp_path, monkeypatch):
    import json
    import sys
    import types
    from runner.sdk import main
    monkeypatch.delenv('MINI_EVAL_WORK_DIR', raising=False)
    monkeypatch.setenv('MINI_EVAL_OUTPUT_DIR', str(tmp_path))
    source = tmp_path/'input.json'
    source.write_text(json.dumps({'episodes': 1, 'seed': 0}))
    monkeypatch.setenv('MINI_EVAL_INPUT', str(source))

    class Client(EvaluationClient):
        def run_episode(self, task, episode, context):
            (context.episode_output_dir/'report.txt').write_text('ok')
            return EpisodeResult(True)

    monkeypatch.setitem(sys.modules, 'artifact_test_business', types.SimpleNamespace(Client=Client, Invalid=object))
    monkeypatch.setattr(sys, 'argv', ['sdk', 'artifact_test_business:Client'])
    main()
    assert (tmp_path/'episode-1'/'report.txt').read_text() == 'ok'
    monkeypatch.setattr(sys, 'argv', ['sdk', 'artifact_test_business:Invalid'])
    with pytest.raises(TypeError, match='EvaluationClient'):
        main()
    monkeypatch.setattr(sys, 'argv', ['sdk'])
    with pytest.raises(SystemExit, match='usage'):
        main()
