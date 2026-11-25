"""Reuse protocol readers, committing to a durable outbox before cursor advance."""
import copy
from pathlib import Path

from .storage import atomic_json


def execution_outbox(worker, *, existing=False):
    from .reliable_outbox import ReliableOutbox
    directory = Path(str(worker.path)+'.outbox')
    if existing and not (directory/'reliable.sqlite3').is_file():
        raise RuntimeError('original result outbox is missing; retaining recovery journal')
    return ReliableOutbox(directory)


def collect(worker, outbox=None, *, output='', protocol_root=None, final=False):
    state = worker.state
    if not state or not state.get('recovery_assignment'):
        raise ValueError('durable recovery assignment required')
    if outbox is None:
        outbox = execution_outbox(worker)
    task = state['task']
    if final:
        from .recovery_runtime import RecoveryRuntime
        observed = RecoveryRuntime(worker, task['execution_id']).inspect()
        if observed['state'] != 'exited' or observed['identity'] != state.get('recovery', {}).get('identity'):
            raise RuntimeError('final result collection requires original runtime exit')
    if task.get('benchmark') != 'cs-client':
        if final:
            candidate = {**state, 'recovery_results_collected':True}
            atomic_json(worker.path, candidate)
            worker.state = candidate
        return True
    execution = task['execution_id']
    receipt = state['recovery_assignment']['claim']['receipt']
    if receipt['execution_id'] != execution:
        raise ValueError('result assignment does not match execution')
    template = task['client_spec']['template']
    if template.get('integration_mode') == 'sdk-task-result':
        source, cursor, kind = 'task_result', None, 'task_result'
    elif task.get('log_protocol') == 2:
        if protocol_root is None:
            raise ValueError('protocol journal root required')
        source, cursor, kind = 'protocol_events', 'protocol_event_offset', 'episode'
    elif template.get('episode_reporting'):
        source, cursor, kind = 'episode_journal', 'episode_file_offset', 'episode'
    else:
        source, cursor, kind = 'stdout_events', 'episode_offset', 'episode'
    if state.get('recovery_result_source') not in (None, source):
        raise ValueError('durable result source cannot change')
    if state.get('recovery_result_source') is None:
        candidate = {**state, 'recovery_result_source':source}
        atomic_json(worker.path, candidate)
        worker.state = state = candidate

    # Readers mutate their cursor before save(). Keep those mutations private
    # until the atomic save succeeds, including after a disk-full exception.
    reader = copy.copy(worker)
    reader.state = copy.deepcopy(state)

    def save():
        candidate = copy.deepcopy(reader.state)
        atomic_json(worker.path, candidate)
        worker.state = candidate

    class LocalResultAPI:
        def post(self, path, body):
            suffix = 'task-result' if kind == 'task_result' else 'episodes'
            if path != f'worker/executions/{execution}/{suffix}':
                raise ValueError('result collector cannot send unrelated requests')
            sequence = reader.state.get(cursor, 0) if cursor else 0
            outbox.put(execution, receipt['generation'], kind, sequence, body)
            return {'queued':True}

    reader.api = LocalResultAPI()
    reader.save = save
    if source == 'task_result':
        done = reader.send_task_result(execution)
    elif source == 'protocol_events':
        done = reader.send_protocol_events(execution, protocol_root/'events.jsonl')
    elif source == 'episode_journal':
        done = reader.send_episode_journal(execution)
    else:
        done = reader.send_episodes(execution, output)
    if final and done:
        # Some legacy readers return True for a trailing incomplete line.
        # Never retire a recovery journal with such unread bytes remaining.
        if source == 'stdout_events':
            from runner.sdk import EVENT_PREFIX
            tail = output[reader.state.get(cursor, 0):]
            # After verified exit, ordinary diagnostic text needs no newline.
            # Retain complete or partial event prefixes for result recovery.
            if (tail and len(tail.splitlines(keepends=True)) == 1
                    and not tail.startswith(EVENT_PREFIX) and not EVENT_PREFIX.startswith(tail)):
                reader.state[cursor] = len(output)
            done = reader.state.get(cursor, 0) == len(output)
        elif source in ('episode_journal', 'protocol_events'):
            from .workspace import execution_dirs
            path = (protocol_root/'events.jsonl' if source == 'protocol_events' else
                    execution_dirs(worker.workspace, execution)/'work'/'episodes.jsonl')
            done = not path.exists() or reader.state.get(cursor, 0) == path.stat().st_size
        if done:
            reader.state['recovery_results_collected'] = True
            save()
    return done
