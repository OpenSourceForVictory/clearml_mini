# 宿主机启动失败恢复验证（2026-11-18）

已复现：创建异常后失败完成接口不可用，旧实现恢复时把原错误改为 missing unit。失败证据见 before.log。修复在停止/上报前保存首次失败请求，恢复重报，不重复创建；提前记录的 created/started 是尝试标记，保留防重复保护。不能由此断言现场两卡第二实例首次失败的根因。

## 专项结果

- tests.log：136 passed、3 skipped；覆盖执行恢复、启动续租、宿主机后端、安装与编排。未跑全量。
- extra-tests.log：11 passed（与前者重叠），补充失败退出码持久化。不可相加为独立用例总数。
- test_host_failure_recovery：503、丢失完成响应、停止失败、重新加载 journal、旧 missing 状态来源、host/Docker、不重复创建；两卡两个 Worker 相同服务名称，共用 workspace 时仍独立控制目录，清理不交叉。
- test_service_instance_claim_identity：真实测试 API 与隔离数据库，同名服务两个实例 ID、执行 ID、Job、端口不同；该 API 用例为同卡共享，不冒充两张物理卡。
- two-card-workers.json：本机真实用户 systemd，两个 Worker 对象分别配置卡编号 0/1、独立 journal，两个监听服务同时运行、退出码 0、清理后恢复为空；日志协议 1/2 均验证。没有调用物理 GPU/NPU，控制 API 为 fixture，不是模型验收；服务进程为独立 systemd unit，Worker 对象处于同一测试进程。
- system-two-services.json：此前同一卡 Worker 的真实 root/system systemd 双服务结果，作为既有边界证据，不等同本次两张物理卡测试。

## 覆盖率与发布边界

使用 scripts.test_coverage 的 project.coveragerc 与 scripts.coverage_report 同一分母和门槛，只收集本次专项并合并同版本数据；完整结果见 coverage.md。专项总行 32.96%、分支 18.03%，全项目覆盖率门槛未达到；未合并旧代码结果，未排除未测业务代码。真实 systemd 子进程和前端未插桩，不计入覆盖率。3 项跳过来自 test_host_backend 的真实 systemd opt-in（HOST_EXECUTION_TEST 未开启）；另行运行的 systemd 验证范围见上文，不替代全部跳过用例。本次没有真实 Docker 验收。

Worker 0.21.1；API claim 仅新增可选实例身份，无数据库迁移。尚未发布或更新现场节点。同步 docs/host-execution.md、界面执行指南和 Skill project-map。未知原错误仍需现场最早启动日志确认，禁止清空 journal 强制重复启动。

后续按用户要求执行的全量及未达标项见 [2026-11-19 全量记录](../host-recovery-full-20261119/README.md)。
