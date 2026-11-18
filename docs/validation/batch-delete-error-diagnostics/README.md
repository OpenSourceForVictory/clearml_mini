# 批次删除错误诊断验证（2026-11-18）

现场报告 HTTP 500 和 Unexpected token 'I'。模拟文本 500 已复现前端错误；未获得现场 API 堆栈，不能认定后台 500 根因已修复。

修复前：新增浏览器 HTTP 500 用例失败，实际为 `SyntaxError: Unexpected token 'I', "Internal S"... is not valid JSON`，预期为请求失败 (500)。原批次浏览器测试 2 通过、1 失败：确认框缺少“不释放磁盘空间”说明。首次 Vite 启动因路径重复 frontend/ 失败，修正启动路径后运行测试。

修复后：batch-history.spec.ts 7 通过（文本 500、HTML 502、空 503、JSON 409、保留选择并重试、空成功、格式错误成功、文本 401）；TypeScript 检查通过。test_batch_deletion.py 与 test_batch_deletion_records.py 合计 7 通过，1 条 Starlette 弃用警告。现有 6 个删除 API 测试另在 SQLite foreign_keys=ON 下全部通过；新增记录测试在删除时开启外键，覆盖日志、回合、产物、执行数据及清理记录。

未运行全量测试、统一覆盖率统计、真实 PostgreSQL 或现场业务验收；不宣称达到覆盖率门槛。未部署。无需数据库迁移。前端 mock 不代表后台 500 已解决。现场仍需批次 ID、失败时间对应的 API Traceback。
