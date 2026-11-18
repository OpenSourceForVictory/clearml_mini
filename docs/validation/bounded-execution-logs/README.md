# 有界执行日志验证（2026-11-18）

本变更尚未发布到平台或已纳管节点。验证使用当前工作树，OpenSpec 为 bounded-execution-logs。

## 测试边界与真实运行

`live-matrix.json`：本机真实 systemd 用户服务与 Docker，分别执行普通命令、直接协议、SDK 两回合和服务端，共 8 个场景。业务每次输出 1 MB；SDK 校验四个事件与两个回合结果。`docker-rotation.json` 增加四个 Docker 场景，每次输出 4 MB，超过 Docker 单文件 1 MB 的轮转配置。最终源代码又以每次 4 MB 输出重跑宿主机和 Docker 共 8 项（final-live-matrix.json）。所有场景验证尾部错误、退出码、完成请求与持续心跳。服务端按测试设计自行退出，退出码 0、状态 FAILED 是常驻服务的现有语义。

这是 CPU 诊断程序、真实启动器和回环 HTTP 控制接口桩的集成验证；不是线上平台端到端验收，也不是 GPU/NPU 模型质量评测。控制接口未连接平台数据库。未插桩的 systemd/Docker 子进程不计入 Python 覆盖率。

复现脚本 `live_probe.py` 需要仓库根目录 PYTHONPATH、可用 systemd 用户总线、Docker 和脚本指定的本地镜像。它只创建和删除自己随机 UUID 命名的执行。执行前先创建 `.runtime/bounded-logs-live`；以 `PYTHONPATH=. .venv/bin/python docs/validation/bounded-execution-logs/live_probe.py` 运行，使用 `LOG_LIVE_MODES=docker LOG_LIVE_LARGE=1` 验证大输出轮转。运行目录含启动环境，不能提交或公开。

失败证据保留在 initial-host-failure.txt、initial-docker-rotation-failure.txt：前者暴露 stdout 缓冲挤出 stderr 尾部，后者暴露 Docker 接入前日志已轮转。现分别按流保留退出尾部，并在 Runner 保留 7 KiB stderr 快照；新增针对两种情况的回归测试。快照可能重复显示，缺口不会伪装为完整日志。

## API 与迁移

postgres-tests.txt：实际一次性 PostgreSQL 数据库 4 项通过，涵盖迁移/回退保留旧日志、并发重试、完成、删除，以及全局调度锁被其他连接占有时 v2 仍可追加。未改动平台业务数据库。

## 固定负载测量

api-benchmark.json：同样 16 KiB 正文，旧接口 8 请求、64 SQL、8 次全局锁；v2 合并为 1 请求、6 SQL、0 次全局锁。单次本机样本分别 84.82 ms / 13.2 ms，不是线上吞吐量承诺。锁等待边界通过独立连接持有全局锁的测试验证。

node-benchmark.json：真实 supervisor 捕获约 16 MiB 输出。v1/v2 诊断磁盘约 256 KiB / 537 KiB；v2 保留滚动尾部。最大 RSS 包含业务子进程一次性分配的字符串，不能当作采集器独占内存。v2 耗时约 0.70 秒，旧版约 0.09 秒；有界滚动文件增加本机 I/O，不能仅凭请求减少声称所有方面均更快。复现 node_benchmark.py 需预建 `.runtime/bounded-logs-benchmark`。

## 覆盖率

以 scripts.test_coverage 和 docs/code-coverage.md 的源文件分母统计；新采集、发送和 API 模块纳入对应目录及关键业务执行功能。前端 Playwright 通过数不代表前端覆盖率。最终覆盖率与全量结果补充在本目录，不调整原门槛。

## 最终回归与门槛说明

全量通过 `python -m scripts.test_coverage` 执行：1600 通过、132 跳过、1 失败（full-tests-initial.txt）。失败是 SDK 下载包未重新生成；已重新构建，独立验证 1 项通过，最终相关回归也包含该测试。未将原失败记录覆盖成“全量通过”。跳过多为需要显式启用的实机、外部依赖测试，从全量 JUnit 提取的原因见 skipped-tests.json。

在高输出实测修正后，最终相关 Python 回归 267 通过、4 跳过（final-recheck.txt），并补充事件游标/失败重试/鉴权停止及异常退出缺口测试；前端日志与服务实例 5 项、批次监控与历史 13 项通过。TypeScript 检查通过；构建保留原有大于 500 KB 包体提示。

覆盖率保留全量原报告 initial-coverage.md。由于全量执行期间对 runner/log_capture.py、worker/log_stream.py、worker/client.py 做了实测修正，最终合并前先清除这三个文件的旧行/分支数据，再使用最终相关测试数据补入；其余文件源代码未变，保留全量覆盖。最终 summary 仍使用仓库完整 source 分母与原门槛，不排除业务文件。不合并未插桩的 Docker/systemd 子进程覆盖率。此证据是“全量检查 + 最终变更专项复核”，不是第二遍完全重跑全量。

异常提前结束时不等待日志补传，但完成请求必须标记缺口。exception-regression-before.txt 保留取消分支漏标记的回归失败，修正后的结果另存 exception-regression-after.txt。

同一 Worker 并发两个宿主机服务实例的补充实测见 two-host-services.json：v1/v2 各两个实例同时运行，unit 独立，完成后 Worker journal 恢复正常。该测试使用回环控制接口桩，未覆盖用户现场 API 调度与节点配置，不能据此宣称已修复现场“第二实例 missing unit”。日志 journal 使用 .logs 后缀，避免多执行恢复扫描将辅助日志状态误作 execution JSON。

最终覆盖率见 final-coverage.md：仍未达到 scripts 行 80%、业务执行与结果行 90% / 分支 80% 门槛，保留失败，不降低标准。异常退出专项 50 项、最后恢复专项 28 项通过；这些与前述专项有重叠，不能简单相加作为唯一测试数。
