## Purpose

为客户端和服务端的运行模板提供可复用、可校验的 CPU、内存与加速设备资源契约，使不同提交入口、预检、调度和执行使用同一有效申请，并明确异构硬件的能力边界及资源占用语义。

## ADDED Requirements

### Requirement: Template resource declaration
模板 SHALL 声明 CPU、内存及 GPU/NPU 类型、设备内存、型号与共享/独占要求，区分最低要求、默认值和允许覆盖字段；注册、复制和只读查看 MUST 保留同一语义。

#### Scenario: Invalid template defaults
- **WHEN** 默认申请低于最低要求，或共享模式无正设备内存申请
- **THEN** 注册被拒绝并指出字段，不创建无效版本

### Requirement: Consistent effective resource resolution
所有提交和预检入口 SHALL 使用同一解析规则，依次应用模板默认、提交和单项显式取值，验证覆盖权限及下限并保存有效快照；旧模板 MUST 保持兼容。

#### Scenario: Locked field override
- **WHEN** 提交尝试修改模板锁定的设备类型或低于 CPU/内存下限
- **THEN** 预检和提交都拒绝相同输入，返回字段原因，不能通过另一个入口绕过

#### Scenario: Retry and restart
- **WHEN** 任务重试或服务重启，原模板已经有新版本
- **THEN** 使用原已保存的有效资源快照，不静默改用新模板默认值

### Requirement: Capability-aware accelerator allocation
平台 SHALL 根据设备类型、稳定身份、执行后端能力和采样时效匹配 GPU/NPU；硬件探测成功 MUST 不等价于执行能力就绪。

#### Scenario: NPU without execution backend
- **WHEN** 节点上报 NPU 但缺少相应执行或共享能力
- **THEN** 不领取该任务，预检列明不满足的能力，不退化为 GPU 执行

### Requirement: Resource accounting and exclusivity
平台 SHALL 按节点 CPU/RAM 和设备内存预留进行准入，设备独占与共享互斥；执行停止但清理未确认时 MUST 保持占用。服务实例共享与设备共享 SHALL 分别配置。

#### Scenario: Shared device capacity exhausted
- **WHEN** 新任务的共享内存申请加有效预留超出设备可用预算
- **THEN** 有效任务排队并显示资源不足，释放确认后推进，不以瞬时低利用率绕过账本

#### Scenario: Exclusive task arrives
- **WHEN** 同一设备上有未清理的共享执行，另一个任务请求独占
- **THEN** 独占任务不能领取该设备，也不能通过更换客户端/服务端角色绕过互斥
