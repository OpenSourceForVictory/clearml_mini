# 批次、任务与独立回合耗时验证

**最终验证：Python 2430 通过、126 跳过；前端 246 通过、10 跳过；全部 Python 覆盖率门槛通过。**

变更：`execution-duration-display`。仅增加诊断与展示，不修改任务时限、续租、调度或退出/业务结果判据。批次只有整体历时，没有累计执行耗时。

## 真实执行范围

在本机隔离 API `127.0.0.1:18090`、独立 PostgreSQL 数据库和专用 Worker 上执行；没有更新生产 API 或现有纳管节点。设备为 virtual，宿主机使用真实 systemd 用户单元，容器使用真实 Docker。业务是可运行小样例，**不是 GPU/NPU 算力验证或真实模型/RoboTwin 评测**。

| 模式 | 结果 | 计时核对 |
| --- | --- | --- |
| host 普通命令 / whole_task | 成功 | 仅整任务时间，无回合时间列 |
| host 普通命令 / per_episode | 两回合成功 | 每回合自动单调计时 |
| host direct / per_episode | 两回合成功 | 整任务与回合都有记录 |
| host direct / 缺 final / continue | 任务失败，两回合均尝试 | 每回合退出 0、协议错误、耗时仍存在 |
| host sdk-command | 两回合成功 | 按实际独立子进程计时 |
| Docker sdk-command / 平台注入 Runner | 两回合成功 | 容器内独立回合计时 |
| Docker direct / whole_task | 成功 | 保留整任务时间，不伪造回合进程时长 |
| Docker 历史自带 Runner，无 runtime 注入 | 成功 | 镜像内没有 episode_timing 模块，兼容回退正常 |
| 原失败任务重新评测 | 新尝试失败 | 新执行 ID，原耗时保持不变 |
| 运行中取消 | CANCELLED | 回合中断、任务时间冻结 |
| supervisor 超时 | FAILED（原有退出状态） | 回合中断、任务时间冻结，不因新增计时改变任务状态 |

数据见 [实测时间](live-results.json)、[重试/中断/遗留镜像](live-recovery.json)。所有记录来自隔离环境，ID 不属于生产平台。七个真实批次均用浏览器打开详情并检查回合时间列是否与实际启动模式匹配；另查看窄屏。没有复制令牌、Worker 配置或完整运行环境。

## 自动化断言

- `test_execution_timing.py`：等待/执行分离、排队取消、启动失败、缺时间、异常/负墙钟、并行不累加、重试起点、旧 runtime 与中断回退。
- `test_execution_timing_migration.py`：真实 PostgreSQL 升级/降级、旧写入、无效索引重建和幂等重试；EXPLAIN 关闭顺序扫描以确认新增索引路径可用；1001 个尝试使用 3 次聚合查询。
- `test_timing_api.py`：同一响应统一 as_of、任务列表整页查询、批次/详情/CSV 一致、空批次、旧尝试、缺时间、权限、幂等、私有接收时间不泄漏；可选能力有/无时 claim 差异。
- `test_episode_timing_contract.py`、`test_command_sdk.py`：时间结构、UTC、有限范围、单调计时与墙钟回拨；真实 direct/SDK 子进程成功、非零、缺/多/坏 final、结果文件缺失/格式错误、准备异常、早停/继续、日志关闭；native SDK/whole_task 不启用独立进程计时。
- `test_timing_recovery.py`：升级恢复沿用原开关（含旧快照缺字段），断连/重启重放原始事件，不重置起点、不新增业务启动。
- `test_worker_docker_contracts.py`：协商开关进入容器实际 input；既有宿主机/日志/恢复回归检查接线不影响旧执行。
- `test_integration_bundle.py`：下载 SDK 与源码一致，可脱离仓库运行。
- `execution-timing.spec.ts`：0/null/日级格式、不使用浏览器墙钟、6 秒过期冻结/恢复/终态停止、切换模式保留协议结果、200 行共用一个 tick，卸载释放。
- 既有批次监控浏览器测试覆盖历史选择、迟到响应、窄屏横向滚动和回合展开。

## 失败及验证边界

保留首次失败，不以最终通过覆盖证据（提交日志仅去除行末空白）：

1. 新增浏览器组件测试初次直接导入 Vite 预构建 CommonJS 包，取错 createRoot 导出；改为 default 导出。随后独立挂载缺登录会话、应用自动跳转影响测试，改为独立 HTML 宿主页与模拟会话。属测试夹具问题，日志 `timing-frontend*.log` 保留，最终 3 项通过。
2. 新增 Worker 重放测试重复 mkdir 已由 execution_dirs 创建的目录，改为 exist_ok=True；第一次 1 失败 / 39 通过，重试 4 通过，证据 `timing-extra.log` / `timing-recovery-retry.log`。
3. 首次隔离实测误用 SQLite 跑全套历史迁移，历史 ALTER CONSTRAINT 不支持；改为一次性 PostgreSQL。第二次普通整任务命令误填 2 回合，被既有准入拒绝；修正为 1 回合，未放宽业务校验。
4. 首轮前端全量 244 通过、10 跳过、1 超时：既有 Docker 模板注册测试在滚动点击时超时；未修改该业务代码或强制点击，单独重跑原测试 3 项通过，随后重新全量验证。
5. 首轮 Python 全量在复查发现任务列表新增逐项等待查询后主动中断。修复为整页聚合后从空覆盖率文件重新全量；中断数据只作诊断，不能代表门槛达标。

旧/新节点混合采用能力协商与恢复快照的确定性测试，外加真实历史自带 Runner 镜像；没有宣称在两台不同版本的生产节点上完成混合滚动升级。前端没有覆盖率插桩；独立进程及真机样例未合并到 Python 覆盖率，不以通过数代替覆盖率。

前端最终全量：**246 通过、10 跳过**（3.9 分钟），构建通过；跳过的专用环境场景及名称保留在 `full-frontend-final.log`。真实 systemd 专项：**8 通过**，包括启停、重启恢复、超时及续租丢失。

复查另发现历史终态缺领取时间不能判成未启动或整段等待，补充保守未知投影及测试；第二次诊断全量亦主动停止，最终覆盖率从空数据文件重新运行。

截图：[失败回合仍计时](host-direct-per_episode-missing.png)、[整任务命令](host-command-whole_task-success.png)、[容器 SDK](docker-sdk-command-whole_task-success.png)、[历史尝试](historical-attempt.png)、[取消后冻结](cancelled.png)、[窄屏](narrow.png)。

Python 最终全量：**2430 通过、126 跳过、0 失败**，1907.48 秒。使用 `python -m scripts.test_coverage`，设置一次性 PostgreSQL 的 TEST_DATABASE_URL、LOG_TEST_DATABASE_URL、MIGRATION_DRIVER_TEST_URL；没有排除未测业务文件或降低门槛。

行覆盖率 **92.09%**、分支覆盖率 **83.30%**，所有目录和功能门槛通过。关键“业务执行与结果”为 **96.37% / 89.55%**。详见[统一覆盖率](coverage.md)、[分组数据](coverage-summary.json)、[涉及模块](timing-module-coverage.json)、[完整测试输出](full-python.log)、[126 项跳过明细](python-skips.json)。运行中断的两份覆盖率只作失败证据，未合并到最终测量；真实子进程和浏览器验证不冒充已插桩覆盖率。

本次未部署生产平台。上线需要迁移 ab852c3；新独立回合自动计时需要 Worker/Runner 0.21.15，任务与批次时间不要求升级旧节点。
