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
