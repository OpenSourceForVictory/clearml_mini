# 通用命令：客户端与服务端

界面入口：管理设置 → 运行模板 → 注册新模板 → 命令解析方式、启动命令；页面提供 Python、Bash、可执行文件示例。用户指南中的“宿主机环境”和“运行模板”也说明使用方式。

宿主机与 Docker 都支持通用命令。程序、参数、工作目录和环境变量分别配置，不把整条 shell 字符串当作程序名。绝对路径原样使用，`./` 路径基于业务目录，裸程序名通过最终 PATH 查找。参数里的空格、分号等不会自动解释成 Shell。需要管道/重定向时显式指定 Bash，避免将不可信业务参数拼入脚本文本。

## Docker 示例

```json
{
  "name":"native-client", "version":"1", "image":"my-business:1",
  "execution_mode":"docker", "task_type":"command", "integration_mode":"direct",
  "command":["./bin/evaluator","--config","configs/eval.json"],
  "runtime":{
    "command_mode":"exec", "interpreter":"/opt/platform/bin/python",
    "working_directory":"/app", "work_dir":"/tmp/mini-eval-work",
    "output_dir":"/tmp/mini-eval-output", "environment":{}, "assets":{}
  }
}
```

镜像需预装平台 Python（Python 3.10+；建议 3.11+），以及业务所需运行时、代码、动态库。平台 Python 只用于执行管理和 SDK 包装，业务可以是另一 Python、Bash 或无需 Python 的二进制；本特性不意味着支持完全没有平台 Python 的镜像。Worker 注入平台 runner，业务普通命令无需主动接入 SDK。

容器业务目录必须是容器内已存在的绝对路径，可来自镜像或目录挂载；留空使用平台 work 目录。设置目录不会自动创建业务代码、挂载目录或改变镜像用户。挂载权限须与镜像运行用户匹配。平台 work/output 与业务 cwd 独立，产物写 MINI_EVAL_OUTPUT_DIR，避免并发写共享代码。

服务端也使用上述方式，任务类型保留评测/服务生命周期，通过平台参数映射获得监听地址与端口，通过就绪检查后分配客户端。SDK 命令模式每回合执行一次，必须写结果 JSON；普通命令一次执行按退出码判断，不虚构回合完成与模型成功率。

## 兼容性与验证

新字段需要 Worker 0.17.0 / 协议 14；未升级节点在调度预检显示升级提示。复制旧模板保留兼容模式，确认 cwd 后切换通用模式。此变更不自动发布或升级节点。

复现测试：

```bash
.venv/bin/python -m pytest backend/tests/test_generic_commands.py -q
GENERIC_DOCKER_TEST=1 GENERIC_DOCKER_IMAGE=mini-eval-api:root-managed-20261117 .venv/bin/python -m pytest backend/tests/test_generic_commands.py -q
```

Docker 测试镜像可替换为含 `/usr/local/bin/python`、Bash、与主机编译产物兼容的 glibc 镜像。测试需要本机 `cc`，容器使用当前 UID/GID、无网络、无额外 capabilities。测试覆盖真实进程与容器入口，服务端样例运行后退出，不能代替长驻推理服务的就绪与恢复验收，也不是模型评测。

本次结果与失败证据见 [验收记录](validation/generic-commands/README.md)。
