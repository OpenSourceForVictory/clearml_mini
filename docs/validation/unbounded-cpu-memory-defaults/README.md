# CPU/内存按有效申请调度：验证记录

资源池正数 CPU/内存值改为未申请时的默认值；0 表示该轴未申请时不预约、也不设置 Docker/systemd 额度。模板或任务明确申请可超过资源池默认值，仍受节点配置容量、Agent 主机采样、平台已有预约和并发约束。服务端与客户端同机时按两端有效申请相加。

本次为本地隔离数据库、模拟节点样本及容器/systemd 命令构造测试，不代表真实业务执行或模型评测，也不代表生产节点已升级。CPU 主机样本含总逻辑核数和利用率，没有可直接预约的“空闲 CPU 核数”；内存样本含总量和 MemAvailable。

失败证据：首次扩展测试在收集期因 pytest 保留参数名 `request` 中断（1 error），已改名。其后 181 passed / 1 failed，新增 Agent 样本入库测试发现同一节点的配置容量与实测容量均不足时，调度原因重复返回 `node_cpu`；已在 `node_codes` 合并重复原因。后续定向复测 146 passed / 1 warning（Starlette/AnyIO 弃用提示）；初次大范围回归中其余 181 项通过。前端 TypeScript、Vite 生产构建、资源池表单 3 项及用户指南 3 项 Playwright 测试通过；OpenSpec 严格校验通过。

未运行全量覆盖率脚本，不能以定向测试通过数推断目录或关键功能覆盖率门槛。

## 全量回归后的测试契约修正（2026-11-18）

旧 `test_resource_budget_rejects_oversize_without_partial_jobs` 要求 CPU=10000 超过池默认值时拒绝，与已取消资源池单任务上限的规则冲突。原失败保留在 `docs/validation/host-container-full-20261118/README.md`，本次不恢复旧限制。

替换为批次资源正常/边界/失败路径：超过池默认值的 CPU/内存申请与合法最大值按原值保存；超过接口数值边界、显式零、虚拟池设备内存请求整批返回 400，不留下首项已创建的 Job。定向 7 passed，XML 为 `coverage-artifacts/case-resources-fix-20261118.xml`。节点容量及可用量准入继续由 `test_unlimited_cpu_memory.py` 等调度用例验证；接受提交不等于获得资源。无生产逻辑或界面变化。
