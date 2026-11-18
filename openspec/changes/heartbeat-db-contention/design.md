# Design

## Context

2026-11-18 全量回归发现 Agent 配置与心跳并发时 sequence 从 2 回退到 1。见 proposal；心跳已改用 Worker 行锁，但其他 connection JSON 写入仍仅用调度 advisory lock，读取的旧 JSON 会覆盖最新样本。

## Goals / Non-Goals

保证同节点 connection 的所有读改写以同一 Worker 行锁读取最新状态；保留普通心跳不获取全局调度锁的要求。不改变协议、采样周期、租约、数据库结构或业务执行方式。

## Decisions

低频管理操作保持先调度锁、再 Worker 行锁的顺序；读取用于修改的 connection 前通过 refresh(with_for_update=True) 或锁定 SELECT 获取最新值。Agent 注册、配置、单卡目录设置、纳管与升级回执、SSH 清理均检查。心跳只有行锁，不逆向获取全局锁。拒绝恢复所有心跳全局锁的做法，以免重引连接池争用；不引入 PostgreSQL 专用 JSON 更新以保持 SQLite 开发兼容。

测试使用一次性 PostgreSQL；保留已有配置/心跳/claim 并发回归，增加可控交错验证管理写入等待行锁后保留最新心跳和无关 connection 字段、重复和过期会话处理。SQLite 测试不冒充数据库锁验收。

## Risks / Trade-offs

同节点管理与心跳会短时串行 → 仅在数据库事务内持锁，不持锁执行 SSH；跨节点心跳仍独立。锁定后刷新可能丢弃本事务未提交修改 → 在任何字段修改之前取锁，新建节点沿用 flush 后本事务已有锁。纳管会主动撤销旧 Agent 会话，不能将有意清空与丢更新混淆。

## Migration Plan

无迁移、无 Worker 协议变更。验证后仅需正常 API 发布；本次修复与测试不自动部署。回滚代码会恢复已知并发风险。
