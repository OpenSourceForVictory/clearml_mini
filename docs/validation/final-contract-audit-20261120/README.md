# 日志与产物复合条款补验

本轮只补测试和证据，不修改生产 Python/Worker/Runner 行为。主工作区冻结全量单独运行；这里使用隔离工作区和专用 PostgreSQL 容器创建/删除一次性数据库，未访问生产数据库。

## 新增可观察断言

| 测试 | 结果与行为 |
| --- | --- |
| test_shared_artifact_lifecycle.py | 4通过。PostgreSQL仅2连接、两个共享目录读取被挂起时，连接全归还，心跳及取消成功；FAILED/CANCELLED/SUCCEEDED终态并发登记唯一，批次删除后迟到上报404，不删共享业务文件 |
| test_process_log_lifecycle_edges.py | 4通过。真实PostgreSQL重复目录登记只产生一个ID，保留旧RunLog；终态只更新已登记scope；删除后迟到catalog/reply及历史读取均404。真实open边界换链接被拒，已取得目录句柄不随可见路径切换 |
| test_process_log_scale_retention.py | 2通过。构造9999已关闭空scope历史后真实创建/写入/关闭第10000份，验证元数据预算耗尽时业务仍退出0，产物不删；真实rename至.cleanup后登记为cleaned，替换目录不能借原身份读取 |
| test_service_process_log_entry.py | 8通过。宿主进程及真实Docker各覆盖退出0、退出7、可执行文件不存在、取消；实际执行service_entry，传递参数/环境/端口；容器删除后日志仍在，取消与启动失败标incomplete，正常和非零退出标closed |
| process-log-recovery.spec.ts | 9通过。超时/轮转忙/清理/不存在/失败/代次变化明确提示且可刷新；关闭后旧响应不污染重开页面，暂停不轮询，恢复读取新页，取消下载不生成半份文件且保留已显示正文 |

低预算用例直接构造越限索引以验证守卫，不代表管理员降额会改变活动执行快照。

万scope用例是历史索引和末份真实写入的规模边界，不是启动一万个业务进程或吞吐基准；并发写入、真实writer崩溃、轮转预算和持续输出内存边界沿用test_process_log_concurrency.py。保留首轮2失败：测试使用了不存在的sync_one方法，并把执行预算调低于scope预算；纠正测试构造后2项通过，未放宽实现或门槛。

## OpenSpec条款对照

- 产物2.2：上述小连接池、终态重放/删除测试，加test_shared_artifacts中的变化/缺失/能力协商/无正文上传，test_shared_artifact_migration的真实并发首次登记与旧正文保留。
- 产物2.3：artifact_layout、manifest、collection_failures的宿主/容器持久路径、回合归属、异常降级；[真机矩阵](../artifact-final-matrix-20261120/README.md)验证容器删除后、64MiB范围及Worker停止/恢复后读取。文件不依赖Worker活跃，不能反推节点文件已备份。
- 日志1.2/4.2/4.3：新增登记/删除迟到回包、open竞态与staging测试，加原PostgreSQL迁移、cursor/generation/过滤空页、读取与清理互斥、轮转后缺口、cleaned墓碑拒绝复活。
- 日志2.3：四写进程共用execution预算、writer崩溃恢复、轮转淘汰及万scope末份写入/元数据预算；整机不限制，仍沿用原保留清理，不启用跨账号配额通道。
- 日志3.2/3.3：service_entry实际失败/取消矩阵，加cs_entry原生SDK/SDK命令/直接协议/普通命令的host/docker组合及0缺final；能力协商拒绝旧镜像启用不支持的格式，Worker注入当前Runner、专用挂载、普通账号路径权限、安装SDK包真实导入均有现有测试和真机证据。兼容v2外层仅保留有界摘要，独立scope文件是业务日志来源。
- 日志5.1/5.2：新增恢复/晚到响应/暂停/取消，加process-logs.spec.ts的有限翻页、过滤、缺口及固定下载分片，services.spec.ts的同名实例与历史执行选择，实际GPU/NPU页面查看和下载。前端未插桩，不报告其Python覆盖率。

关联组合回归184通过（283.90秒，无跳过）；新增18项Python、9项浏览器不是全仓覆盖率证明。主工作区冻结全量另行报告。已有失败和条件跳过不删除。

新增浏览器用例并入前端全套后：241通过、10条件跳过（3.7分钟），没有前端覆盖率结论。关联Python184项通过无跳过。
