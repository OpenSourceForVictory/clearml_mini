# SSH 注册与解除纳管实测（2026-11-27）

使用当前代码及 API 镜像 `mini-eval-api:priority-default-20261127`，在独立 Compose 项目和独立 PostgreSQL 上，通过真实 API、SSH、systemd、Agent、Worker、updater 完成注册与解除纳管。目标是可销毁的 Debian systemd 容器，透传本机 RTX 4090；这是实际组件与 GPU 身份注册测试，不是独立实体机器或业务模型评测。测试调用真实管理 API，未在浏览器里点击确认安装与解除按钮。

| 步骤 | 实测结果 |
| --- | --- |
| SSH 主机指纹确认、环境检查 | `NEEDS_HOST_KEY` → `READY / ENVIRONMENT`，检查问题为空 |
| 安装和回连验收 | `INSTALLING` → `SUCCEEDED / COMPLETE`；Agent、Worker、updater 运行，机器状态 `online`，占用执行数 0 |
| 解除纳管 | 管理 API 返回 `unmanaged`、`enabled=false`；机器列表状态一致 |
| 旧 Worker 凭据 | 请求 `worker/self` 返回 HTTP 401 |
| 测试数据清理 | 删除已解除的测试机器记录，列表不再显示；销毁隔离节点、Compose 项目及其数据库卷，移除临时 SSH 公钥副本与隔离环境文件 |

原始脱敏状态留在未跟踪的 `.runtime/onboarding-live-20261127/isolated-check.json`、`isolated-install.json`、`isolated-unmanage.json`。没有保存管理员令牌、SSH 私钥或 Worker 令牌到验证文档。

先尝试把同一隔离容器注册到本机现有平台，环境检查通过，但 Worker 注册收到 HTTP 409：`GPU UUID already registered to another slot`。原因是容器透传了已被本机节点注册的同一张 GPU；这是正确的重复设备保护。该测试记录已在原平台解除并删除，容器也重新创建。之后改用独立数据库，完整闭环通过。现有实体节点 `rtx3080-182` 仅执行了只读预检，得到 `existing_layout_untrusted`、`existing_installation_requires_identity_migration`，没有解除或重装它。

验证边界：本次说明新节点的 SSH 安装、回连、解除纳管和凭据撤销链路能在隔离平台工作。它不能证明另一套平台出现的“环境检查未完成”已经修复；该故障仍需对应执行器的 `onboarding unexpected failure` 诊断行。容器共用宿主 GPU，因此不能代替独立实体机器或浏览器逐按钮验收。
