## Context
Python 依赖准备同步发生在 Worker 注册前，多个 Worker 争用安装锁，回连仅等待 120 秒。
## Goals / Non-Goals
注册心跳不等待可选依赖；不掩盖 API 响应超时或认证失败，不自动放宽业务准入。
## Decisions
依赖在单个后台线程准备；心跳更新 software 能力，缺少 service_readiness_v1 不阻止安装/升级版本验收，业务调度仍检查能力。保存重连时节点修订和发布摘要；继续验收仅进行平台状态查询与受修订保护的完成操作，不使用 SSH 或旧凭据。执行器环境 ONBOARDING_RECONNECT_TIMEOUT_SECONDS 限制 30–1800 秒，默认 300，非法值启动时拒绝。UI 显示等待预算、各组件与探测依赖状态。
## Risks / Trade-offs
旧 API 忽略新增心跳 software；需同步更新 API/执行器/UI，并更新节点程序。依赖安装异常只报告安全错误分类，不输出可能带凭据的 pip 原文。现场 HTTP 超时仍需独立诊断。
