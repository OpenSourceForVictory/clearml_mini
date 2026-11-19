"""Adapt an argv-based business command to the per-episode evaluation protocol.

One command invocation performs one episode and writes MINI_EVAL_RESULT JSON.
The JSON object must contain boolean success, with optional metrics/artifacts.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from .episode_contract import EpisodeResultError, EpisodeExecutionError
from .sdk import EpisodeResult, EvaluationClient, run
from .image_runtime import TOKEN
from .template_contract import command as validate_command


class CommandClient(EvaluationClient):
    def run_episode(self, task, episode, context):
        env=dict(os.environ)
        if task['template'].get('execution_mode')=='host':
            env.update(task.get('_resolved_environment',{}))
        runtime = task['template'].get('runtime')
        cwd=task.get('_resolved_cwd') or (runtime or {}).get('working_directory') or context.work_dir
        per_episode=(task['template'].get('execution_mode')=='host'
                     and (task['template'].get('host_runtime') or {}).get('episode_launch')=='per_episode')
        independent=task.get('_artifact_layout')=='batch-v1'
        if independent and not per_episode:
            from .task_input import build
            path=context.work_dir/('episode-input-'+str(episode.id)+'.json')
            env.update(MINI_EVAL_OUTPUT_DIR=str(context.episode_output_dir),MINI_EVAL_INPUT=str(path))
            task=build(task,env)
            path.write_text(json.dumps(task,ensure_ascii=False,allow_nan=False))
        result_path = context.work_dir / ('episode-result-'+str(episode.id)+'.json')
        if per_episode:
            from .host_episode import episode_input
            task,env=episode_input(task,episode,context,env)
            result_path=Path(env['MINI_EVAL_RESULT'])
        result_path.unlink(missing_ok=True)
        values = {k:str(task[k]) for k in ('task_name','episodes','seed','endpoint')}
        values.update(episode_id=str(episode.id),episode_seed=str(episode.seed),result_path=str(result_path),
                      input_path=env['MINI_EVAL_INPUT'] if per_episode or independent else os.environ['MINI_EVAL_INPUT'],
                      work_dir=env['MINI_EVAL_WORK_DIR'] if per_episode else str(context.work_dir),
                      output_dir=env['MINI_EVAL_OUTPUT_DIR'] if per_episode or independent else str(context.output_dir))
        # Runtime resolver sets per-node asset paths; no shell evaluation is used.
        values.update(task.get('_resolved_assets', {}))
        if runtime:
            values.update({'assets:'+k:v for k,v in runtime['assets'].items()})
            values.update({'mounts:'+k:v['target'] for k,v in runtime.get('mounts',{}).items()})
        def expand(text):
            def replace(match):
                if match[1] not in values: raise ValueError('unknown command placeholder: '+match[1])
                return values[match[1]]
            return TOKEN.sub(replace,text)
        argv=[expand(x) for x in validate_command(task['template']['command'])]
        settings=task['template'].get('host_runtime') or runtime or {}
        for k,v in {**settings.get('environment',{}),**task.get('environment',{})}.items():env[k]=expand(v)
        schema = task['template'].get('parameter_schema')
        if schema is not None:
            from .parameter_contract import bind_values
            argv, additions = bind_values(schema, task.get('parameters', {}), argv)
            if set(additions) & (set(settings.get('environment', {})) | set(task.get('environment', {}))):
                raise ValueError('business environment conflicts with runtime configuration')
            env.update(additions)
        from .task_input import build, bind_platform
        argv, additions=bind_platform(task['template'].get('platform_bindings',[]),build(task,env),argv)
        if set(additions)&(set(settings.get('environment',{}))|set(task.get('environment',{}))):
            raise ValueError('platform environment conflicts with runtime configuration')
        env.update(additions)
        env.update(MINI_EVAL_EPISODE_ID=str(episode.id),MINI_EVAL_EPISODE_SEED=str(episode.seed),MINI_EVAL_RESULT=str(result_path))
        from .process_command import for_template
        argv=for_template(task['template'],argv,cwd,env)
        if task.get('_process_logs'):
            from .process_log_run import run as captured_run
            process=subprocess.CompletedProcess(argv,captured_run(argv,config=task,scope='episode',episode=episode.id,cwd=cwd,env=env))
        else:
            process = subprocess.run(argv,env=env,cwd=cwd,check=False)
        self.process_exit_code = process.returncode
        process.check_returncode()
        if not result_path.exists() and not result_path.is_symlink():
            raise EpisodeResultError('missing_result_file')
        if result_path.is_symlink() or not result_path.is_file() or result_path.stat().st_size>16384:
            raise EpisodeResultError('invalid_result_file')
        try:
            with result_path.open() as f: result=json.load(f)
        except (OSError, ValueError):raise EpisodeResultError('invalid_result_file') from None
        if not isinstance(result,dict) or type(result.get('success')) is not bool or set(result)-{'success','metrics','artifacts'}:
            raise EpisodeResultError('invalid_result_file')
        artifacts=result.get('artifacts',[])
        if independent:
            from .cs_contract import artifact_name
            if not isinstance(artifacts,list) or any(not isinstance(p,str) or not artifact_name(p) for p in artifacts):
                raise EpisodeResultError('invalid_result_file')
            artifacts=[context.artifact_path(Path(env['MINI_EVAL_OUTPUT_DIR'])/p) for p in artifacts]
        return EpisodeResult(result['success'],result.get('metrics',{}),artifacts)


def main():
    config=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
    metrics=run(CommandClient(),config)
    output=Path(os.environ['MINI_EVAL_OUTPUT_DIR'])
    (output/'report.json').write_text(json.dumps({'task_name':config['task_name'],'metrics':metrics}))


if __name__=='__main__':
    try:main()
    except EpisodeExecutionError as exc:
        print(str(exc),file=sys.stderr,flush=True)
        raise SystemExit(2)
