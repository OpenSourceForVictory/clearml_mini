"""Image advice is non-executing and never substitutes a custom business image."""
import pytest
from onboarding.docker_prepare import guidance
from onboarding.contracts import Rejected


@pytest.mark.parametrize('present',[True,False])
@pytest.mark.parametrize('image',['mini-eval-runner:dev','registry.example/runner:arm64','registry/runner@sha256:abc'])
def test_manual_guidance_uses_actual_image_and_never_claims_runtime_verified(image,present):
    value=guidance(image,present)
    assert value['image']==image and value['present']==present and not value['runtime_verified']
    assert value['command']==('docker build -f runner/Dockerfile -t '+image+' .' if image=='mini-eval-runner:dev' else 'docker pull '+image)
    assert value['context']


@pytest.mark.parametrize('image',[None,'','-bad','x;touch /tmp/bad','$(id)','a'*501])
def test_invalid_image_cannot_inject_a_suggested_command(image):
    with pytest.raises(Rejected,match='runner_image_invalid'):guidance(image,False)
