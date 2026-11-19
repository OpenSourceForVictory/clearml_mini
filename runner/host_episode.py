"""Host-only adapter for one business process per episode."""
import json
import math
import os
import subprocess
import sys
import time

from .image_runtime import TOKEN
from .process_command import for_template
from .sdk import EpisodeResult, EvaluationClient, run
from .task_input import build
from .episode_contract import EpisodeResultError


def episode_input(task, episode, context, environment):
    work=context.work_dir/('episode-'+str(episode.id))
    output=context.output_dir/('episode-'+str(episode.id))
    work.mkdir(parents=True,exist_ok=True,mode=0o700)
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=work/'input.json'
    child={k:v for k,v in task.items() if k not in ('_resolved_environment','_resolved_command','_resolved_cwd')}
    child.update(episodes=1,seed=episode.seed,_parent_episode_id=episode.id)
    env={**environment,'MINI_EVAL_INPUT':str(path),'MINI_EVAL_WORK_DIR':str(work),
         'MINI_EVAL_OUTPUT_DIR':str(output),'MINI_EVAL_EPISODE_ID':str(episode.id),
         'MINI_EVAL_EPISODE_SEED':str(episode.seed),'MINI_EVAL_RESULT':str(work/'result.json')}
    child=build(child,env)
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as stream:
        json.dump(child,stream,ensure_ascii=False,allow_nan=False)
    return child,env


class HostEpisodeClient(EvaluationClient):
    def __init__(self, command, cwd, environment, ordinary):
        self.command=command
        self.cwd=cwd
        self.environment=environment
        self.ordinary=ordinary

    def run_episode(self, task, episode, context):
        child,env=episode_input(task,episode,context,self.environment)
        values={k:str(child.get(k,'')) for k in ('task_name','episodes','seed','endpoint')}
        values.update(episode_id=str(episode.id),episode_seed=str(episode.seed),
                      input_path=env['MINI_EVAL_INPUT'],result_path=env['MINI_EVAL_RESULT'],
                      work_dir=env['MINI_EVAL_WORK_DIR'],output_dir=env['MINI_EVAL_OUTPUT_DIR'])
        command=[TOKEN.sub(lambda m:values.get(m[1],m[0]),arg) for arg in self.command]
        configured={**task['template']['host_runtime'].get('environment',{}),**task.get('environment',{})}
        for key in configured:
            env[key]=TOKEN.sub(lambda m:values.get(m[1],m[0]),env.get(key,configured[key]))
        from .parameter_contract import bind_values
        from .task_input import bind_platform
        schema=task['template'].get('parameter_schema')
        if schema is not None:
            command,additions=bind_values(schema,child.get('parameters',{}),command)
            if set(additions)&set(configured):raise ValueError('business environment conflicts with runtime configuration')
            env.update(additions)
        command,additions=bind_platform(task['template'].get('platform_bindings',[]),child,command)
        if set(additions)&set(configured):raise ValueError('platform environment conflicts with runtime configuration')
        env.update(additions)
        command=for_template(task['template'],command,self.cwd,env)
        final=[]
        final_count=0
        started=time.monotonic()
        if task.get('_process_logs'):
            def line_received(line):
                nonlocal final_count
                try:record=json.loads(line)
                except ValueError:record=None
                if isinstance(record,dict) and 'final' in record:
                    final_count+=1
                    if final_count==1:final.append(record['final'])
                elif not line.startswith('MINI_EVAL_EVENT '):
                    print(line,end='',flush=True)
            from .process_log_run import run as captured_run
            code=captured_run(command,config=task,scope='episode',episode=episode.id,cwd=self.cwd,env=env,on_line=line_received)
        else:
            with subprocess.Popen(command,cwd=self.cwd,env=env,stdout=subprocess.PIPE,
                                  text=True,errors='replace') as process:
                for line in process.stdout:
                    try:record=json.loads(line)
                    except ValueError:record=None
                    if isinstance(record,dict) and 'final' in record:
                        final_count+=1
                        if final_count==1:final.append(record['final'])
                    elif not line.startswith('MINI_EVAL_EVENT '):
                        print(line,end='',flush=True)
                code=process.wait()
        self.process_exit_code = code
        if code:
            raise RuntimeError(f'business episode {episode.id} exited with code {code}')
        if self.ordinary:
            return EpisodeResult(True,{'process_runtime':time.monotonic()-started})
        if not final_count:raise EpisodeResultError('missing_final')
        if final_count!=1:raise EpisodeResultError('multiple_final')
        if not isinstance(final[0],dict):raise EpisodeResultError('invalid_final')
        result=final[0]
        if (type(result.get('episode_count')) is not int or result['episode_count']!=1
                or type(result.get('success_rate')) not in (int,float)
                or not math.isfinite(result['success_rate']) or result['success_rate'] not in (0,1)
                or type(result.get('runtime')) not in (int,float)
                or not math.isfinite(result['runtime']) or result['runtime']<0):
            raise EpisodeResultError('invalid_final')
        return EpisodeResult(bool(result['success_rate']),{'process_runtime':time.monotonic()-started})


def main(config,command,cwd,environment):
    ordinary=config['template'].get('task_type')=='command'
    return run(HostEpisodeClient(command,cwd,environment,ordinary),config)
