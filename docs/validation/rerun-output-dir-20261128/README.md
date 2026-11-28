# 历史批次 `output_dir` 参数专项验证

问题：客户端模板有业务参数 `output_dir` 时，基于批次创建并修改它，是否会改变新任务的启动命令或产物目录。

当前实现把 `output_dir` 列为平台保留名。类型化客户端模板声明同名字段时，注册 API 返回 400 `invalid request parameters`；底层契约明确报 `invalid or platform-reserved parameter name`。这不是再次运行阶段才限制的。旧式无类型化模板的任务参数 JSON 可包含 `output_dir`，再次运行导入、编辑、预检、提交均保留修改值；新任务输入中的 `business.output_dir` 会更新，但 `platform.paths.output` 仍是节点分配路径。旧式模板无该字段绑定，不会自动把它拼进启动命令。

再次运行从当前模板引用生成新的任务快照，不复制旧执行生成的 argv。用合法字段 `result_dir` 显式绑定 `--output-dir` 的集成测试中，原任务命令为 `--output-dir /business/old`，再次运行改值后新任务命令解析为 `--output-dir /business/new`。若业务应写入平台可浏览的产物目录，应绑定 `platform.paths.output` 或读取 `MINI_EVAL_OUTPUT_DIR`，不要用旧式同名业务值替代。

验证：项目 `.venv` 执行 `backend/tests/test_batch_rerun.py` 与 `backend/tests/test_parameter_contract.py`，48 项通过；`frontend/tests/batch-rerun.spec.ts` 覆盖向导编辑旧式同名参数及预检/提交请求，14 项通过；前端构建通过。浏览器测试使用模拟 API，后端集成测试使用隔离数据库与命令绑定函数；未在真机启动用户业务，也未运行全项目覆盖率。本次只有测试和说明变更，无迁移、API/Worker/Runner 运行时代码或部署改动。
