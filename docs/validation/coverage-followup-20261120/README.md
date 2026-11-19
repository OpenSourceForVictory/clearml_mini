# 覆盖率补测与近期待办核对（2026-11-20）

本轮按用户要求先补测试，再处理近期未完成项，最后在最终代码上执行全量测试。不得把此前定向测试集的全仓低覆盖率解释为现有全部测试的覆盖率。

## 新增测试

- service_log_compatibility：同一服务重启的两次执行，当前及历史日志分别读取，历史列表与不存在实例 404。
- worker_service_execution：探测会话等待期间真实启动续租线程继续工作，取消、过期租约和恢复分支不创建第二个业务进程。
- execution_locations：外执行、外槽位的库存目录不能混入本尝试；不存在尝试 404。
- 浏览器 execution-location：旧尝试响应迟到不能覆盖当前选择，非法目录不提供复制按钮。
- integration_bundle：将 SDK 解压到仓库外，运行真实子进程并验证独立日志文件及关闭索引，不只验证导入。

## 已保留的失败与修正

1. 完整基线发现 test_freshness 的 SimpleNamespace 缺少 connection/storage_config/id，在存储继承检查中报 AttributeError，尚未进入时效断言。补全夹具字段；不放宽原过期、未来时间、revision 和在线窗口断言。独立复测 2 passed。
2. test_integration_bundle 检出 ZIP 与当前源码不一致。重新生成后普通自测通过，但新增日志测试复现 ModuleNotFoundError: runner.process_log_store。构建器增加四个 process_log_* 依赖并重新生成 ZIP，仓库外运行验证通过。需要发布前端后用户下载才更新；不声称修改工作区即已上线。
3. 初次完整基线期间继续增加测试和修复上述缺口；该轮仅为失败及缺口发现，不作为最终固定版本的全量验收。

前端没有统一插桩，业务子进程未合并覆盖数据；不得把其通过数换算为 Python 覆盖率。生产数据库不参与；本轮 PostgreSQL 是仅监听 loopback 随机端口的一次性 Docker 容器。统计命令保持 scripts.test_coverage 与原门槛。

## 日志补齐与专项验证

- 手动部署的服务管理页接入按执行历史选择的独立日志抽屉，节点/设备及缓存来源明确显示。下载采用固定结束序号，每份最多 96 页/8 MiB，显式继续下一份；失败不生成不完整文件冒充成功。
- 机器数据保留增加日志预算；API 保存期望版本，Worker 0.21.11 每 30 秒同步并持久化，逐卡确认，失败保留上次配置。首次领取按已确认版本冻结到 execution_log_policies；现有执行不随降额改变。整机总量仍不限制，不启用配额通道。
- 修复普通心跳覆盖软件信息时丢失日志策略回执的风险，增加正常/非法/版本冲突/失败保留/心跳保留/重启读取/执行冻结/同步失败仍继续历史读取测试。
- 真实独立 PostgreSQL 专项 12 passed（配置、迁移、离线与慢节点负载）；补充同步节流后配置专项 4 passed。迁移从旧表升级、批次删除外键顺序通过。
- 旧 schema a730d12 的 custom-format pg_dump 恢复到另一临时数据库，再 upgrade head 到 b841e23；历史 RunLog 正文一致、新快照可写，临时数据库和备份已删除。证据 restore.txt。这不是生产数据库恢复演练。
- 前端首轮完整回归 194 passed / 1 failed / 10 skipped：image-runtime 旧用例未展开新向导的高级 JSON 区域，等待 45 秒超时。修正为先展开再填写，保留原提交 payload 断言。新增下载失败用例初次使用错误按钮文字，已对齐“任务公共日志”；均保留失败说明，不归咎于环境。
- OpenSpec 全仓检查 86 passed / 5 failed：host-device-environment-boundary、host-finished-unit-recovery、host-gpu-assignment-verification 的历史 delta 标题不符合 CLI 格式；host-inherit-task-limits、host-login-environment 缺 delta。未删除历史空目录或伪造规范。当前变更单独 strict 结果另记。

本轮未部署生产 API，也未升级真实节点。已有 GPU/NPU 日志真机记录验证此前版本，不能替代 0.21.11 新日志预算的现场升级验收；NPU 无产物/NFS 的用户决策保持不变。

慢节点证据见 slow-node-metrics.json：128 请求、20 秒节点延迟、两连接池，38 次控制检查，control P95 约 21.6 ms，结束连接占用 0；缓存 32,024 字节、测试数据库约 9.86 MB、测试进程峰值 RSS 167,792 KiB。测量期间未触发生产任务，不将单机 API/模拟节点结果称作生产性能 SLA。

配置界面另修正保存后的短暂旧轮询响应：只有平台版本高于草稿才判定冲突，避免成功保存立刻显示冲突。界面测试保留该断言。

OpenSpec 后续整理：将三个已提交历史规范的 `## Requirements` 修正为 CLI 要求的 `## ADDED Requirements`，保留所有原条款和场景。全仓复核变为 89 passed / 2 failed；剩余 host-inherit-task-limits、host-login-environment 是当前工作区未提交的空草稿目录，未删除或编造其规范。本次三个关联变更 strict 均通过。

前端第二轮 195 passed / 1 failed / 10 skipped：日志预算用例等待冲突提示时，开发服务热更新导致页面回到默认列表；本轮期间发生过源文件修改，不能算固定版本验收。保留失败后冻结代码，重新运行完整前端套件；不通过增大断言超时来掩盖。

## 最终前端结果

冻结前端源码后完整 Playwright：**197 passed / 10 skipped / 0 failed，3.3 分钟**（包含后补的任务详情目录用例；此前稳定轮为 196/10/0）。跳过项是未配置真实平台凭据的 live 测试，不能宣称本轮新功能已真机上线。类型检查通过，Vite 生产构建通过；保留现有 >500 kB bundle 提示，未通过调高阈值消除。此结果不提供前端行/分支覆盖率。

执行目录待办补齐：3 项浏览器测试覆盖批次尝试切换、复制成功/失败、迟到响应、任务详情旧 API 404 与恢复、未分配与未上报。4 项 Python 测试覆盖目录与身份契约、历史根、多根、非法路径、未清理状态、跨用户/任务/执行/槽位及不存在尝试。沿用既有指南，目录查询本身不增加表；本轮日志预算迁移另计。增加这一用例后再次运行完整前端套件，不将专项数量当全量数量。

## 最终 Python 全量与覆盖率

固定 Python 源码执行 `python -m scripts.test_coverage --output coverage-artifacts/coverage-final-20261120`，在一次性 PostgreSQL 开启 TEST_DATABASE_URL、LOG_TEST_DATABASE_URL、MIGRATION_DRIVER_TEST_URL；实际 Docker 日志矩阵使用 mini-eval-cs-test:1，存储探测使用 mini-eval-storage-check:1。

**2218 passed / 101 skipped / 0 failed，27 分 50 秒，pytest_exit_code=0。** 六目录与全部关键功能组门槛通过：整体行 91.73%、分支 82.58%；调度与资源 97.65%/88.54%，纳管升级 95.67%/83.65%，业务执行结果 96.12%/88.72%，scripts 80.81%/78.20%。逐目录与全部分组见 final-summary.md；跳过原因计数见 python-skips.json。未降低阈值、未排除业务代码、未拼接此前不同源码版本的覆盖数据。

这些统计只覆盖 Python 父进程插桩范围；真实子进程、Docker 和前端的通过数不折算为行/分支覆盖率。未开启的 root/systemd、专门镜像与 live 环境测试保持跳过，不能称全部硬件/部署组合已验收。完整原始 JSON/XML/HTML 在本地 coverage-artifacts/coverage-final-20261120（Git 忽略），仓库保留摘要和失败/跳过说明。
