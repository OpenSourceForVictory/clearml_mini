"""Release metadata, independent of protocol compatibility."""
import json
from pathlib import Path

SOFTWARE_VERSION='0.21.3'
PROTOCOL_VERSION=17
CAPABILITIES=['docker','host','episode_reporting','typed_parameters','directory_mounts','inference_services','task_input','command_tasks','accelerator_contract','ascend_docker','ascend_host','service_readiness_v1','host_login_environment','host_episode_launch','bounded_logs_v2']


def metadata():
    receipt=Path(__file__).resolve().parent.parent/'.worker-release.json'
    digest=None
    if receipt.is_file():digest=json.loads(receipt.read_text()).get('sha256')
    from .probe_dependencies import ready
    caps=CAPABILITIES if ready() else [c for c in CAPABILITIES if c!='service_readiness_v1']
    return {'version':SOFTWARE_VERSION,'build_digest':digest,'capabilities':caps}
