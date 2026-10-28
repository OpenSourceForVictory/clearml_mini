"""ClearML task operations. The adapter never accepts user-controlled code or images."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional
from clearml import Task


class AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerConfig:
    repo_url: str
    commit: str
    image: str
    project_name: str = 'Mini Eval'

    @classmethod
    def from_env(cls):
        values = [os.getenv('RUNNER_REPO_URL'), os.getenv('RUNNER_COMMIT'), os.getenv('RUNNER_IMAGE')]
        if not all(values):
            raise AdapterError('RUNNER_REPO_URL, RUNNER_COMMIT and RUNNER_IMAGE are required')
        return cls(*values)


class RealClearMLAdapter:
    def __init__(self, config: RunnerConfig, task_class=Task):
        self.config = config
        self.task_class = task_class

    def find_by_marker(self, marker: str) -> Optional[str]:
        # Task.create can commit before add_tags; exact task name is a second recovery key.
        tasks = self.task_class.get_tasks(project_name=self.config.project_name, task_name=marker)
        exact = [task for task in tasks if task.name == marker]
        if len(exact) > 1:
            raise AdapterError(f'multiple ClearML tasks for marker {marker}')
        return exact[0].id if exact else None

    def create_task(self, marker: str, job) -> str:
        existing = self.find_by_marker(marker)
        if existing:
            return existing
        if job.benchmark != 'mock':
            raise AdapterError('benchmark runner is not approved')
        task = self.task_class.create(
            project_name=self.config.project_name,
            task_name=marker,
            task_type='testing',
            repo=self.config.repo_url,
            commit=self.config.commit,
            script='runner/mock_eval.py',
            docker=self.config.image,
            argparse_args=[('job_id', job.id), ('model_ref', job.model_ref),
                           ('benchmark', job.benchmark), ('episodes', str(job.episodes))],
        )
        task.add_tags([marker])
        return task.id

    def enqueue_task(self, task_id: str, queue_name: str) -> None:
        task = self.get_task(task_id)
        task.reload()
        state = task.get_status()
        if state in ('queued', 'pending', 'in_progress', 'completed', 'failed', 'stopped', 'closed'):
            return
        if state != 'created':
            raise AdapterError(f'unknown ClearML status {state}')
        try:
            self.task_class.enqueue(task, queue_name=queue_name)
        except Exception:
            task.reload()
            if task.get_status() not in ('queued', 'pending', 'in_progress', 'completed', 'failed', 'stopped'):
                raise

    def get_task(self, task_id: str):
        return self.task_class.get_task(task_id=task_id)

    def abort_task(self, task_id: str) -> None:
        self.get_task(task_id).mark_stop_request()

    def get_console_output(self, task_id: str, limit: int = 100) -> list[str]:
        return list(self.get_task(task_id).get_reported_console_output(number_of_reports=min(max(limit, 1), 1000)))

    def list_queue_tasks(self, queue_name: str) -> list:
        raise AdapterError('queue inventory requires verified ClearML queue API')
