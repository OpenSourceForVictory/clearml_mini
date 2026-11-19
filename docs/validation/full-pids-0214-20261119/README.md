# Worker 0.21.4 后全量测试（2026-11-19）

基于提交 `6e7c404`，本轮不修改产品实现、不部署 API、不升级节点。Python 使用 3.13，测试与覆盖报告命令均返回 0。

## 结果

- Python：**1727 passed、104 skipped、0 failed**，1205.26 秒（20 分 05 秒）。见 [完整日志](python-full.log)。
- 前端 Playwright 全套：**138 passed、10 skipped、0 failed**，2.5 分钟。见 [日志](frontend.log)。
- TypeScript `tsc --noEmit` 和 Vite 生产构建通过；保留 chunk 大于 500 kB 的提示，见 [构建日志](frontend-build.log)。
- Python 总行覆盖率 **90.00%**、总分支 **79.92%**；全部目录和关键功能组达到各自门槛，见 [覆盖报告](coverage.md)。未降低门槛、排除未测代码或合并历史测试数据。

关键功能：调度与资源 97.48% / 88.19%；节点纳管与升级 95.67% / 83.65%；业务执行与结果 91.13% / 82.06%（行 / 分支）。

## 复现与边界

使用一次性 PostgreSQL 16.8 容器，仅监听本机随机端口，先升级到仓库迁移 head，再将 `DATABASE_URL`、`TEST_DATABASE_URL`、`MIGRATION_DRIVER_TEST_URL` 指向该一次性服务器。没有访问生产数据库。后两个变量启用相应并发测试及三个迁移驱动测试；其他测试有各自开关，未开启的仍跳过。

```bash
PYTEST_ADDOPTS="--import-mode=importlib -rs" .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/full-pids-0214-20261119
```

原始 XML、JSON、HTML 覆盖产物位于上述 coverage-artifacts 目录。前端使用本机 18081 独立 Vite，清空生产账号与 live API 测试变量后运行全部 Playwright。测试数据库及 Vite 已清理。

[跳过原因](skips.md) 保留原始证据，包括未开启的 Docker、systemd、特权挂载、独立迁移等测试。默认全套完成不代表所有可选真机场景已验证。本轮没有真实 GPU/NPU 模型评测；前端及未插桩独立进程不计入 Python 覆盖率。此前 PID 真机验证范围见 [独立记录](../unlimited-pids-20261119/README.md)。

Python 保留两项弃用警告：Starlette/anyio 别名及多线程进程 fork 提示。它们与前端构建提示均非测试失败。
