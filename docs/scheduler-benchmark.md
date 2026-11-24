# 调度基准工具

`scripts.scheduler_benchmark` 用于隔离测量，不能据此直接宣称平台支持多少 Worker。工具自行创建带唯一标签的 PostgreSQL 16.8 容器，随机绑定本机环回端口；不接受数据库 URL、不读平台 `.env`，只删除自己创建且标签匹配的容器。需要本机 Docker 和项目 Python 依赖，测试容器使用 trust 认证，仅绑定 127.0.0.1，运行完自动删除。

```bash
.venv/bin/python -m scripts.scheduler_benchmark \
  --history 1000 --pending 100 --repeat 5 \
  --output .runtime/scheduler-benchmark/history-1000.json
```

参数 history/pending 为 0–10000，repeat 为 1–20。初始化失败同样写入失败类型，不输出连接凭据。异常后检查自身容器清理是否成功；不要清理其他测试或业务 PostgreSQL。

当前种子是一个用户、一个普通 mock 槽位、终态历史批次和待预约任务；不是托管服务、真实 GPU/NPU 或 HTTP 混合负载。先预热一轮，每次测量以同一时钟、同一数据运行实际 tick，并回滚测量事务，以免后续重复测量只看到已经预约的队列。测量包含 flush，但不包含 commit 持久化成本；有独立测试证明重复运行仍创建实际预约且数据未残留。

输出 tick 时间、SQL 次数/时间、ORM 加载次数、对账和补齐阶段时间及运行版本。计时器本身有开销；未测锁等待、连接等待、RSS、持锁时长、成对服务和端到端控制通道，因此仅是基准工具第一步。覆盖率运行与性能运行分开，不把插桩耗时作为性能结论；正式优化需完成 OpenSpec 的完整决策对照及五轮配对基准。
