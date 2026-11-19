# 可选业务 PID 上限回归（2026-11-19）

触发条件：旧 Worker 宿主机 TasksMax 与 Docker --pids-limit 都固定256，宿主机两处校验还各自默认256；模型/数据加载线程可能耗尽额度，手工仅修改启动值又可能触发校验失败。不可访问现场的实际错误原因尚未确认。

修复：Worker0.21.3读取每卡JSON顶层 execution_pids_limit，缺省/null继承运行时默认值，正整数按每个业务实例设置。宿主机启动快照、systemd参数、Worker及supervisor校验统一，Docker共用配置解析器；探测32保持不变。协议17，无迁移。

## 证据

- before.log：新专项在旧代码上22失败、13通过，包括固定256与未传播配置的失败。
- after.log：专项、资源校验、宿主机生命周期及Docker契约117通过。
- collection-error.log：一次回归命令引用不存在的test_npu_host.py，收集失败；修正为test_npu_cann_environment.py后重跑。
- regression.log：10个相关测试文件187通过、8跳过、1条Starlette依赖弃用警告。117已包含在187内，不相加。
- 8个跳过为3个真实systemd用户服务测试及5个需要root/systemd/预置账号的managed host测试。本次使用临时文件和模拟systemd/Docker命令验证；未部署节点、未做GPU/NPU真实模型及实际运行时线程压力验收。
- 前端tsc --noEmit通过；OpenSpec strict校验通过。

## 覆盖率

采用scripts.test_coverage相同project.coveragerc、全源码分母和scripts.coverage_report门槛，运行上述定向测试，不是全量测试。全项目行28.54%、分支13.62%，门槛未达；完整目录/功能结果见coverage.md。resource_limits.py行93.33%/分支84.78%，host_backend.py行95.67%/分支86.79%，client.py行52.53%/分支43.71%，单文件数据不能代替目录门槛。未插桩前端及独立进程不计入覆盖声明。

新测试覆盖缺省/显式、非法类型、cgroup v1/v2、上限超出或max的拒绝、缺省时无pids控制器、create/start配置变动快照稳定性、旧launch兼容、supervisor参数传播和Docker参数。现有回归覆盖身份、NPU环境、启动失败及升级打包。
