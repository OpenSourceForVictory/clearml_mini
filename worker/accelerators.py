"""Explicit Ascend adapter. Node approval never comes from task parameters."""
import os
import subprocess
from pathlib import Path

COMMON=('/dev/davinci_manager','/dev/devmm_svm','/dev/hisi_hdc')
DRIVER='/usr/local/Ascend/driver'


def ascend_sample(config):
    from node_agent.ascend import mappings,metrics
    def command(args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=5).stdout
    values=metrics(command(['npu-smi','info']),mappings(command(['npu-smi','info','-m'])))
    prefix='npu_'+str(int(config.get('gpu_index',0)))+'_'
    if values.get(prefix+'health')!='OK':raise RuntimeError('assigned NPU is not healthy')
    return {k[len(prefix):]:v for k,v in values.items() if k.startswith(prefix)}


def approved(config):
    profile=config.get('npu_runtime')
    if not isinstance(profile,dict) or profile.get('approved') is not True:raise RuntimeError('NPU runtime requires node administrator approval')
    index=int(config.get('gpu_index',0));device=f'/dev/davinci{index}'
    for path in (device,*COMMON):
        if not Path(path).is_char_device():raise RuntimeError('NPU device missing: '+path)
    if not Path(DRIVER).is_dir():raise RuntimeError('Ascend driver missing')
    return profile,device


def capabilities(config):
    if config.get('accelerator_type','gpu')!='npu':
        profile=config.get('gpu_runtime')
        if not isinstance(profile,dict):return []  # Legacy GPU nodes retain their existing pool policy.
        modes=config.get('execution_modes',['docker','host'])
        result=[f'gpu:{mode}:exclusive' for mode in ('docker','host') if mode in modes]
        if profile.get('allow_shared') is True:
            result.extend(f'gpu:{mode}:shared' for mode in ('docker','host') if mode in modes)
        return result
    try:
        profile,_=approved(config)
        result=['npu:docker:exclusive'] if 'docker' in config.get('execution_modes',['docker','host']) else []
        if result and profile.get('allow_shared') is True:result.append('npu:docker:shared')
        if 'host' in config.get('execution_modes',['docker','host']):
            try:
                from .npu_host import settings
                settings(config)
                result.append('npu:host:exclusive')
                if profile.get('allow_shared') is True:result.append('npu:host:shared')
            except (ValueError,RuntimeError,OSError,KeyError):pass
        # Actual host readiness also requires the background isolation probe.
        return result
    except (ValueError,RuntimeError,OSError):return []


def assignment(config,task):
    if task.get('accelerator_type','gpu')!='npu':return []
    profile,device=approved(config)
    if config.get('accelerator_type')!='npu' or task.get('device_index')!=int(config.get('gpu_index',0)):raise RuntimeError('assigned NPU differs from worker device')
    if task.get('gpu_mode')=='shared' and profile.get('allow_shared') is not True:raise RuntimeError('NPU shared execution is not approved')
    sample=ascend_sample(config)
    if sample['identity']!=task['gpu_uuid']:raise RuntimeError('NPU stable identity changed')
    return profile,device


def docker_args(config,task):
    if task.get('accelerator_type','gpu')!='npu':return []
    profile,device=assignment(config,task)
    args=[]
    for path in (device,*COMMON):args+=['--device',path+':'+path+':rw']
    args+=['--mount',f'type=bind,source={DRIVER},target={DRIVER},readonly']
    # CANN enumerates the single container-visible device as logical zero.
    args+=['--env','ASCEND_RT_VISIBLE_DEVICES=0']
    return args
