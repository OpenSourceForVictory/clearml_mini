# Spec Delta

## Purpose

为多节点评测平台提供可核验、可中止且保留任务历史的发布流程，使管理员能够辨别实际部署版本、升级门禁和验收结果，在升级失败时保留可恢复状态，而不将测试环境成功误认为整个机群已经可用。

## ADDED Requirements

### Requirement: Explicit release inventory
发布工具 SHALL 校验显式目标清单，并输出脱敏的节点身份、组件版本、协议、数据库版本与存储配置版本；只读检查 MUST 不改变运行状态。

#### Scenario: Unexpected target
- **WHEN** 指定主机不在发布白名单内或远端身份不符
- **THEN** 拒绝对该主机执行变更，报告原因，不自动扩大发布范围

#### Scenario: Incomplete inventory
- **WHEN** 一个节点不可达或组件版本无法核实
- **THEN** 将该项记录为未知并阻止机群验收通过，不以旧文档版本填充实时结果

### Requirement: Drain and compatibility gates
发布工具 SHALL 在替换组件前停止目标新准入并等待活动执行和待确认清理归零，检查组件兼容性，保留已有节点身份、目录和执行记录。

#### Scenario: Drain timeout
- **WHEN** 排空在指定期限内未完成
- **THEN** 中止该目标升级，不强杀用户任务，不删除 journal，不继续后续节点

#### Scenario: Old worker
- **WHEN** 发布后的任务要求协议 10 而节点尚不支持
- **THEN** 该节点不得领取不兼容任务，发布报告列明未就绪状态

### Requirement: Recoverable rollout
发布流程 SHALL 在迁移前验证备份可恢复，保留既有访问入口，并逐节点检查心跳、配置回执与执行能力；失败后 MUST 保留诊断和可恢复状态。

#### Scenario: Failed migration or node validation
- **WHEN** 数据库迁移或任一节点升级后检查失败
- **THEN** 停止后续发布，不宣告成功，提供与当前数据版本相容的恢复步骤

#### Scenario: New data after release
- **WHEN** 新版本已产生任务或结果后需要回退
- **THEN** 不自动覆盖旧数据库备份，不删除新记录来强制降级

### Requirement: Scoped acceptance evidence
验收工具 SHALL 记录实际主机、任务、尝试、服务地址、时间和清理结果，区分通过、失败、跳过与未验证；故障注入 MUST 限定为显式登记的测试资源。

#### Scenario: Cross host execution
- **WHEN** 验收声称客户端与服务端跨物理机运行
- **THEN** 证据包含不同节点身份、实际服务访问和业务参数回显，而非仅模拟槽位分配

#### Scenario: Interrupted test
- **WHEN** 验收进程中止后再次运行
- **THEN** 能凭测试标识定位并对账遗留执行，停止其自有测试资源并确认清理，不影响其他用户任务

#### Scenario: Missing environment
- **WHEN** 缺少宿主机环境、镜像或真实 GPU 验证条件
- **THEN** 对应场景记录未验证及原因，不能计为验收通过
