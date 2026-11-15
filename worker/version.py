"""Release metadata, independent of protocol compatibility."""
import json
from pathlib import Path

SOFTWARE_VERSION='0.11.0'
PROTOCOL_VERSION=9
CAPABILITIES=['docker','host','episode_reporting','typed_parameters','directory_mounts','inference_services']


def metadata():
    receipt=Path(__file__).resolve().parent.parent/'.worker-release.json'
    digest=None
    if receipt.is_file():digest=json.loads(receipt.read_text()).get('sha256')
    return {'version':SOFTWARE_VERSION,'build_digest':digest,'capabilities':CAPABILITIES}
