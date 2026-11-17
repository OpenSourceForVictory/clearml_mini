# 计算资源 GPU 遥测新鲜度显示验证（2026-11-17）

触发条件：管理员“计算资源”页旧判断只检查 `平台时间 - telemetry_at < 15 秒`，因此未来时间戳或缺少空闲显存/利用率的样本可能显示为新鲜，而调度判为无效。修正后 API 列表直接使用调度的 `telemetry_fresh` 判定，只有过去不足 15 秒且两个数值齐全才显示新鲜；未改变采样、心跳、有效期或准入逻辑。

`test_resource_roles.py` 覆盖有效、超过 15 秒、未来时间戳和缺失字段的管理员 API 明细，3 项通过。TypeScript 检查、Vite 构建、相关资源页及登录会话 Playwright 11 项通过；OpenSpec 严格校验通过。

隔离 PostgreSQL 上的 `python -m scripts.test_coverage`：1311 通过、100 跳过、0 失败；全部 Python 行/分支 90.03% / 79.26%，`backend/app` 95.23% / 80.60%，全部既定目录和功能门槛通过。原始报告在忽略入库的 `.runtime/resource-freshness/coverage/`，`tests.xml` 保留特权挂载、真实容器/进程及专用环境等跳过原因；前端未插桩，不计入 Python 覆盖率。

本次没有验证用户现场的 GPU 样本时间，也没有改变实际运行的 `mini-eval-api:login-renewal-8479d78` 容器；该容器只发布登录修复，不包含本次资源显示修正。当前页面若仍显示旧行为，需在后续发布本提交后再核对。
