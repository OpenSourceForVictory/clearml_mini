# root 管理与普通业务身份验证

日期：2026-11-17。覆盖率门槛：新增核心模块综合覆盖率 ≥90%，启用分支测量。

## 结果

- [覆盖率报告](coverage.txt)：38 项通过，无跳过；`worker/host_identity.py` 与 `scripts/install_system_node.py` 行覆盖率 100%，综合覆盖率 99%。其中 5 项在本机 root/systemd 下真实执行（权限拒绝及四种业务启动方式）。
- 关联 Worker/SSH/安装/升级/加速设备回归：104 项通过、12 项因环境前提跳过；新增回滚保护后另跑 43 项相关回归通过；最终系统安装器 22 项通过。
- Agent 模块、故障、集成与节点 API：47 项通过。
- TypeScript 检查与生产构建通过；Playwright SSH 向导 1 项通过，校验 root 默认身份、NPU 选择、UID/GID 提交及安装计划展示。
- 生产浏览器检查通过；API 安装包包含新系统安装器与 Worker 0.16.0；纳管执行器正常。发布检查 6 节点无占用执行，未迁移现有节点身份。见 [发布记录](release.json)。
- [独立安装验收](isolated-install.json)：在无网络、无生产数据挂载的独立 systemd 容器中，实际创建 mini-eval、安装两个模拟设备 Worker 及 Agent/updater、验证配置权限和普通账号资源限制、拒绝重复安装，最后停止全部测试服务。

## 失败与修正

1. 直接从私有 `/home/dgh` 执行真实测试时，普通账号无法读取平台代码；权限检查正确拒绝。随后使用可读的独立平台代码副本验证，未放宽用户 HOME 权限。
2. 初次安装容器依赖下载阻塞，改用本机已有系统组件。容器缺少标准 Python 和 D-Bus 时，安装器拒绝验证；补齐测试前提后继续。
3. 真实安装发现 Worker 单元 `WorkingDirectory` 被错误加引号，systemd 报 “path is not absolute”。修复路径输出，并为多卡安装测试增加实际 `systemd-analyze verify`，避免仅通过字符串替身。修正后完整安装成功；失败安装没有被误记为通过。

## 边界和复现

这些是平台执行/安装验收，不是模型评测。隔离安装使用两张模拟设备及 API 替身（任务接口返回 503），验证系统安装与启动，不代表完成真实平台注册/领取任务闭环。NPU 选择与身份使用解析器和契约测试，本次未在实体 NPU 上重新安装账号。现有节点身份迁移仍须单独排空实施。

普通测试与覆盖率命令见 [root 纳管指南](../../root-managed-host.md)。隔离安装脚本 [isolated_install.py](isolated_install.py) 只能在可销毁、具备 systemd、D-Bus、cgroup v2 和 `/usr/bin/python3` 的容器中运行；安装包解压到 `/tmp/bundle`，显式设置 `MINI_EVAL_DISPOSABLE_INSTALL=1`。它创建系统账号与服务，不能在已纳管生产机执行。产物写入 `/tmp/result.json`。
