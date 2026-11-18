## ADDED Requirements
### Requirement: 可选总执行时限
系统 SHALL 支持客户端 timeout_seconds=0，跳过总时限但保留租约和启动期限。
#### Scenario: 无限时长
- **WHEN** 用户选不限总执行时长且 Worker 支持协议16
- **THEN** 宿主机和Docker不附加总执行截止时间，任务仍可取消
#### Scenario: 旧Worker
- **WHEN** 不限任务遇到协议15及更早Worker
- **THEN** 拒绝准入并提示升级，不下发可能立即超时的任务
### Requirement: 预约最低资源但不限制运行上限
系统 SHALL 支持cpu_unlimited和memory_unlimited，仍按cpus/ram_gb正数最低申请预约。
#### Scenario: 内存不限仍需要最低容量
- **WHEN** 任务要求最低8GiB且选择不限制内存上限
- **THEN** 调度预约8GiB，节点容量不足则等待，启动不设置MemoryMax或Docker内存上限
#### Scenario: 无模板最低值
- **WHEN** 用户在无资源模板时选择不限
- **THEN** 必须提供正数最低申请量，不得按0预约
#### Scenario: 模板约束仍有效
- **WHEN** 用户申请低于模板最低量或修改锁定字段
- **THEN** 拒绝请求，不能用不限绕过约束
