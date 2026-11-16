# Proposal

## Why

运行模板尚不能声明 CPU、内存及加速设备资源要求，用户需要在提交时重复配置。现有任务层 GPU 资源字段及 NPU 遥测不能代替模板约束和可验证的 GPU/NPU 执行能力。

## What Changes

- 客户端与服务端模板统一声明 CPU 核数、内存 GiB、加速设备类型 GPU/NPU、型号约束、设备内存 GiB 及共享/独占模式。
- 区分最低要求、默认申请与允许覆盖字段；提交时展示有效申请及来源，低于下限或覆盖锁定字段即拒绝。
- 贯通单任务、集合、编排和独立服务部署入口的解析、预检、持久化快照与调度账本。
- GPU/NPU 由 Agent 上报类型与能力，Worker 显式声明执行后端；未就绪的执行组合明确阻止准入，不根据探测到设备推断可执行。
- NPU 后端必须验证实际设备分配及清理；共享模式按资源预留准入，不宣称显存硬隔离或严格算力限额。

## Capabilities

### New Capabilities
- `runtime-resource-contract`: 模板到执行的一致资源约束、覆盖策略、异构设备能力匹配与可解释预检。

### Modified Capabilities
无。主规格 platform 仅描述旧 GPU 槽位行为；该增量补充统一契约并保留旧任务兼容，不直接改写既有槽位身份。

## Impact

涉及 TemplateCreate/ServerCreate、template_versions、job_resources、batches、orchestrations、services、单任务入口、调度器、设备采样及 Worker 后端、运行模板与提交向导界面。需要兼容数据迁移和跨模块测试；不能只通过前端默认值实现。与 template-authoring-guidance 独立提交和发布，保留当前机群稳定性验收。
