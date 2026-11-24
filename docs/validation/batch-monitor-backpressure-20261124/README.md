# 批次监控降载专项（2026-11-24）

修复固定2秒失败重试及同请求重复托管候选查询。批次监控正常5秒，失败指数退避至30秒并尊重更长 Retry-After；隐藏文档暂停，恢复可见立即读取，取消旧请求，保留旧数据和具体reason。只读 DiagnosticContext 缓存同批次/服务集合/workload候选，不参与调度或跨请求缓存。

## 验证

- 后端：51 passed，0 skipped，178.29秒。包含监控、预检、只读查询、端点及新增缓存边界；同请求重复100次诊断不新增SQL，下次请求重新识别健康变化；不同服务集合、缺省、空集合及不同批次不混用。
- 浏览器：13 passed，包含现有服务页和新轮询测试。覆盖正常5秒、失败10/20/30秒策略、Retry-After40秒、保留数据、恢复成功复位、隐藏/恢复可见、切换批次防止旧响应覆盖、JSON与非JSON重试元数据及AbortSignal。
- 第一次浏览器运行3 passed / 1 failed：测试安装假时钟后仍自然推进，9999毫秒边界叠加真实耗时提前越过；改为明确暂停假时钟后通过，保留失败日志。不是通过放宽业务断言处理。
- TypeScript检查、OpenSpec strict、git diff --check通过。

## 覆盖率与范围

采用 scripts.test_coverage 同一 project.coveragerc 与 scripts.coverage_report 口径，只跑本次专项；未合并前次变更数据。preflight行覆盖率100.00%，分支94.44%。完整目录与关键功能分母保留，专项全项目门槛未通过，详见coverage-summary.md；前端未插桩，不宣称全项目覆盖率达标。

后端使用隔离SQLite功能/查询计数测试，浏览器使用模拟API；不是故障平台压测或真实模型测试。没有调整锁等待、连接池或请求预算，不能据此认定现场lock_timeout根因已消除。当前未部署；需更新API和前端，无新增迁移或Worker升级。指南docs/batch-monitoring.md、operations及Skill索引已同步。
