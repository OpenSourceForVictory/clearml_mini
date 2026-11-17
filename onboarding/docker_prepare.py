"""Manual image guidance only; node enrollment never pulls or builds images."""
import re
from .contracts import Rejected

DEFAULT_IMAGE='mini-eval-runner:dev'


def guidance(image, present):
    if not isinstance(image,str) or len(image)>500 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*',image):
        raise Rejected('runner_image_invalid')
    return {'image':image,'present':bool(present),'runtime_verified':False,
            'command':('docker build -f runner/Dockerfile -t '+image+' .' if image==DEFAULT_IMAGE else 'docker pull '+image),
            'context':('在项目根目录或完整节点安装包的解压目录执行；镜像仅提供平台 runner，不含业务模型或 CANN 环境。' if image==DEFAULT_IMAGE else '在目标节点执行；私有镜像请先配置仓库认证，使用与节点架构匹配的镜像。')}
