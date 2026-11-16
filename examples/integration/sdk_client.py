"""Protocol-only demo: replace the predicate with your business evaluation."""
from runner.sdk import EvaluationClient, EpisodeResult


class Client(EvaluationClient):
    def run_episode(self, task, episode, context):
        # context.service_endpoint is the assigned URL, not a hardcoded host.
        success = not context.business.get('force_failure', False)
        return EpisodeResult(success=success, metrics={'seed': episode.seed})
