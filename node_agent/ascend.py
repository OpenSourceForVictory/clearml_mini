"""Ascend 910B npu-smi adapter. Read-only, batch queries, explicit format checks."""
import re
import time


def mappings(output):
    result = {}
    for line in output.splitlines():
        match = re.fullmatch(r'\s*(\d+)\s+(\d+)\s+(\d+)\s+(Ascend\s+\S+)\s*', line)
        if match:
            card, chip, logical, model = match.groups()
            key = (int(card), int(chip))
            if key in result: raise ValueError('duplicate Ascend device')
            result[key] = (int(logical), model)
    if not result or len(result) > 64 or len({v[0] for v in result.values()}) != len(result):
        raise ValueError('unsupported Ascend mapping')
    return result


def metrics(output, mapping):
    if 'HBM-Usage(MB)' not in output or 'AICore(%)' not in output:
        raise ValueError('unsupported Ascend memory layout')
    values, seen, parent = {}, set(), None
    for line in output.splitlines():
        if 'Process id' in line: break  # process tables are not device metrics
        cells = [v.strip() for v in line.split('|')[1:-1]]
        if len(cells) != 3: continue
        header = re.fullmatch(r'(\d+)\s+(\S+)', cells[0])
        if header:
            parent = (int(header[1]), cells[1])
            continue
        if parent is None or not cells[0].isdigit(): continue
        key = (parent[0], int(cells[0]))
        if key not in mapping: raise ValueError('Ascend mapping changed')
        if key in seen: raise ValueError('duplicate Ascend metrics')
        if not re.fullmatch(r'[\da-fA-F]{4}:[\da-fA-F]{2}:[\da-fA-F]{2}\.\d', cells[1]):
            raise ValueError('invalid Ascend PCI identity')
        match = re.fullmatch(r'(\d+)\s+(\d+)\s*/\s*(\d+)\s+(\d+)\s*/\s*(\d+)', cells[2])
        if not match: raise ValueError('unsupported Ascend metric columns')
        utilization, _, _, used, total = map(int, match.groups())
        if not 0 <= used <= total or total <= 0 or not 0 <= utilization <= 100:
            raise ValueError('invalid Ascend capacity')
        logical, model = mapping[key];prefix = 'npu_'+str(logical)+'_'
        fields = {'identity':'ascend:'+cells[1].lower()+':'+str(key[1]),
                  'model':model, 'card':key[0], 'chip':key[1], 'logical':logical,
                  'total_mb':total, 'free_mb':total-used,
                  'utilization':utilization, 'health':parent[1]}
        values.update({prefix+k:v for k,v in fields.items()});seen.add(key);parent=None
    if seen != set(mapping): raise ValueError('incomplete Ascend metrics')
    return values


class AscendProbe:
    def __init__(self, command):
        self.command = command
        self.mapping = None
        self.discovered = 0

    def __call__(self):
        if self.mapping is None or time.monotonic()-self.discovered >= 60:
            self.mapping = mappings(self.command(['npu-smi','info','-m']))
            self.discovered = time.monotonic()
        try: return metrics(self.command(['npu-smi','info']), self.mapping)
        except Exception:
            self.mapping = None
            raise
