class RoboTwinRunner:
    def evaluate(self, job_id: str, model_ref: str, benchmark: str, episodes: int) -> dict:
        raise NotImplementedError('RoboTwin runner requires pinned upstream version, assets, checkpoint contract and GPU validation')
