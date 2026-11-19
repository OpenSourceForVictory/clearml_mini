## Context

见 proposal.md。Run 已有 execution_id，但任务详情的 attempts 只返回 Run.id；两者不同。ExecutionData 已保存 run_id、slot_id、root_path、entry_name、scanned_at 和扫描/清理状态。现有 jobs/{id}/execution-data 限制 last_cleanup_status 非空，只服务清理提示，不能据此判断目录是否存在。Worker 本地 state 有存储快照，平台 Run 没有目录快照。

## Goals / Non-Goals

目标：按所选尝试定位真实上报目录，清晰标记未上报、缺失和历史状态，适用于宿主机和 Docker 的节点侧目录。
非目标：不新增每回合日志归档、文件浏览、SSH、删除入口或自动扫描；不根据路径判断业务成功，不改启动目录和目录布局。

## Decisions

1. 任务详情 attempts 追加可空 execution_id；保留 id 的 Run.id 含义。新增只读 jobs/{job_id}/attempts/{run_id}/execution-location，响应 execution_id 和 directories 数组；遵循任务所有权及 run.job_id 校验。替代方案是扩大旧清理接口，但旧界面按清理状态显示，独立接口更清楚且无需改变旧调用。
2. directories 仅从该 Run、该槽位且执行 ID 匹配的 ExecutionData 取 root_path/entry_name，返回节点名、扫描时间、状态。路径为 POSIX 节点路径，必须校验绝对 root 和 UUID entry 与 execution_id 一致；不允许畸形上报构造可复制的误导路径。非法记录显示不可用，不暴露 reason 原文。多个记录分别列出，不随意选一个“当前”路径。不从当前 storage report 推断；没有记录显示“目录尚未上报”，已知扫描数据不保证当前文件存在。
3. 共享 ExecutionLocation 组件接入 main.tsx 任务详情和 batch-detail.tsx 排查区，以 jobId/runId 为键重建请求，防止切换尝试后迟到响应覆盖。只查询打开的尝试，避免列表 N+1。目录文字可选择，复制通过浏览器剪贴板 API，捕获拒绝并提示手动复制。空值不提供复制动作。
4. 页面展示执行目录及说明；不声称 host-control 必然在该路径下（旧布局可能不同），不将 work/episode-N 当成独立日志目录。既有清理提示不作路径数据源。

## Risks / Trade-offs

- [扫描延迟] → 首次启动可能只有执行 ID；显示等待上报，可在机器数据保留入口主动扫描，避免伪造目录。
- [历史上报过时] → 显示扫描时间与清理状态，不保证文件在线可读。
- [路径泄露] → 沿用任务所有者/管理员授权，只展示对应 Run 数据，不给普通用户机器全量目录清单。
- [浏览器 HTTP 剪贴板限制] → 原文可选择且错误可见，不谎报复制成功。

## Migration Plan

无数据库迁移，无 Worker 更新；更新 API 与前端。旧 API 未提供新字段/接口时组件显示不可用，不影响日志及结果；回滚不涉及持久数据变更。上线前按当前流程检查活跃执行；本次开发只运行关联回归，部署另行记录。
