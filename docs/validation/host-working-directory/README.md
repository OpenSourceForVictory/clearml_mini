# 宿主机独立业务执行目录验收（2026-11-16）

## 行为

模板可独立指定宿主机环境与 working_directory，Python 路径与业务目录无须相同。客户端 direct、SDK 命令、普通命令及服务端均读取 Worker 解析的目录；任务临时数据和输出路径不变。省略字段保持旧行为。新字段要求协议 13，旧节点不会静默忽略。

## 验证记录

- 首轮 host/环境/命令测试 27 passed、3 个 systemd 测试按开关跳过；随后开启 HOST_EXECUTION_TEST，宿主机后端 8 passed。
- HOST_EXECUTION_TEST=1 的目录、SDK 包、模板版本、调度、编排关联回归 50 passed。包含四种入口的真实子进程和真实 systemd 单元；解释器来自单独 venv，业务从另一目录读取相对配置。所有测试单元结束后清理。
- 补充客户端/服务端 API 注册及 clone 保留字段，generic_templates 3 passed。
- 模板与配置界面回归 11 passed；补强新字段只读与复制检查后 2 passed。线上静态前端的同组 2 passed，API 使用 mock，不创建生产业务任务。
- TypeScript、Vite build 与 OpenSpec strict 校验通过；SDK ZIP 从源文件重新构建并通过一致性/离线自检。

## 发布

API 镜像 `mini-eval-api:host-cwd-20261116`；无数据库迁移。发布前确认节点无持有执行并核对 Compose 配置，保留 LAN 地址与原有配额。

Worker 0.15.0 / 协议 13 通过平台升级到六个在线节点。初次四个较旧 GPU 节点缺少固定健康探测依赖，网络自动准备未成功，升级验证失败并回滚；未改动业务参数或放宽版本验证。确认它们均为 x86_64/Python 3.10 后，从已验证同平台依赖目录同步固定版本 aiohttp 3.14.5、aiodns 3.6.1、async-timeout 5.0.1 及依赖，验证可导入；保留首次失败历史，确认回滚后取消失败升级，再发起同一发布。最终六节点 SUCCEEDED、所有设备在线、协议和发布摘要匹配，摘要记录见 worker-upgrades.json。

本次验证的是业务进程的解释器、cwd 和平台生命周期，不是 GPU/NPU 模型推理质量测试；没有声称在每个生产节点重新跑完整仿真批次。后续跨版本升级应在排空前检查固定运行依赖可用性，避免进入升级后才等待公网下载。

## 补充可载入样例

新增三个 `-cwd` 模板，分别覆盖 SDK 客户端、直接协议客户端和服务端。模板目录校验与真实 HTTP 回显测试 3 passed，Docker 专项 1 skipped（本次未运行）；独立目录专项此前重新执行 20 passed，含 systemd。注册页可下载的说明包含独立 Python 路径、代码根目录、业务子目录与命令对应关系。静态 catalog/README 已同步到当前 API 容器并通过 HTTP 读取验证，未重建 API 镜像；后续镜像发布需包含仓库最新 public 文件。未自动注册模板或修改节点批准环境。

## 平台真实批次验证（2026-11-16）

通过线上 API 注册三份 `目录分离验收-…-cwd` 模板和两个三任务集合，SDK-command、direct 客户端各提交一批，每任务两回合。两端均由 local4090-151 的 Worker 以宿主机方式启动，解释器为项目 `.venv/bin/python`，批准根目录为项目根目录，业务执行目录为 `examples/runtime_templates`。每个进程预约共享 GPU 显存 1 GiB、CPU 1、内存 1 GiB（回显业务不实际计算 GPU）。节点每卡并发上限 2，使服务端与一个客户端并行，剩余任务及另一批次排队。

初次配置遗漏服务端角色，且添加角色后服务网络误用了旧内网地址，造成绑定失败；保留首轮失败历史，取消首轮批次，将地址更正为当前节点 192.168.8.151 后重新提交。两批共 6 个任务全部 SUCCEEDED，12 回合完成；客户端 cleanup_confirmed 为 true，两个服务 STOPPED 且无待清理，节点 held_executions 为 0。

运行期间读取真实服务进程 `/proc/4026736/cwd`，确认 cwd 为 `/home/dgh/Code/clearml_mini/examples/runtime_templates`；argv 使用 `/home/dgh/Code/clearml_mini/.venv/bin/python` 启动 server.py。该 venv 的解释器最终链接到 `/opt/anaconda3/bin/python3.13`，这不改变选用 venv 的启动方式。客户端短生命周期未捕获到 /proc 目录证据；客户端启动目录依靠前述真实子进程/systemd 专项测试和本次保存的模板快照验证，不将服务端观测冒充客户端观测。

[脱敏执行证据](platform-evidence.json) 包含批次、回合、清理和服务进程目录。注册模板、集合及本机 template_demo_cwd 环境保留可复用，本机服务端角色与端口范围 19200–19210 保留；没有驻留测试业务进程。本次是模拟 HTTP 业务的实际平台调度验收，不是模型推理质量测试。
