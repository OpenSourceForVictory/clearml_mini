## Context
参见 proposal.md。服务恢复已经走控制池，重评走普通池；全局调度锁维护资源一致性。SQLAlchemy 提交可能使 ORM 属性过期。

## Goals / Non-Goals
目标：隔离重评、缩短控制请求、避免提交后详情查询失败。非目标：移除一致性锁、扩容连接池、修改心跳预算、解决全部监控性能问题。

## Decisions
- request_category 和 nginx 同时增加单任务 retry、批次 retry-failed 路由，保持 POST 限定和日志普通分类。
- 服务控制在提交前构造纯值确认，提交成功后只返回该字典，不再访问 ORM；不在控制事务内调用完整 view。
- 内置服务页面与单任务重评界面区分控制成功和随后读取失败，明确提示操作已接受，避免误判。
- 单任务重评也在提交前保存响应字段。现有状态校验防止同一当前尝试连续重试；不新增跨尝试幂等协议。
- 批量重评按选中 ID 与 batch_id 查询并保留 selectinload 完整历史，不使用只读 latest_jobs 投影；保持排序、权限、原子成员检查、每项清理和额度检查。
- 保留 expected_revision 和 expected_run_id，重放旧请求不重复启动。网络断开仍应刷新核对，不保证 exactly-once 响应。

## Risks / Trade-offs
- 控制池新增有限批量请求（最多100项）可能占用心跳资源：缩小查询并测试普通池耗尽与心跳共存；不声称消除 PostgreSQL 总容量或锁瓶颈。
- 服务控制响应不再完整：内置界面原本忽略响应，外部调用者使用 GET 获取详情。

## Migration Plan
同步更新两个 API 镜像和网关路由并重建/重启网关使配置生效，沿用现有 Compose 文件。无数据库迁移、无 Worker 更新；回滚同时恢复 API 和网关。
