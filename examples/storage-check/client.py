"""Storage acceptance client; verifies mounts, not simulation/model quality."""
import json
import os
import time
from runner.sdk import EvaluationClient, EpisodeResult


class StorageClient(EvaluationClient):
    def run_episode(self, task, episode, context):
        assets=(context.assets_dir/'marker.txt').read_text()
        cache=(context.cache_dir/'marker.txt').read_text()
        assert assets==cache
        assert os.statvfs(context.assets_dir).f_flag & os.ST_RDONLY
        assert os.statvfs(context.cache_dir).f_flag & os.ST_RDONLY
        (context.work_dir/'write-check.txt').write_text(assets)
        evidence={'assets':str(context.assets_dir),'cache':str(context.cache_dir),
            'work':str(context.work_dir),'output':str(context.output_dir),'marker':assets,'episode':episode.id}
        filename=f'mounts-{episode.id}.json'
        (context.output_dir/filename).write_text(json.dumps(evidence))
        print(json.dumps({'storage_mount_check':evidence}),flush=True)
        time.sleep(task['parameters'].get('sleep_seconds', 1))
        return EpisodeResult(success=True, metrics={'mount_check':1}, artifacts=[filename])
