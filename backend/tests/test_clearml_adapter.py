from types import SimpleNamespace
from backend.app.adapters.clearml import AdapterError, RealClearMLAdapter, RunnerConfig


class TaskStub:
    tasks = {}
    created = []
    enqueued = []

    def __init__(self, name, status='created'):
        self.name = name
        self.id = f'task-{name}'
        self.status = status
        self.tags = []
        self.calls = None

    @classmethod
    def get_tasks(cls, project_name=None, task_name=None, task_filter=None):
        return [t for t in cls.tasks.values() if not task_name or t.name == task_name]

    @classmethod
    def get_task(cls, task_id):
        return next(t for t in cls.tasks.values() if t.id == task_id)

    @classmethod
    def create(cls, **kwargs):
        task = cls(kwargs['task_name'])
        task.calls = kwargs
        cls.tasks[task.name] = task
        cls.created.append(task)
        return task

    @classmethod
    def enqueue(cls, task, queue_name):
        cls.enqueued.append((task.id, queue_name))
        task.status = 'queued'

    def add_tags(self, tags):
        self.tags.extend(tags)

    def reload(self):
        pass

    def get_status(self):
        return self.status

    def mark_stop_request(self):
        self.status = 'stopped'

    def get_reported_console_output(self, number_of_reports):
        return ['line']


def test_create_recover_and_enqueue_once():
    TaskStub.tasks, TaskStub.created, TaskStub.enqueued = {}, [], []
    adapter = RealClearMLAdapter(RunnerConfig('git@example/repo', 'a' * 40, 'runner:v1'), TaskStub)
    job = SimpleNamespace(id='job-1', benchmark='mock', model_ref='model', episodes=3)
    marker = 'eval:job-1:1'
    task_id = adapter.create_task(marker, job)
    assert adapter.create_task(marker, job) == task_id
    assert len(TaskStub.created) == 1
    assert TaskStub.created[0].calls['commit'] == 'a' * 40
    assert TaskStub.created[0].calls['docker'] == 'runner:v1'
    assert marker in TaskStub.created[0].tags
    adapter.enqueue_task(task_id, 'gpu-node-0')
    adapter.enqueue_task(task_id, 'gpu-node-0')
    assert TaskStub.enqueued == [(task_id, 'gpu-node-0')]
    assert adapter.get_console_output(task_id) == ['line']
    adapter.abort_task(task_id)
    assert adapter.get_task(task_id).get_status() == 'stopped'
