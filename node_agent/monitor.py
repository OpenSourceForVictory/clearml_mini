"""Bounded probes on daemon threads; a stuck probe cannot block heartbeats."""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path


def command(argv):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=3, check=True)
    if len(r.stdout) > 32768: raise ValueError('probe output too large')
    return r.stdout


def host():
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {'cpu_count': os.cpu_count() or 1, 'load_1m': os.getloadavg()[0],
            'memory_total_mb': int(mem['MemTotal'].split()[0])//1024,
            'memory_available_mb': int(mem['MemAvailable'].split()[0])//1024}


def gpu():
    rows = command(['nvidia-smi', '--query-gpu=index,memory.free,utilization.gpu', '--format=csv,noheader,nounits'])
    values = {}
    for row in rows.splitlines()[:64]:
        i, free, util = row.split(',')
        values['gpu_'+str(int(i))+'_free_mb'] = int(free)
        values['gpu_'+str(int(i))+'_utilization'] = int(util)
    return values


class Monitor:
    def __init__(self, root, probes=None):
        self.probes = probes or {'host': host, 'gpu': gpu,
            'disk': lambda: {'free_mb': shutil.disk_usage(root).free//1048576},
            'docker': lambda: {'version': command(['docker', 'version', '--format', '{{.Server.Version}}']).strip()}}
        self.samples = {}; self.threads = {}; self.started = {}; self.lock = threading.Lock()

    def sample(self, interval):
        current = time.monotonic()
        with self.lock:
            for name, probe in self.probes.items():
                thread = self.threads.get(name)
                if thread and thread.is_alive():
                    if current-self.started[name] > 5:
                        self.samples[name] = {'status': 'failed', 'sampled_at': time.time(), 'values': {}}
                    continue
                if name in self.started and current-self.started[name] < interval: continue
                self.started[name] = current
                def collect(name=name, probe=probe):
                    try: result = {'status': 'ok', 'sampled_at': time.time(), 'values': probe()}
                    except Exception: result = {'status': 'failed', 'sampled_at': time.time(), 'values': {}}
                    with self.lock: self.samples[name] = result
                self.threads[name] = threading.Thread(target=collect, daemon=True)
                self.threads[name].start()
            return {k: dict(v) for k, v in self.samples.items()}
