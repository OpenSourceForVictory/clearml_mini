"""Vendor-neutral inventory contract. Missing metrics never mean zero usage.

Ascend telemetry does not authorize execution until the runtime adapter and
node enrollment path have also been validated.
"""
import re


def inventory(samples):
    devices = []
    gpu = samples.get('gpu', {})
    if gpu.get('status') == 'ok':
        values = gpu['values']
        for key, identity in values.items():
            match = re.fullmatch(r'gpu_(\d+)_uuid', key)
            if not match: continue
            index = int(match[1]);prefix = 'gpu_'+str(index)+'_'
            devices.append({'kind':'gpu','vendor':'nvidia','device_id':str(identity),
                'index':index,'sampled_at':gpu['sampled_at'], 'status':'ok',
                'memory_total_mb':values[prefix+'total_mb'],
                'memory_free_mb':values[prefix+'free_mb'],
                'utilization_percent':values[prefix+'utilization'],
                'execution_ready':True})
    npu = samples.get('npu', {})
    if npu.get('status') == 'ok':
        values = npu['values']
        for key, identity in values.items():
            match = re.fullmatch(r'npu_(\d+)_identity', key)
            if not match: continue
            prefix = 'npu_'+match[1]+'_'
            devices.append({'kind':'npu','vendor':'huawei','device_id':identity,
                'index':values[prefix+'logical'],'model':values[prefix+'model'],
                'physical_card_id':values[prefix+'card'],'chip_id':values[prefix+'chip'],
                'sampled_at':npu['sampled_at'],
                'status':'ok' if values[prefix+'health']=='OK' else 'failed',
                'health':values[prefix+'health'],
                'memory_total_mb':values[prefix+'total_mb'],
                'memory_free_mb':values[prefix+'free_mb'],
                'utilization_percent':values[prefix+'utilization'],
                'execution_ready':False})
    return devices[:128]
