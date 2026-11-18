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


class HostEpisodeClient(EvaluationClient):
    def __init__(self, command, cwd, environment, ordinary):
        self.command=command
        self.cwd=cwd
        self.environment=environment
        self.ordinary=ordinary

    def run_episode(self, task, episode, context):
        work=context.work_dir/('episode-'+str(episode.id))
        output=context.output_dir/('episode-'+str(episode.id))
        work.mkdir(parents=True,exist_ok=True,mode=0o700)
        output.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=work/'input.json'
        child={k:v for k,v in task.items() if k not in ('_resolved_environment','_resolved_command','_resolved_cwd')}
        child.update(episodes=1,seed=episode.seed,_parent_episode_id=episode.id)
        child=build(child,{**self.environment,'MINI_EVAL_WORK_DIR':str(work),'MINI_EVAL_OUTPUT_DIR':str(output)})
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as stream:
            json.dump(child,stream,ensure_ascii=False,allow_nan=False)
        result_path=work/'result.json'
        values={'episode_id':str(episode.id),'episode_seed':str(episode.seed),
                'input_path':str(path),'result_path':str(result_path),
                'work_dir':str(work),'output_dir':str(output)}
        command=[TOKEN.sub(lambda m:values.get(m[1],m[0]),arg) for arg in self.command]
        env={**self.environment,'MINI_EVAL_INPUT':str(path),'MINI_EVAL_WORK_DIR':str(work),
             'MINI_EVAL_OUTPUT_DIR':str(output),'MINI_EVAL_EPISODE_ID':str(episode.id),
             'MINI_EVAL_EPISODE_SEED':str(episode.seed),'MINI_EVAL_RESULT':str(result_path)}
        command=for_template(task['template'],command,self.cwd,env)
        final=[]
        started=time.monotonic()
        with subprocess.Popen(command,cwd=self.cwd,env=env,stdout=subprocess.PIPE,
                              text=True,errors='replace') as process:
            for line in process.stdout:
                try:record=json.loads(line)
                except ValueError:record=None
                if isinstance(record,dict) and 'final' in record:
                    final.append(record['final'])
                elif not line.startswith('MINI_EVAL_EVENT '):
                    print(line,end='',flush=True)
            code=process.wait()
        self.process_exit_code = code
        if code:
            raise RuntimeError(f'business episode {episode.id} exited with code {code}')
        if self.ordinary:
            return EpisodeResult(True,{'process_runtime':time.monotonic()-started})
        if len(final)!=1 or not isinstance(final[0],dict):
            raise ValueError('direct protocol episode must emit exactly one final result')
        result=final[0]
        if (type(result.get('episode_count')) is not int or result['episode_count']!=1
                or type(result.get('success_rate')) not in (int,float)
                or not math.isfinite(result['success_rate']) or result['success_rate'] not in (0,1)
                or type(result.get('runtime')) not in (int,float)
                or not math.isfinite(result['runtime']) or result['runtime']<0):
            raise ValueError('direct protocol episode final result is invalid')
        return EpisodeResult(bool(result['success_rate']),{'process_runtime':time.monotonic()-started})


def main(config,command,cwd,environment):
    ordinary=config['template'].get('task_type')=='command'
    return run(HostEpisodeClient(command,cwd,environment,ordinary),config)
