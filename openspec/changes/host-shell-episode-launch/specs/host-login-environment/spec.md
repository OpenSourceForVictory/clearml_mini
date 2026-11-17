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
