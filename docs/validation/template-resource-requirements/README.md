# 模板资源及 NPU 宿主机验收

2026-11-16；API 和五台 GPU 节点已发布，Worker 0.13.0 / 协议 11，Agent 1.1.0。发布后跨机器执行验收及清理已完成。证据见 [evidence.json](evidence.json)。

## 已验证

- CPU/RAM/设备类型、共享模式、默认/最低/覆盖权限经统一解析，模板注册、复制、集合、单任务、编排和独立服务保存有效快照；旧模板保持兼容。
- 真实 host/host、Docker/Docker、host/Docker、Docker/host × 服务共享/独占：8 组通过，真实进程与容器，隔离 API 数据库和两个逻辑节点。
- 完整样例：host/Docker × 服务共享/独占 4 组通过，每组 3 批 × 3 任务 × 2 回合；实际 argv/env、资产、输出与 CPU/RAM 限制检查。另有 host/Docker × 失败/取消 4 组通过，后续批次推进、无待确认清理。容器只读挂载增加 statvfs 校验后两组复测通过，避免把普通文件权限拒绝误认为只读挂载。
- 实际 910B3：NPU Docker 单卡可见和 ACL 内存申请/释放通过；NPU host 使用物理卡 2，系统级 systemd 管理，两个非 root 服务并发，各分配 1 MiB，HTTP 可访问、单卡可见。一服务 SIGKILL 后另一个保持运行；最终 NPU 2 无运行进程、无遗留测试 unit。
- NPU host 隔离探测验证了 cgroup v1 的 CPU/RAM/pids 配额及未分配 NPU 计算设备无法打开。普通用户 GPU host 的 cgroup v2 路径也有真实执行测试。设备共享是预留准入，不是设备显存或算力硬隔离，也不等同完整多租户安全沙箱。
- PostgreSQL：旧数据迁移/降级/再升级、资源契约存在时拒绝破坏性降级、调度并发和编排竞争使用一次性数据库，生产数据未改动。
- 纳管检查及权限修复：只给目标用户委派控制器，不添加管理员组；配置冲突拒绝覆盖；实际验证失败不完成纳管。普通用户 Worker 不自动提升为 NPU 系统级 Worker。

## 回归记录

前一轮后端全量 526 通过、55 跳过；新增纳管回归 26 通过；后续 NPU/资源/纳管回归 28 通过；宿主机真实停止/超时/恢复及所有权回归 6 通过。前端全量 57 通过、9 跳过，纳管浏览器补测 1 通过，TypeScript/Vite 构建通过。PostgreSQL 并发 5 通过，资源迁移保护单独 1 通过。最终后端全量 541 通过、61 跳过（767 秒）；最新调度补测 39 通过。OpenSpec strict 校验通过。

跳过主要为显式环境变量启用的 Docker/宿主机、专用 PostgreSQL 和生产浏览器场景；本页记录的实机/集成项另外显式运行。逻辑节点执行测试与五台物理节点的发布后验收分别记录。

## 关键修正

- cgroup v2 文件存在不等于 CPU 委派生效；短任务直接读取内核配额。本机持久化用户委派并刷新管理器后复验通过。
- 系统级 NPU unit 用非 root 用户运行；原子更新启动清单时保留所有者，修复启动前权限问题，并单独保留 supervisor 诊断日志。
- 昇腾设备访问规则只允许一张卡后，CANN 使用逻辑编号 0；传物理编号 2 会出现设备数为 0，已用实际内存分配验证修正。
- 重启测试需先结束旧执行器写入线程，再构造新执行器，避免把两个活跃写入者当作进程重启。
- 新宿主机资源契约不交给协议 10 及以前的 Worker，防止旧执行器忽略新增验证。

完整样例与使用方式：[examples/scheduling_acceptance](../../../examples/scheduling_acceptance/README.md)。实机 NPU 脚本：[validate_npu_host.py](../../../scripts/validate_npu_host.py)。

## 生产发布

发布源提交 `3abfc71`，API 固定镜像 `sha256:393d93934ea385aef6025e6ad9bca900c01b3b3a29074cf25aef89b77d4d01f1`，节点制品摘要 `5eb4cb855e3989671cc64932cb1ca1c5001f4f0340f5dbc23c07b1ca94c52ab6`。

五台授权 GPU 节点先停新准入，确认持有执行为零后备份。隔离恢复数据库并由 `r841a23` 迁移至 `s952b34`，222 条历史执行记录数量不变；随后正式迁移、启动 API、逐台升级 Worker/Agent，复核摘要、存储及运行环境回执后恢复原准入状态。前端实际响应的两个静态资源与本地构建逐字节一致。

四台远端为 Worker 用户 UID 1000 增加持久化 systemd CPU/memory/pids 委派并刷新用户管理器；本机 UID 1001 已就绪。五台均通过短任务实际内核配额验证，没有重启主机、终止已有业务或更改登录密码。

生产 Compose 使用 `.runtime/resource-contract/release/image.yml` 作为固定镜像 overlay，同时保留 `deploy/compose.yml` 和 `deploy/lan-compose.yml`。发布阶段记录、0600 数据库备份和恢复校验位于 `.runtime/resource-contract/release/`，不入 Git。后续维护应沿用完整 Compose 配置，不能用旧镜像 overlay 覆盖新版本。

上述 GPU 发布阶段的 NPU 验证仅为独立执行探测。随后已完成公网 NPU 节点常驻纳管，详见 [服务端接入](../../ascend-server-node.md) 和 [实机验收记录](npu-enrollment.json)；自动 SSH 向导的通用 NPU 安装仍不在本次范围。

生产跨机器 8 组（16 批、48 个客户端任务）全部通过；3 类 GPU 的 9 个 CUDA 分配任务覆盖五台机器。超额 CPU/RAM 拒绝、显存不足排队和取消清理通过。验收模板已停用，临时节点配置已按修订号恢复，最终五台节点均启用、在线、持有执行为零。脱敏证据见 [production.json](production.json)。
