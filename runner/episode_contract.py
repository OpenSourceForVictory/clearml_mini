"""Shared validation with no external dependencies, used by SDK and API."""
import json
import math
from .cs_contract import artifact_name


def validate_event(event):
    if not isinstance(event, dict):
        raise ValueError('episode event must be an object')
    allowed = {'episode_id', 'seed', 'status', 'success', 'metrics', 'artifacts', 'runtime', 'error', 'exit_code'}
    if set(event) - allowed:
        raise ValueError('unknown episode event fields')
    for key, lower, upper in [('episode_id', 1, 10000), ('seed', 0, 2147483647)]:
        if type(event.get(key)) is not int or not lower <= event[key] <= upper:
            raise ValueError('invalid '+key)
    status = event.get('status')
    required = {'episode_id', 'seed', 'status'}
    if status == 'COMPLETED':
        required |= {'success', 'metrics', 'runtime', 'artifacts'}
        if type(event.get('success')) is not bool:
            raise ValueError('success must be boolean')
        metrics = event.get('metrics')
        if not isinstance(metrics, dict) or len(metrics) > 64:
            raise ValueError('metrics must be a bounded object')
        for k, v in metrics.items():
            if not isinstance(k, str) or not 1 <= len(k) <= 100 or type(v) not in (int, float) or not math.isfinite(v):
                raise ValueError('metrics must contain finite numeric values')
        runtime = event.get('runtime')
        if type(runtime) not in (int, float) or not math.isfinite(runtime) or runtime < 0:
            raise ValueError('invalid runtime')
        artifacts = event.get('artifacts')
        if not isinstance(artifacts, list) or len(artifacts) > 8 or any(not isinstance(x, str) or not artifact_name(x) for x in artifacts):
            raise ValueError('invalid artifact references')
    elif status == 'ERROR':
        required.add('error')
        if not isinstance(event.get('error'), str) or not 1 <= len(event['error']) <= 1000:
            raise ValueError('invalid error')
    elif status != 'STARTED':
        raise ValueError('invalid episode status')
    if 'exit_code' in event:
        if status == 'STARTED' or type(event['exit_code']) is not int or not -255 <= event['exit_code'] <= 255:
            raise ValueError('invalid episode exit_code')
        required.add('exit_code')
    if set(event) != required or len(json.dumps(event, allow_nan=False).encode()) > 16384:
        raise ValueError('invalid episode payload')
    return event


RESULT_ERRORS = {
    'missing_final': '未收到协议结果；请向 stdout 单独输出一行 final JSON，不要添加日志前缀。',
    'multiple_final': '收到多条 final 结果；每个业务回合只能输出一条。',
    'invalid_final': 'final 结果格式无效；需要 episode_count=1、success_rate 为 0 或 1、runtime 为非负有限数。',
    'missing_result_file': '缺少 SDK 命令结果文件；请写入 MINI_EVAL_RESULT 指定路径。',
    'invalid_result_file': 'SDK 命令结果文件无效；需要不超过 16 KiB 的普通 JSON 文件及布尔 success 字段。',
    'invalid_episode_result': '回合结果字段无效；请检查 success、metrics 和 artifacts 的协议格式。',
}


class EpisodeResultError(ValueError):
    """Only fixed, non-payload diagnostics may enter durable episode events."""
    def __init__(self, code):
        self.code = code
        super().__init__('episode_result_'+code+': '+RESULT_ERRORS[code])


class EpisodeExecutionError(RuntimeError):
    """An already-reported episode failure, safe for a concise entrypoint error."""


def result_error_code(error):
    for code in RESULT_ERRORS:
        if error == str(EpisodeResultError(code)):
            return code
    return None
