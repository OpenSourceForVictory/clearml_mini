"""Node resource snapshots and admission, independent of execution lifecycle."""
import math
from datetime import datetime, timezone
from sqlalchemy import select
from .core import Slot, now
from .freshness import RESOURCE_SAMPLE_SECONDS


def record(w):
    return (w.connection or {}).get('agent', {})


def fresh(r):
    try:
        seen = datetime.fromisoformat(r['seen_at'])
        if seen.tzinfo is None: seen = seen.replace(tzinfo=timezone.utc)
        return 0 <= (now()-seen).total_seconds() < 45
    except (KeyError, ValueError, TypeError): return False


def resource_source(w):
    return record(w).get('resource_protocol') == 1


def resource_values(w, kind, at=None):
    """Only the original sample time establishes freshness; heartbeats cannot renew it."""
    at = at or now()
    r = record(w)
    sample = r.get('samples', {}).get(kind, {})
    try:
        age = at.timestamp() - sample['sampled_at']
        if (not fresh(r) or sample['status'] != 'ok' or not 0 <= age < RESOURCE_SAMPLE_SECONDS): return None
        return sample['values']
    except (KeyError, TypeError, ValueError): return None


def number(values, key, maximum):
    value = float(values[key])
    if not math.isfinite(value) or not 0 <= value <= maximum: raise ValueError('invalid resource')
    return value


def apply_resources(s, w):
    if not resource_source(w): return
    for slot in s.scalars(select(Slot).where(Slot.worker_id == w.id)):
        slot.telemetry_at = slot.free_vram_mb = slot.gpu_utilization = None
        kind=getattr(slot,'accelerator_type',None) or 'gpu'
        values=resource_values(w,kind)
        if values is None: continue
        prefix = kind+'_' + str(slot.gpu_index) + '_'
        try:
            if values[prefix+('identity' if kind=='npu' else 'uuid')] != slot.gpu_uuid: continue
            total = number(values, prefix+'total_mb', 10240000)
            if total != slot.vram_mb: continue
            free = number(values, prefix+'free_mb', total)
            utilization = number(values, prefix+'utilization', 100)
            sampled = record(w)['samples'][kind]['sampled_at']
            if kind=='npu' and values.get(prefix+'health')!='OK':continue
            slot.telemetry_at = datetime.fromtimestamp(sampled, timezone.utc)
            slot.free_vram_mb, slot.gpu_utilization = int(free), int(utilization)
        except (KeyError, TypeError, ValueError, OverflowError): continue


def host_codes(w, pool, cpu, ram, at=None, pending_ram=0):
    if not resource_source(w): return []
    values = resource_values(w, 'host', at)
    if values is None: return ['node_telemetry_stale']
    try:
        cores = number(values, 'cpu_count', 10000)
        total = number(values, 'memory_total_mb', 102400000)
        available = number(values, 'memory_available_mb', total)
    except (KeyError, TypeError, ValueError): return ['node_telemetry_stale']
    codes = []
    if cpu + pool.cpu_per_job > cores: codes.append('node_cpu')
    # The sample includes external processes. Without per-task usage attribution,
    # charge all platform commitments against it, including running tasks.
    if ((ram + pool.ram_gb_per_job) * 1024 > total
            or (ram + pool.ram_gb_per_job) * 1024 > available):
        codes.append('node_ram')
    return codes
