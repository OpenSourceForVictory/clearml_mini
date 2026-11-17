# root 纳管与普通业务账号

在界面「管理设置 → 机器管理 → 注册新机器」使用 root SSH（推荐密钥），选择 NVIDIA GPU 或 Ascend NPU、设备编号及执行方式。root 账号由机器管理员准备；平台不会设置 root 密码或修改 SSH 登录策略。

新安装的 Agent、Worker 和升级执行器是 root 系统服务；宿主机客户端与服务端业务通过 systemd 的 User/Group 以 `mini-eval` 普通账号启动，CPU/内存/进程限制仍生效。业务不能通过模板指定 root。该账号默认 UID/GID 29000、禁止交互登录、不加入 sudo/docker 组。已有同名或同 ID 的不兼容账号会阻止安装。容器仍使用镜像/容器运行配置中的用户，这个功能不会改写镜像身份。

## 目录与共享存储

- 控制目录 `/var/lib/mini-eval/control`：代码可供业务读取，配置和恢复状态仅 root 可读写。
- 业务 HOME `/var/lib/mini-eval/business`；默认工作目录 `/var/lib/mini-eval/workspaces`，每张设备有独立子目录和 Worker 恢复记录。
- SSH 向导高级设置可指定业务 UID/GID。共享 NFS 的节点应保持相同数值，并由存储管理员授权。不要依赖 root 绕过 NFS root_squash。
- 执行前会按真实业务账号检查 Python、代码根目录、业务执行目录、逻辑资产、工作/输出及缓存权限。报错会指出账号和路径；祖先目录需要可遍历，代码/资产需要可读，输出/缓存需要可写。
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
