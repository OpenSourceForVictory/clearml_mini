"""Update the priority of batches that still have queued work."""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .core import EvaluationBatch, Job, now
from .scheduling import lock


class PriorityUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    priority: int = Field(ge=0, le=9)
    expected_revision: int = Field(ge=0)


def install(app, db, current):
    from .monitoring import owned

    @app.patch('/api/v1/batches/{batch_id}/priority')
    def update_priority(batch_id: str, body: PriorityUpdate,
                        user=Depends(current), s=Depends(db)):
        lock(s)
        batch = owned(s, batch_id, user)
        if batch.control_revision != body.expected_revision:
            raise HTTPException(409, 'batch changed; refresh before retrying')
        pending = s.scalar(select(Job.id).where(
            Job.batch_id == batch.id, Job.status == 'PENDING').limit(1))
        if pending is None:
            raise HTTPException(409, 'batch has no queued tasks; priority can no longer be changed')
        batch.priority = body.priority
        batch.control_revision += 1
        batch.control_updated_at = now()
        s.commit()
        from .batches import batch_view
        return batch_view(s, batch)
