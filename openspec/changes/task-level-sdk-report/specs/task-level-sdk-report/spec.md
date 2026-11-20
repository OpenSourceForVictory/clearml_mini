# Task-Level SDK Result Reporting

## ADDED Requirements

### Requirement: 可选任务级业务结果
系统 SHALL 支持模板选择 `sdk-task-result`，让普通整任务业务程序一次上报任务级结果。默认模板行为保持不变，既有逐回合与直接协议行为 SHALL 不变。

#### Scenario: 任务级结果被接收
- **WHEN** 已启用任务级结果的业务程序调用 SDK 上报一个有效最终结果
- **THEN** Worker 将结果绑定到当前执行尝试转发，平台保存业务成功标记、有限数值指标及可选摘要

#### Scenario: 任务级结果与进程状态分离
- **WHEN** 业务程序以退出码 0 结束并上报 `success=false`
- **THEN** 平台分别显示程序正常退出和业务结果失败，不将两者互相覆盖

#### Scenario: 结果缺失
- **WHEN** 启用任务级结果的业务进程成功退出但未上报最终结果
- **THEN** 平台按退出码记录程序执行状态，业务结果显示未上报且不得推断业务成功

### Requirement: 执行隔离与安全转发
系统 SHALL 将业务结果限制在报告它的 execution ID，且业务程序 SHALL NOT 获得 Worker/API 凭据。

#### Scenario: Worker 并行执行多个任务
- **WHEN** 同一 Worker 同时运行两个启用任务级上报的执行
- **THEN** 每个报告只写入其执行尝试，Worker 使用各自 journal/state 转发，不串报游标或结果

#### Scenario: 结果响应丢失后重试
- **WHEN** Worker 无法确认平台是否已保存结果并重试相同事件
- **THEN** 相同结果幂等成功；不同结果对同一执行尝试被拒绝并可诊断

### Requirement: 结果负载校验
平台 SHALL 校验结果结构、类型、指标数量与大小上限，拒绝非有限数字、越权执行和不支持的结果模式。

#### Scenario: 非法指标或越权执行
- **WHEN** 业务结果包含布尔值冒充数字、NaN/Infinity、超限字段或执行 ID 不属于该 Worker
- **THEN** 平台拒绝该报告且不写入结果
