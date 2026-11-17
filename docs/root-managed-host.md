# root 纳管与普通业务账号

在界面「管理设置 → 机器管理 → 注册新机器」使用 root SSH（推荐密钥），选择 NVIDIA GPU 或 Ascend NPU、设备编号，并勾选宿主机、容器中的一项或两项。两项都选时会同时检查 Docker 和宿主机资源限制；旧节点重新纳管仍保留原执行方式。root 账号由机器管理员准备；平台不会设置 root 密码或修改 SSH 登录策略。

GPU 和 NPU 均需确认“设备共享批准”：新安装默认“不允许共享”；只有明确选“允许共享”才把所选设备类型的 `gpu_runtime.allow_shared` 或 `npu_runtime.allow_shared` 写入每张设备的本地 Worker 配置。重新纳管默认“保留节点原设置”，检查计划会逐卡显示原值；管理员也可明确选择全部开启或关闭。该选择随重新纳管的排空、备份和回滚流程生效；已运行任务先完成清理。节点批准与资源池的“允许加速设备共享”是两道独立条件，任务还需选择共享并申请大于 0 GiB 设备内存。节点并不把本地原始 JSON 上报给平台，而是在注册时上报对应的 `gpu:*:shared` 或 `npu:*:shared` 能力。已有 GPU 节点未配置 `gpu_runtime` 时保留原资源池共享行为，重新纳管可明确设置。

已纳管且需立即手工调整的其他节点，先在平台停用新任务调度并等待执行清理；在节点上用 `systemctl show <Worker服务名> -p ExecStart --value` 找到 `--config` 后的配置路径，不照搬其他节点目录。备份对应 JSON，核对 `accelerator_type=npu`、`npu_runtime.approved=true`，仅修改需要共享的卡的 `npu_runtime.allow_shared`，保留文件属主和权限；重启对应 Worker 后确认共享能力上报。不要展示整个 JSON，其中可能有节点凭据。GPU 节点对应字段为 `gpu_runtime.allow_shared`；手工修改不替代资源池共享开关。

新安装的 Agent、Worker 和升级执行器是 root 系统服务；宿主机客户端与服务端业务通过 systemd 的 User/Group 以 `mini-eval` 普通账号启动，CPU/内存/进程限制仍生效。业务不能通过模板指定 root。该账号默认 UID/GID 29000、禁止交互登录、不加入 sudo/docker 组。已有同名或同 ID 的不兼容账号会阻止安装。容器仍使用镜像/容器运行配置中的用户，这个功能不会改写镜像身份。

## 目录与共享存储

- 控制目录 `/var/lib/mini-eval/control`：代码可供业务读取，配置和恢复状态仅 root 可读写。
- 业务 HOME `/var/lib/mini-eval/business`；默认工作目录 `/var/lib/mini-eval/workspaces`，每张设备有独立子目录和 Worker 恢复记录。
- SSH 向导高级设置可指定业务 UID/GID。共享 NFS 的节点应保持相同数值，并由存储管理员授权。不要依赖 root 绕过 NFS root_squash。
- 执行前会按真实业务账号检查 Python、代码根目录、业务执行目录、逻辑资产、工作/输出及缓存权限。报错会指出账号和路径；祖先目录需要可遍历，代码/资产需要可读，输出/缓存需要可写。
- 对持续更新的业务目录，优先在存储端设置与业务 UID/GID 一致的共享组、setgid 目录及默认 ACL，让新文件继承权限；只给必要的父目录遍历权限，代码/资产读权限，工作/输出/缓存写权限。先以 `sudo -u mini-eval test -r <文件>` 或 `test -w <目录>` 验证，不要全树 `chmod 777`。
- 安装器不会递归修改已有业务代码或资产树权限。私有 HOME 中的 Python 环境如果不可访问，应迁至可共享目录或授予准确的 ACL。NPU 驱动、CANN 及设备访问权限由节点管理员准备；设备隔离探测失败不能跳过。

## 旧节点与故障恢复

升级或同账号重新纳管保留原身份和路径。已有 huawei 用户服务不能直接用 root 覆盖；先排空，再按维护计划迁移服务、配置、恢复记录与目录权限。此次发布不自动迁移现有节点。

全新安装串行持有安装锁。失败时停止并移除本次创建的服务和控制目录，保留普通账号及工作数据以供重试。如果停止失败，会保留安装证据，节点继续保持未完成纳管；检查日志与任务状态后处理，不能盲目删除恢复记录。已有安装的恢复由重新纳管流程负责。

## 测试与覆盖率

```bash
.venv/bin/python -m pip install -r backend/tests/requirements-coverage.txt
.venv/bin/python -m coverage run --rcfile=backend/tests/managed-host.coveragerc -m pytest backend/tests/test_managed_host.py backend/tests/test_system_node_installer.py -q
.venv/bin/python -m coverage report --rcfile=backend/tests/managed-host.coveragerc
```

新增身份和系统安装模块启用分支测量，综合覆盖率门槛 90%。普通测试不需要 root。真实 systemd 测试需在隔离验证主机准备 mini-eval 账号，以 root 设置 `MANAGED_HOST_TEST=1` 运行相同命令；测试实际创建短期系统单元并清理，不能把跳过算通过。

测试覆盖账号冲突/幂等、多卡配置隔离、配置权限、失败回滚、保留恢复证据、重复安装、GPU/NPU 选择、缺失身份、权限拒绝、CPU/内存限制、四种业务运行方式、独立 Python/cwd 和清理。SSH 探测和旧节点升级另由关联回归覆盖。结果与实际设备验证边界见 [验证记录](validation/root-managed-host/README.md)。

实体 GPU 节点解除/重新纳管及任务执行闭环另见 [节点生命周期真机验收](validation/node-lifecycle/README.md)，该测试保留原运行身份，不等于完成身份迁移。

 
## cgroup 兼容与 runner 准备（0.17.1）

root 系统宿主机支持 cgroup v1、v2 和混合层级，以实际 CPU、内存、进程数限制验证为准；挂载点显示 tmpfs 不等于不支持。非 root 旧用户服务仍要求 v2 委派，不自动迁移身份或修改引导参数。单纯重启旧镜像不会获得修复，控制面安装包与 SSH 执行器需要同步更新，已部署节点按升级/重新纳管流程更新。

Docker-only 纳管不运行宿主机 systemd 限制探针，只要求 Docker daemon 可用。runner 镜像存在性作为提示，不拉取、构建或试运行；纳管成功不代表容器限制与 NPU/CANN 业务已验证。

已有非标准目录安装仍会被保护性拒绝，需要受控迁移。真实验证范围及失败记录见 [cgroup 与重新纳管验证](validation/cgroup-runtime/README.md)。

镜像准备由用户管理：Docker 纳管仅要求 daemon 可用，缺少平台 runner 或业务镜像不阻止安装。界面提供实际镜像引用与手动命令，纳管不运行镜像验收容器；运行任务仍需批准且就绪的业务镜像。默认 runner 构建方法见 [SSH 纳管说明](ssh-onboarding-executor.md)。
