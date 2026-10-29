"""HTTP protocol example for the simulation SDK; not a real robot benchmark."""
import json
from .sdk import EvaluationClient, EpisodeResult
from .cs_demo import evaluate


class DemoClient(EvaluationClient):
    def run_episode(self, task, episode, context):
        _, results = evaluate({**task, 'episodes': 1, 'seed': episode.seed})
        result = results[0]
        return EpisodeResult(success=result['success'], metrics={'steps': result['steps']})

    def setup(self, context):
        (context.output_dir/'report.json').write_text(json.dumps({'kind': 'cs-protocol-check',
            'description': 'Per-episode results are reported through the platform SDK.'}))
