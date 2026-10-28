# API 示例

交互式契约见 `/docs`。下列示例使用 shell 环境变量，不把密码写入仓库。`BASE` 指向控制节点，`TOKEN` 是登录返回的短期 JWT。

```bash
export BASE=http://127.0.0.1:8000
curl -s "$BASE/api/v1/auth/login" -H 'Content-Type: application/json' \
  --data '{"username":"admin","password":"替换为本机初始密码"}'

curl -s "$BASE/api/v1/pools" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/jobs" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: my-eval-001' \
  --data '{"title":"第一次评测","benchmark":"mock","model_ref":"model-v1","episodes":10,"pool_id":"替换资源池 ID"}'

curl -s "$BASE/api/v1/jobs/$JOB_ID" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/jobs/$JOB_ID/logs?after=-1&limit=20" -H "Authorization: Bearer $TOKEN"
curl -s -X POST "$BASE/api/v1/jobs/$JOB_ID/cancel" -H "Authorization: Bearer $TOKEN"
curl -s -X POST "$BASE/api/v1/jobs/$JOB_ID/retry" -H "Authorization: Bearer $TOKEN"
```

同一用户的 Idempotency-Key 重复提交返回原任务。日志返回 `next_after`，下一次请求用它作为 `after`；`run_id` 可选择历史尝试。成功指标在任务详情的 `attempts[].metrics` 中。

管理员创建账号、资源池和 Worker 身份的接口分别是 `/api/v1/admin/users`、`/api/v1/admin/pools`、`/api/v1/admin/worker-credentials`。普通用户返回 403。Worker 使用独立 `wk_...` 凭据，不能使用用户 JWT；认证凭据不出现在任务、日志或 Worker 列表响应中。

常见错误：400 参数不合法，401 未认证/凭据失效，403 无管理员权限，404 任务不存在或无权查看，409 状态冲突/旧容器尚未清理，413 日志配额已满，429 用户排队额度已满，503 数据库不可用。RoboTwin 尚未开放，提交会返回 400。
