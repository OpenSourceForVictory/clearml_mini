"""Start a generic service command; the Worker owns probes and desired state."""
import json,os,subprocess
from pathlib import Path
from .cs_contract import INPUT_PATH,OUTPUT_DIR


def main():
    inp=os.environ.get('MINI_EVAL_INPUT',INPUT_PATH);config=json.loads(Path(inp).read_text())
    env={**os.environ,'MINI_EVAL_INPUT':inp,'MINI_EVAL_OUTPUT_DIR':os.environ.get('MINI_EVAL_OUTPUT_DIR',OUTPUT_DIR),
         'MINI_EVAL_PORT':str(config['port']),'MINI_EVAL_BIND_HOST':config['bind_host']}
    command=config['template']['command'];cwd=None
    if config['template'].get('execution_mode')=='host':
        command=config['_resolved_command'];env.update(config['_resolved_environment']);cwd=env['MINI_EVAL_WORK_DIR']
    else:
        from .image_runtime import resolve
        command,additions,cwd=resolve(config);env.update(additions)
    from .task_input import build, bind_platform
    config=build(config,env)
    Path(inp).write_text(json.dumps(config,ensure_ascii=False,allow_nan=False))
    schema=config['template'].get('parameter_schema')
    if schema is not None:
        from .parameter_contract import bind_values
        settings=config['template'].get('host_runtime') or config['template'].get('runtime') or {}
        command,additions=bind_values(schema,config.get('parameters',{}),command)
        if set(additions)&(set(settings.get('environment',{}))|set(config.get('environment',{}))):
            raise ValueError('business environment conflicts with runtime configuration')
        env.update(additions)
    command,additions=bind_platform(config['template'].get('platform_bindings',[]),config,command)
    env.update(additions)
    print(json.dumps({'stage':'starting-service','port':config['port']}),flush=True)
    raise SystemExit(subprocess.call(command,env=env,cwd=cwd))
if __name__=='__main__':main()
