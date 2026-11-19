"""One classification for episode detail, batch monitoring and exported reports.

Execution lifecycle and protocol outcome are independent. Never infer a child's
exit code from its wrapper's failure, nor a process per native SDK episode.
"""
from collections import defaultdict
from sqlalchemy import select
from runner.episode_contract import result_error_code
from .core import EpisodeRecord

TERMINAL = {'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED'}
PROCESS_KEYS = ('normal', 'abnormal', 'running', 'unknown', 'not_started')
PROTOCOL_KEYS = ('planned', 'success', 'failure', 'invalid', 'errors', 'unreported', 'not_applicable_tasks')


def per_episode_process(template):
    return (template.get('integration_mode') == 'sdk-command' or
            (template.get('execution_mode') == 'host' and
             (template.get('host_runtime') or {}).get('episode_launch') == 'per_episode'))


def single_command(template):
    return template.get('task_type') == 'command' and not per_episode_process(template)


def process_state(code, started, ended, terminal):
    if type(code) is int:
        return 'normal' if code == 0 else 'abnormal'
    if ended or (started and terminal):return 'unknown'
    if started:return 'running'
    return 'not_started'


def episode_view(template, row, status):
    result = row.result if row else None
    code = result.get('exit_code') if result else None
    scope = per_episode_process(template)
    execution = process_state(code, bool(row and row.started), bool(result), status in TERMINAL) if scope else 'not_applicable'
    diagnostic = result_error_code(result.get('error')) if result else None
    if template.get('task_type') == 'command':protocol = 'not_applicable'
    elif result and result['status'] == 'COMPLETED':protocol = 'success' if result['success'] else 'failure'
    elif diagnostic:protocol = 'invalid'
    elif result and result['status'] == 'ERROR':protocol = 'error'
    else:protocol = 'unreported'
    return dict(execution_state=execution, protocol_state=protocol, diagnostic_code=diagnostic,
                business_exit_code=code if scope else None)


def summarize_attempt(job, run, rows):
    template = (job.client_spec or {}).get('template', {})
    per_process = per_episode_process(template)
    execution = dict.fromkeys(PROCESS_KEYS, 0)
    protocol = dict.fromkeys(PROTOCOL_KEYS, 0)
    command = template.get('task_type') == 'command'
    protocol['not_applicable_tasks'] = int(command)
    protocol['planned'] = 0 if command else job.episodes
    views = [episode_view(template, row, run.status) for row in rows]
    if per_process:
        for view in views:execution[view['execution_state']] += 1
        execution['not_started'] += max(0, job.episodes-len(rows))
    else:
        # Whole-task commands report the execution exit, including setup failures.
        # For protocol tasks a successful outer exit proves its synchronous business command
        # returned 0. A nonzero wrapper exit may instead be setup/teardown failure.
        code = run.exit_code if command else (0 if run.exit_code == 0 else None)
        started = bool(rows) or run.status in {'RUNNING', 'CANCEL_REQUESTED'} or run.exit_code is not None
        ended = run.status in TERMINAL and (started or run.status != 'CANCELLED')
        execution[process_state(code, started, ended, run.status in TERMINAL)] = 1
    if not command:
        for view in views:
            key = {'error':'errors'}.get(view['protocol_state'], view['protocol_state'])
            protocol[key] += 1
        protocol['unreported'] += max(0, job.episodes-len(rows))
    valid = protocol['success']+protocol['failure']
    protocol['valid'] = valid
    protocol['success_rate'] = protocol['success']/valid if valid else None
    return dict(execution=execution, protocol=protocol,
                process_scope='per_episode' if per_process else 'single_process',
                wrapper_exit_code=run.exit_code,
                detail_mode='execution' if single_command(template) else 'episodes')


def aggregate(values):
    values = list(values)
    execution = {key:sum(v['execution'][key] for v in values) for key in PROCESS_KEYS}
    protocol = {key:sum(v['protocol'][key] for v in values) for key in PROTOCOL_KEYS}
    valid = protocol['success']+protocol['failure']
    protocol.update(valid=valid, success_rate=protocol['success']/valid if valid else None)
    return dict(execution=execution, protocol=protocol)


def load_records(s, run_ids):
    groups = defaultdict(list)
    if run_ids:
        for row in s.scalars(select(EpisodeRecord).where(EpisodeRecord.run_id.in_(run_ids))):
            groups[row.run_id].append(row)
    return groups
