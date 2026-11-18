# Spec Delta

## Purpose

为可信宿主机业务提供与已批准运行账号登录机器后相近的进程环境，同时保持平台控制变量和设备分配不可被业务配置覆盖。

## ADDED Requirements

### Requirement: 宿主机登录环境可选
系统 SHALL 允许宿主机模板选择在启动时读取已批准业务账号的登录 shell 环境；未选择的既有模板 SHALL 保持当前显式环境行为。

#### Scenario: 使用业务账号环境
- **WHEN** 模板启用登录环境且业务账号登录 shell 导出 PATH、PYTHONPATH 或自定义变量
- **THEN** 业务程序以该账号启动，并收到这些变量、模板变量和任务允许覆盖的变量

#### Scenario: 登录环境读取失败
- **WHEN** 登录 shell 不能执行、超时或输出无效环境
- **THEN** 启动失败并给出不包含环境值的诊断，不以 Worker/root 环境代替

### Requirement: 平台保留变量最终生效
系统 SHALL 在登录环境之后设置任务输入、目录和平台分配的 GPU/NPU 可见性；登录环境和模板 SHALL 不能改变最终设备分配。

#### Scenario: 登录脚本设置错误设备
- **WHEN** 登录 shell 导出与调度分配不同的 CUDA_VISIBLE_DEVICES 或 ASCEND_RT_VISIBLE_DEVICES
- **THEN** 业务程序只收到平台分配的设备可见性

#### Scenario: Docker 模板
- **WHEN** 模板使用 Docker 执行
- **THEN** 不读取宿主机登录环境，镜像环境行为保持不变

### Requirement: 节点组件升级范围可见
升级界面 SHALL 明确 Worker、Runner 和 Agent 的更新范围，不把节点程序升级描述成业务镜像或驱动升级。

#### Scenario: Runner 更新说明
- **WHEN** 用户查看节点版本升级
- **THEN** 页面说明宿主机 Runner 和新容器平台适配器随节点包更新，旧镜像内置 Runner 需另行重建；Agent 仅在已配置托管时一并更新

### Requirement: 登录环境读取时限可配置
系统 SHALL 默认允许 60 秒读取登录环境，节点管理员可配置整数 1–300 秒；超时诊断包含业务账号和实际时限，不暴露环境值或 Shell 输出。
#### Scenario: 慢登录环境
- **WHEN** 未配置自定义时限且登录环境读取耗时超过 10 秒但少于 60 秒
- **THEN** 允许读取完成，不因旧 10 秒限制拒绝启动
#### Scenario: 自定义时限与校验
- **WHEN** 节点配置有效时限或配置越界、非整数值
- **THEN** 有效值控制读取期限，无效值在启动 Shell 前明确拒绝
#### Scenario: 超时诊断
- **WHEN** 登录读取超过配置时限
- **THEN** 报告登录环境读取超时、账号和时限，不误称业务程序执行超时，平台启动和租约保护继续有效
