"""Conservative scheduler: one physical slot, one active run, leader via PostgreSQL advisory lock."""
import os
import time
from datetime import timedelta
from sqlalchemy import select, text
from .core import Job, Run, Slot, User, now, session_factory

ACTIVE = ('RESERVED', 'ENQUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED')
TTL = int(os.getenv('WORKER_TTL_SECONDS', '30'))


class FakeAdapter:
    """Local development only; never claims remote execution."""
    def __init__(self):
        self.tasks = {}

    def find_by_marker(self, marker):
        return self.tasks.get(marker)

    def create_task(self, marker, job):
        task_id = marker
        self.tasks[marker] = task_id
        return task_id

    def enqueue_task(self, task_id, queue):
        return None


def tick(factory, adapter):
    with factory() as session:
        if session.bind.dialect.name == 'postgresql':
            locked = session.scalar(text('SELECT pg_try_advisory_xact_lock(82510401)'))
            if not locked:
                return False
        active = session.scalars(select(Run).where(Run.status.in_(ACTIVE))).all()
        busy = {r.slot_id for r in active if r.slot_id}
        counts = {}
        for run in active:
            owner = session.get(Job, run.job_id).owner_id
            counts[owner] = counts.get(owner, 0) + 1
        slots = session.scalars(select(Slot).where(Slot.enabled)).all()
        free = [slot for slot in slots if slot.id not in busy and slot.last_seen_at
                and now() - slot.last_seen_at.replace(tzinfo=slot.last_seen_at.tzinfo or now().tzinfo) < timedelta(seconds=TTL)]
        candidates = session.scalars(select(Job).where(Job.status == 'PENDING').order_by(Job.created_at, Job.id)).all()
        for job in candidates:
            if counts.get(job.owner_id, 0) >= 2:
                continue
            slot = next((x for x in free if x.pool_id == job.pool_id), None)
            if slot is None:
                continue
            run = session.scalar(select(Run).where(Run.job_id == job.id).order_by(Run.attempt.desc()))
            run.slot_id, run.queue_name, run.status, run.reserved_at = slot.id, slot.queue_name, 'RESERVED', now()
            job.status = 'RESERVED'
            session.commit()
            marker = f'eval:{job.id}:{run.attempt}'
            task_id = adapter.find_by_marker(marker) or adapter.create_task(marker, job)
            adapter.enqueue_task(task_id, slot.queue_name)
            run.clearml_task_id, run.status, run.enqueued_at = task_id, 'ENQUEUED', now()
            job.status = 'ENQUEUED'
            session.commit()
            return True
        return False


def main():
    factory = session_factory()
    adapter = FakeAdapter()
    while True:
        tick(factory, adapter)
        time.sleep(float(os.getenv('SCHEDULER_TICK_SECONDS', '2')))


if __name__ == '__main__':
    main()
