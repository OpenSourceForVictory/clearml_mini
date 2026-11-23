# 双进程 API 隔离专项验证（2026-11-23）

本次新增的是**可选** Compose 覆盖配置；当前 `mini-eval` 平台未切换、未重建或重启。原 `deploy/compose.yml` 继续是单 API 默认部署，不需要数据库迁移或 Worker 协议改动。启用双进程仍需先确认目标 Worker 已兼容背压 503、数据库总连接预算和发布窗口。

`deploy/split-api-compose.yml` 经 Docker Compose v5.1.1 的 `config --quiet` 校验；展开配置确认 `api` 和 `control-api` 均不暴露宿主机端口，只有 `api-gateway` 绑定原 127.0.0.1:8000。附加 `split-api-lan-compose.yml` 时，额外私网地址只绑定网关。Nginx 1.27 配置经 `nginx -t` 验证。控制进程配置 `SCHEDULER_ENABLED=0`，普通进程维持默认单调度器；两者均设置 `API_ROUTE_GROUP` 和 `API_DB_PROCESS_COUNT=2`。

自动化检查枚举当前 FastAPI 注册的 **165 个方法＋路径** 组合，实例化动态路径参数，逐项比较网关路由与服务端 `request_category`；包含心跳、Agent 观察、服务健康、完成/清理、批次和编排服务控制、结果、日志、产物、领取和 Web。API 入口测试确认误路由在处理函数前返回 404，`GET /healthz` 仍可用。隔离 PostgreSQL 下 `test_split_api_process.py` 和 `test_db_budget.py` 共 **90 passed、0 skipped**。针对该测试集合单独采集覆盖率：`backend/app/db_budget.py` 行 94.3%、分支 91.7%；`backend/app/main.py` 行 36.6%、分支 10.7%，因为本轮未运行该模块其余接口测试，不能当作全量覆盖率或达标结论。

网关另在独立 Docker 网络、临时宿主机端口使用两个模拟 HTTP 后端验证，不连接当前平台或数据库：控制路径进入控制后端，Agent/日志/Web 进入普通后端；停止控制后控制请求 502、普通请求仍成功、总健康检查失败；重建控制容器后 DNS 重新解析且控制请求恢复；停止普通后控制仍成功、普通请求和总健康检查 502。首次脚本将普通后端停止时总健康检查误预期为 500，实际返回 502，因此首次退出 1；修正为按“不健康且明确失败”的实际状态断言后，路由、两种故障隔离和容器重建四组均通过。此失败是测试预期错误，不是平台 5xx 业务故障。

另以新标签 `mini-eval-api:split-smoke-20261123` 构建当前工作树，启动**独立项目** `mini-eval-split-validation`：独立 PostgreSQL、迁移、Bootstrap、Onboarding、普通 API、控制 API 和网关；随机本机回环端口，不使用当前平台数据卷、端口或镜像标签。迁移及启动 `--wait` 通过。经真实网关的 `/healthz`、`/readyz`、`/openapi.json` 返回 200；无凭据的 Worker 心跳在控制进程日志中返回 401，Agent 心跳与管理员健康接口在普通进程日志中返回 401。停止控制进程后 `/healthz` 500、心跳 502、普通管理员接口仍返回预期 401；恢复控制进程并停止普通进程后 `/healthz` 502、心跳仍返回预期 401、普通接口 502。测试后执行 `down -v` 删除该隔离项目、网络和卷；当前 `mini-eval` 服务未重建或重启。

验证边界：这不是 50/100/200 Worker 经网关的真实双进程容量验收，也没有逐项执行 165 个接口的业务功能。第一轮网关后端使用模拟 HTTP 服务；第二轮是真实 API/数据库，但只验证启动、路由、认证拒绝与单侧故障，没有创建真实任务。Nginx、Python、PostgreSQL 最终仍共享主机 CPU、磁盘和数据库；进程隔离只能保护线程池和连接配额，不能保证数据库整体过载时的续租时延。
