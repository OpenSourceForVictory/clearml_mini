# 多设备存储设置验证（2026-11-17）

触发条件：一台机器注册多张 GPU/NPU，整机存储配置为空。旧界面把第 0 卡上报的实际工作目录预填到整机表单，其他卡没有独立的编辑入口；管理员保存后可能无意中改变所有设备的目录。

修正后，整机默认目录只从整机期望配置读取；每张卡可独立设置工作、资产、缓存目录和空间门槛。每卡期望配置经 Worker 心跳下发，并用同卡回执校验。建议工作目录包含机器名与卡号，重复点击建议按钮不会重复拼接。实际工作目录仍追加 `/slots/<slot_id>`；旧数据不会自动搬迁或删除。每卡变更递增整机存储版本，其他卡需要重新确认。旧 API 版本忽略每卡覆盖，回退时需恢复新版 API。

验证范围：

- `.venv/bin/python -m pytest backend/tests/test_storage.py backend/tests/test_execution.py backend/tests/test_worker_storage.py -q`：14 通过、1 跳过。覆盖独立设置、权限、版本冲突、心跳下发、回执不匹配、恢复默认和既有执行边界。
- 前端 TypeScript 检查、Vite 构建通过；Playwright `storage.spec.ts` 与 `config-pages.spec.ts`：8 通过，覆盖多卡编辑、建议路径幂等、草稿轮询、冲突、权限与恢复默认。
- 构建 `mini-eval-api:per-device-storage` 并部署至本机 `mini-eval` API；部署前无运行中或待清理执行，数据库备份位于未提交的 `.runtime/per-device-storage/predeploy.dump` 且可用 `pg_restore -l` 读取。部署后容器 healthy，回环和局域网 `/healthz` 均返回 200，数据库仍为 `v285e67`。

按用户要求未执行全量测试，也未统计本次完整覆盖率。上述页面与 API 验证不等于真实模型评测或节点目录实际迁移验收；每张卡保存后仍应在界面核对 Worker 回执。
