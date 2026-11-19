# 修正后全量复测（2026-11-19）

本次测试基于提交 7f74734，包含宿主机首次失败持久恢复、Agent 测试状态原子写和集合向导测试接口补齐。测试期间不修改实现；未升级平台或节点。原始全量的失败证据仍保留于 [上一轮报告](../host-recovery-full-20261119/README.md)。

## 执行方法

Python：`PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/full-recheck-20261119`。使用项目统一分母和覆盖门槛。

浏览器：独立 Vite 监听本机 18081，运行全部 Playwright 测试；清空生产账号及 live API 环境，真实部署用例按原规则跳过。类型检查和生产构建均通过，构建保留大于 500 kB chunk 的提示。前端未插桩，不能宣称前端覆盖率。

真实迁移：新建一次性 PostgreSQL 16.8 容器，只监听本机随机端口，运行 test_migration_driver_scripts.py（runpy 采集同进程脚本覆盖）；每项使用独立临时数据库，验证集合、资源池归属及镜像运行环境迁移的升级、数据保留、降级保护和再次升级。没有连接生产数据库，完成后已删除临时容器。

## 最终结果

- 前端全套：138 passed、10 skipped、0 failed，2.5 分钟，见 frontend.log。
- 真实 PostgreSQL 迁移：3 passed、0 failed，10.33 秒，见 migrations.log。
- Python 默认全量：**1629 passed、133 skipped、0 failed**，1122.09 秒（18 分 42 秒），见 python-full.log。本轮修正后的 Agent 集成用例在全量中通过，无需失败重跑。
- 默认全量覆盖率：89.47% 行 / 79.93% 分支，scripts 行 78.38% 不足 80%，因此 scripts.test_coverage 命令仍返回 1；pytest 本身返回 0，二者不能混淆。见 coverage-default.md。
- 合并本轮全量与本轮迁移补测的独立覆盖文件（相同实现、相同统计配置，未引入旧轮数据）后，总行 **90.04%**、总分支 **79.93%**，scripts **80.81% / 78.20%**，所有目录及关键功能组覆盖门槛通过（coverage-extended.md）。覆盖报告工具返回 0；未降低阈值或排除未测文件。

扩展报告位于 coverage-artifacts/full-recheck-20261119-extended/，原始默认报告位于 coverage-artifacts/full-recheck-20261119/。累计覆盖不能改变默认运行的 133 个跳过；其中 3 个迁移脚本测试另行启用后通过。跳过明细见 skips.md，包含专用 PostgreSQL、真实 Docker/systemd、root/挂载命名空间等显式环境要求。未插桩独立进程及前端未计入 Python 覆盖率。

本轮出现两项 Python 弃用警告（Starlette/anyio、跨进程测试在多线程进程中 fork），不是测试失败。前端构建的 chunk 大小提示已保留于 frontend-build.log。

真实数据库迁移通过不等于真实 GPU/NPU 模型验收；本轮没有通过 SSH 更新或操作 29 节点。
