"""Full-task protocol client using the platform SDK."""
import json
import os
from pathlib import Path
from runner.sdk import EvaluationClient, EpisodeResult, run
from episode import evaluate

class Client(EvaluationClient):
    def run_episode(self, task, episode, context):
        success = evaluate(context.input, episode.id)
        return EpisodeResult(success=success, metrics={'echo_verified': int(success)})

if __name__ == '__main__':
    run(Client(), json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text()))
