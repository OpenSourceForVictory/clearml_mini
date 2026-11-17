# 旧目录重新纳管与手动镜像准备验证

2026-11-17，目标为 ascend910b3-29，八张 NPU。此前 cgroup 修复仅通过预检，旧目录返回 existing_installation_requires_identity_migration；失败证据与边界保留在 [前次报告](../cgroup-runtime/README.md)。

## 行为变化

从原 root updater 布局识别 /opt/mini-eval-node/app 与 /etc/mini-eval-node，校验全部八卡配置、有效服务、解释器和 root 路径权限，原位恢复，不迁移资产或业务身份。安装前重检配置摘要；服务进程启动时间等瞬态字段不进入摘要。失败恢复 Worker 与辅助组件文件，保留停用状态及原恢复记录。

按用户最新要求，取消纳管中的自动拉取、构建和 runner 容器验收。Docker daemon 仍必须可用；runner 镜像缺失只显示实际镜像与手动命令，实际业务镜像继续由运行环境与调度检查。

## 当前验证证据

- 定向 Python 回归 156 项通过；随后补充服务瞬态字段稳定性等回归 83 项通过，两组有重叠，不相加。
- 浏览器模拟 API 回归 4 项通过，覆盖原目录、原业务身份、缺镜像仍可确认、手动命令、失败拒绝及 HTTP UUID 降级。未统计前端行/分支覆盖率。
- 真实 root SSH 只读预检：host/docker 均无阻塞，识别八张 NPU 与 HwHiAiUser；Docker 使用不存在的 runner 标签仍通过，无 pull/build/run 操作。此项不是完整重新纳管或业务模型评测。
- 本机 API/执行器镜像构建通过，保留现有监听和 socket 配置。

## 全量统计与失败记录

按 scripts.test_coverage 固定口径运行。首次因两个同名 test_bundled_onboarding.py 收集冲突失败，保留 coverage.log；改用 PYTEST_ADDOPTS=--import-mode=importlib，不排除测试。需求从自动镜像准备调整为手动提示，以及布局摘要排除进程瞬态信息期间，中间版本运行被明确中断并保留；不能使用这些部分报告声明门槛通过。最终统计使用 coverage-verified 目录。

PostgreSQL 测试使用独立临时容器和 eval_test 数据库；迁移驱动器使用同一隔离服务器的临时数据库，未在生产数据库执行测试。生产配置备份含凭据，仅保存在 Git 忽略的 .runtime/legacy-reenrollment 下且权限 0600，不提交。

最终全量 Python：1201 passed、100 skipped、2 warnings，耗时 768.88 秒；pytest 和覆盖率门槛均返回 0。总行/分支覆盖率 89.77% / 78.92%；纳管与升级 95.59% / 84.19%。全部目录及要求的三个关键功能均达标，未降低门槛或排除业务代码。详见 [分组统计](python-coverage.md) 和 [跳过原因](skipped-tests.md)。前端未插桩，独立子进程未合并覆盖率，均不在这些统计声明中。

浏览器首次误用默认 API 端口导致连接失败，保留日志；改用本次测试服务端口后 4 项通过。首次收集失败及两次中断的 Python 报告均保留，不用于通过声明。

## 真实重新纳管结果

本机 API 与容器纳管执行器更新至 `legacy-reenrollment` 镜像，实际使用的用户级纳管执行器同步重启，均通过健康检查。未升级其他业务节点。

通过与界面相同的公开接口执行解除纳管 → 检查环境 → 确认 → 等待回连，操作 `d1ae4b22c97a4cf9bca25c7f70e22baa` 返回 `SUCCEEDED / COMPLETE`，节点 enrollment 为 `COMPLETE`。八个 Worker 均 ready，Agent 与 updater 均 ready，远端十个 systemd 服务均 active。

比对安装前后：节点 ID 与八个 slot ID/设备 UUID 不变；八份 Worker 配置除凭据轮换外完全一致（包括 API 地址、业务账号、业务目录及 host/docker 模式）；八个 Worker unit 文件 SHA256 不变；应用、状态、配置路径与服务列表保持原值。新布局通过后续心跳继续在线。

解除纳管按既定规则清空资源池，重新纳管本身不自动恢复。完成检查后，为复原本次验证前环境，通过带 revision 校验的公开资源归属接口将 NPU 2、3 重新分配到原池，其余六卡保持未分配；该动作产生独立归属审计记录。

完整流程使用节点已有 runner 镜像。不存在 runner 标签的真实验证仅覆盖只读预检，完整缺镜像安装由自动化回归覆盖；未删除生产镜像做破坏性试验。没有执行真实业务模型评测，亦不据此声明模型质量或所有业务模板可运行。存储与业务镜像仍受任务准入检查。

原始操作、前后配置摘要、私有备份及测试日志保留在本机忽略目录 `.runtime/legacy-reenrollment/`；文档仅记录不含凭据的结论。
