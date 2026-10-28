from typing import Protocol


class EvaluationRunner(Protocol):
    def evaluate(self, job_id: str, model_ref: str, benchmark: str, episodes: int) -> dict: ...
