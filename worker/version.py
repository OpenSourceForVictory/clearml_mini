"""Release metadata, independent of protocol compatibility."""
import json
from pathlib import Path

SOFTWARE_VERSION='0.21.22'
PROTOCOL_VERSION=18
CAPABILITIES=['docker','host','episode_reporting','typed_parameters','directory_mounts','inference_services','task_input','command_tasks','accelerator_contract','ascend_docker','ascend_host','service_readiness_v1','host_login_environment','host_episode_launch','bounded_logs_v2','process_logs_v1','process_log_policy_v1','shared_artifacts_v1','artifact_roots_v1','task_result_reporting_v1','episode_timing_v1']


def recovery_config(config):
    """Use durable Worker state, never a task workspace, for the shared outbox."""
    value = dict(config or {})
    if not value.get('node_outbox_root'):
        state = value.get('state_path')
        if isinstance(state, str) and Path(state).is_absolute():
            parent = Path(state).parent
            if parent.resolve() == parent:
                value['node_outbox_root'] = str(parent/'reliable-outbox')
    return value


def metadata(config=None):
    config = recovery_config(config)
    receipt=Path(__file__).resolve().parent.parent/'.worker-release.json'
    digest=None
    if receipt.is_file():digest=json.loads(receipt.read_text()).get('sha256')
    from .probe_dependencies import ready
    caps=list(CAPABILITIES) if ready() else [c for c in CAPABILITIES if c!='service_readiness_v1']
    enabled = config.get('recovery_execution_enabled', bool(config.get('node_outbox_root')))
    if enabled is True:
        root = config.get('node_outbox_root')
        valid = (isinstance(root, str) and bool(root) and Path(root).is_absolute()
                 and not Path(root).is_symlink() and Path(root).parent.resolve() == Path(root).parent
                 and bool(config.get('node_name')))
        if not valid:
            if config.get('recovery_execution_enabled') is True:
                raise ValueError('recovery execution requires node_name and an absolute shared node_outbox_root')
            return {'version':SOFTWARE_VERSION,'build_digest':digest,'capabilities':caps}
        caps.append('disconnected_execution_v1')
    return {'version':SOFTWARE_VERSION,'build_digest':digest,'capabilities':caps}
