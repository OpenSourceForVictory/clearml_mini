# SDK 与评测协议测试设计

## 范围与判定原则

验证业务程序 → SDK/命令适配器 → 本地事件文件 → Worker → API → 批次汇总的边界。现有接口是 `EvaluationClient.run_episode` 返回 `EpisodeResult`；上下文管理器示例不是当前已实现接口。

执行完成与业务成功分开判定：程序跑完、回合结果完整可判执行成功，所有回合业务失败时成功率应为零。未完成回合不得默认成功，也不能伪装成完整评测。数据库测试使用隔离 SQLite；这些测试不证明 PostgreSQL 并发锁、真实多机 GPU 或模型评测已经验收。

## 当前可执行用例

| ID | 输入或故障注入 | 预期结果 | 自动化位置 |
| --- | --- | --- | --- |
| SDK-01 | 两回合分别成功和失败 | 成功率 0.5，teardown 执行 | test_evaluation_framework.py |
| SDK-02 | 两回合全部业务失败 | 执行可 SUCCEEDED，成功率 0 | test_sdk_reporting_contract.py |
| SDK-03 | 计划 100，完成 80，成功 60 | planned=100，completed=80，success_rate=0.75；拒绝最终成功汇总 | test_sdk_reporting_contract.py |
| SDK-04 | 回合 ID 超出计划、种子错误 | 拒绝且不增加进度 | test_sdk_reporting_contract.py |
| SDK-05 | bool 冒充 ID、整数冒充 success、负 runtime、字符串指标、未知字段、越界产物名 | 拒绝且不写入有效结果 | test_sdk_reporting_contract.py |
| SDK-06 | SDK 返回 None、非布尔 success、NaN 指标或越界产物 | 生成 ERROR，不生成最终成功，执行 teardown；持久事件与 stdout 一致 | test_sdk_reporting_contract.py |
| SDK-07 | 完成先于开始、同事件重发、同回合结果冲突 | 无开始拒绝；相同重发幂等；冲突返回 409 | test_evaluation_framework.py |
| SDK-08 | 汇总成功率与回合记录不同 | 拒绝最终 SUCCEEDED | test_evaluation_framework.py |
| SDK-09 | 其他 Worker 或租约过期的执行上报 | 拒绝，不改变进度 | test_sdk_reporting_contract.py |
| SDK-10 | 失败后重试 | 新尝试进度为零，历史尝试结果仍可查 | test_evaluation_framework.py |
| SDK-11 | 上报响应丢失 | Worker 重发同一事件，确认前不移动游标 | test_evaluation_framework.py、test_sdk_reporting_contract.py |
| SDK-12 | 文件尾部只有半行 JSON | 等完整换行后再发送，不丢失尾部 | test_sdk_reporting_contract.py |
| SDK-13 | 命令退出零但无结果、非法结果、非零退出 | 不以退出零推断业务成功，任务失败 | test_command_sdk.py |
| SDK-14 | 普通用户读取他人结果/产物 | 拒绝访问 | test_evaluation_framework.py、test_cs_protocol.py |
| SDK-15 | 多执行本地日志、Worker 恢复、已失效执行 | 保留各自身份，失效执行不再次启动，日志重传内容不变 | test_worker_recovery.py |
| SDK-16 | 无效集合中混入合法任务项、重复提交 | 无效集合不生成部分任务；重复提交按现有幂等规则处理 | test_cs_protocol.py |

上述文件位于 backend/tests/。每次验证以 pytest 实际结果为准，不把设计表等同通过记录。

## 新协议的待实现验收用例

以下需要功能完成后转为可执行测试，不使用跳过或预期失败掩盖尚未实现的能力。

| ID | 场景 | 必须检查的断言 | 关联模块 |
| --- | --- | --- | --- |
| NEXT-01 | 模板默认 → 集合取值 → 合法运行覆盖 | 输入类型正确，优先级固定，禁止覆盖字段拒绝，旧快照不变 | 模板、集合、SDK |
| NEXT-02 | 含空格、引号、布尔、数组/对象的业务参数 | argv 边界保持，不执行 shell；env/JSON 编码一致 | 解析器、Worker、业务程序 |
| NEXT-03 | 80 完成、10 跳过、10 未完成 | 按集合版本明确分母；各状态分别展示，跳过不自动算成功 | 协议、汇总、前端 |
| NEXT-04 | 记录已落盘后进程崩溃；API 已落库但响应丢失后 Worker 重启 | 恢复只续报，数据库只计一次；不误称恢复了仿真中途状态 | SDK、Worker、API |
| NEXT-05 | 取消恰好与回合完成竞争 | 保留已确认结果，终态规则确定；清理前资源不释放 | SDK、执行、调度 |
| NEXT-06 | 任务重试后旧尝试延迟上报 | 不污染新尝试；保存历史，拒绝无效租约 | API、重试、汇总 |
| NEXT-07 | 结果已报，视频上传超时/失败 | 结果和产物状态独立；UI不展示未完成产物为可用 | SDK、产物、前端 |
| NEXT-08 | 未知协议版本或缺少必需能力 | 启动前拒绝并说明能力差异，旧 Worker 不领取 | 预检、调度、Worker |
| NEXT-09 | 不接平台运行相同任务文件 | 本地与平台模式输入、种子、结果契约一致 | SDK、本地调试 |
| NEXT-10 | 两节点逻辑目录/设备映射不同，独占设备竞争 | 解析正确；无映射节点不准入；独占设备不重复分配 | 机器、调度、执行 |
| NEXT-11 | API断网、磁盘满、日志洪泛、产物过大 | 有界占用、可诊断失败；续租/取消通道不被阻塞 | SDK、存储、Worker |
| NEXT-12 | 多用户并发提交、重复事件并发抵达 | PostgreSQL 唯一性和事务正确，无丢更新/超卖/重复计数 | API、数据库、调度 |

## 分层执行与发布门槛

1. 纯协议测试：类型、事件状态、编码、边界值，无 GPU。
2. SDK 子进程测试：业务异常、退出码、事件落盘、终止及结果缺失。
3. API 集成测试：所有权、计划匹配、租约、幂等、汇总、重试隔离。
4. Worker 故障测试：网络超时、确认丢失、崩溃恢复、取消和清理确认。
5. PostgreSQL 并发测试：真实事务及唯一约束，至少两个提交/上报者同步竞争。
6. 两节点验收：Docker/host × SDK/直接协议，多集合排队、服务端中断、NFS 中断和节点恢复。破坏性故障仅在隔离测试目录/进程中注入，不停止生产 NFS 或推理服务。

修改共享契约时至少执行关联的 SDK、API、Worker 测试；提交报告明确测试环境、通过项、失败项及未覆盖项。新增能力未通过对应 NEXT 用例前，不标记完整上线。

## 本次执行记录

2026-11-13：新增 test_sdk_reporting_contract.py 的 16 个参数化测试场景；连同原有评测框架、命令 SDK、Worker 恢复和 CS 协议测试，共 46 项通过（55.72 秒）。存在一个依赖库弃用提示，不影响测试结果。

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  backend/tests/test_sdk_reporting_contract.py \
  backend/tests/test_evaluation_framework.py \
  backend/tests/test_command_sdk.py \
  backend/tests/test_worker_recovery.py \
  backend/tests/test_cs_protocol.py
```

本次仅新增测试和文档，未变更或部署业务实现；NEXT-01 至 NEXT-12 仍为待实现/待验收清单。
