# 批次优先级全量测试后的条件跳过续查

本记录是 2026-11-20 批次优先级发布后全量覆盖复测的独立补跑。官方全量结果仍是 2331 通过、126 条件跳过、0 失败；以下结果不合并到全量 JUnit 或覆盖率，也不减少全量报告中的跳过数。

本机环境为 Docker 29.4、NVIDIA RTX 4090；当前会话没有可连接的 systemd user manager，也没有 `npu-smi`。所有数据库用例使用临时 PostgreSQL 容器及独立数据库，测试后容器已删除。

| 补跑范围 | 结果 |
|---|---:|
| Docker 通用命令矩阵，禁网、非 root、丢弃 capabilities、1 CPU / 256 MiB | 24 passed |
| 进程日志管线及服务日志生命周期，含 Docker 启动方式 | 89 passed |
| Worker 存储探测 Docker 镜像 | 1 passed |
| 模板示例镜像与隔离 loopback 服务 | 1 passed |
| PostgreSQL 批次控制、保留策略、endpoint、编排、资源迁移及 endpoint/retention 并发 | 7 passed |

Docker 命令矩阵使用 `GENERIC_DOCKER_TEST=1 GENERIC_DOCKER_IMAGE=mini-eval-api:root-managed-20261117`。日志测试使用 `PROCESS_LOG_TEST_IMAGE=mini-eval-runner:dev`。存储和模板镜像分别使用 `STORAGE_PROBE_TEST_IMAGE=mini-eval-storage-check:1` 与 `RUN_TEMPLATE_DOCKER_TEST=1`。PostgreSQL 补跑通过临时容器创建独立迁移库与 race 库，并在 race 库上先运行 Alembic head；`PYTHONPATH` 指向仓库根目录，避免 Alembic 子进程导入失败。

首次 PostgreSQL 尝试有 5 项失败、2 项通过，未作为通过证据：endpoint 迁移用例要求被拒绝的降级后版本保持旧硬编码值 `i952d34`，但该分支当前有效 head 已更新；编排/资源迁移子进程没有仓库 `PYTHONPATH`；两个 race 数据库缺少已迁移 schema。修正 endpoint 测试为比较拒绝降级前后的 revision，并以正确环境重跑后，7 项全部通过。迁移 revision 修正仅改测试断言，没有改迁移行为。

完整日志保存在本机忽略目录 `.runtime/batch-priority-skip-recheck-20261120/postgres-rerun.log`；首次失败细节已记录在本文件，临时数据库容器已清理。

仍未在这次续查中启用的路径：需要 systemd user manager 的 host 单元/委托 systemd 测试、必须 root 且预置专用业务身份的真实纳管测试，以及特权 mount namespace 测试。当前会话不具备前两项可安全使用的环境；没有用模拟测试替代这些系统验收。既有归档记录中独立 systemd、onboarding Compose、mount namespace 和多逻辑节点测试结果保持各自边界，不等同于本次全量覆盖统计。
