"""Optional process-boundary timing; wall clock is diagnostic, monotonic is authoritative."""
from datetime import datetime, timezone
import math
import time


def stamp():
    return datetime.now(timezone.utc).isoformat()


def independent(template):
    return template.get('integration_mode') == 'sdk-command' or (
        template.get('execution_mode') == 'host'
        and (template.get('host_runtime') or {}).get('episode_launch') == 'per_episode')


class Timer:
    def __init__(self):
        self.started_at = stamp()
        self.begin = time.monotonic()

    def event(self, final=False):
        value = {'schema': 1, 'started_at': self.started_at}
        if final:
            end = stamp()
            seconds = max(0, time.monotonic() - self.begin)
            wall = (datetime.fromisoformat(end) - datetime.fromisoformat(self.started_at)).total_seconds()
            value.update(finished_at=end, duration_seconds=seconds, clock_adjusted=abs(wall-seconds)>1)
        return value


def validate(value, status):
    keys = {'schema', 'started_at'}
    if status != 'STARTED':keys |= {'finished_at', 'duration_seconds', 'clock_adjusted'}
    if not isinstance(value, dict) or set(value) != keys or type(value['schema']) is not int or value['schema'] != 1:
        raise ValueError('invalid episode timing schema')
    for key in ('started_at', 'finished_at'):
        if key not in value:continue
        raw=value[key]
        if not isinstance(raw,str) or len(raw)>40:
            raise ValueError('invalid episode timing timestamp')
        try:dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
        except ValueError:raise ValueError('invalid episode timing timestamp') from None
        if dt.tzinfo is None or dt.utcoffset().total_seconds()!=0:
            raise ValueError('episode timing requires UTC timestamps')
    if status != 'STARTED':
        seconds=value['duration_seconds']
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<=seconds<=315576000:
            raise ValueError('invalid episode duration')
        if type(value['clock_adjusted']) is not bool:
            raise ValueError('invalid episode clock flag')
