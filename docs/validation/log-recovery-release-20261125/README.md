# 2026-11-25 部署与真实进程验收

部署源码：3216ae0；API 镜像：mini-eval-api:log-recovery-3216ae0。本次使用现有四角色 Compose 与网关，保留原存储挂载；先确认无活跃执行和未清理资源、备份数据库，再迁移 ae185f6 → b03a7b8 并启动。四角色健康检查、登录、批次列表、机器管理均通过，原 13 个 Worker 槽位、6 个 Agent 恢复上报。

通过界面使用的升级 API 将 local4090-151 升到内容摘要 218731dd8649e543a44a8e7001c378888fd14a3cfddbc61adf879a634585f947（版本 0.21.17），操作成功、槽位身份不变。不是浏览器点击升级，也没有升级其他节点。完整恢复能力仍不宣告、不启用，不能把此次发布解释为长期断联全链路验收完成。

## 平台批次

以下均由实际平台调度 Worker 启动，不是孤立单元测试；批次记录保留，可在“评测运行与结果”按标题“日志恢复发布验收”查找。

| 类型 | 批次 ID | 验证 |
| --- | --- | --- |
| 宿主机 SDK | 1dde1d0a-9d1a-48c4-8353-3b255ad27b67 | 2 个程序退出 0，业务结果分别成功、失败，执行正常与业务结果分开 |
| 宿主机日志 | 38b3f39d-23b7-45dd-bd7b-1923cbe74d16 | 2 MiB 输出、最新 stdout/stderr 尾部、截断提示、退出 0/1、清理 |
| Docker 日志 | fc1f3524-5b7e-4ff1-97e8-44995db578be | 同上，容器启动与 Runner 接线 |

共 6 个真实进程用例按预期通过；其中退出 1 是故意构造的失败路径，界面显示 FAILED 是正确结果。日志程序还核对 MINI_EVAL_OUTPUT_DIR 与输入配置一致并写入 JSON 产物；本次没有验收产物预览。Playwright 登录部署页面并打开三个批次详情，标题均可见。最终活跃执行和未清理资源均为 0，四角色健康。

## 未通过的准备步骤与验证边界

- 验收脚本初次请求日志 limit=256 超过 API 的 100 上限，返回 400；修正为 100。
- 初次读取 attempt.exit_code，实际返回字段为 case.outcomes.wrapper_exit_code；修正断言位置。修正后复用原批次，没有重复提交。
- 额外 GPU 算子用例首先引用已禁用的历史模板，预检返回 400。创建独立验证模板后，预检指出 release_gpu_smoke 未批准；当前唯一批准环境 template_demo_cwd 的 Python 不含 torch（ModuleNotFoundError）。首次验收因此未执行 CUDA 算子；后续经用户授权补充环境并通过，见下节。
- 没有 NPU 实测、真实模型质量评测或持续容量长测；未注入生产断联。完整恢复能力仍关闭，已确认队列压缩的专项证据见 ../disconnected-execution-20261124/outbox-compaction-20261125.json。
- 本轮部署没有修改业务代码，也没有重跑全量测试；独立业务/API 进程未收集覆盖率，不据通过数推断覆盖率。

机器可读结果见 deployment.json、upgrade-result.json、live-result.json、browser-result.json、final-health.json。原始失败日志、页面截图及数据库备份保留在受保护的本机 .runtime/log-release-20261125，不提交凭据、备份或完整运行目录。

## 追加 GPU 环境与真实算子验收

用户授权新增 PyTorch 环境后，在 local4090-151 创建独立 Python 3.10 环境 `/home/dgh/.local/share/mini-eval-envs/release_gpu_smoke`，安装 torch 2.6.0+cu124、numpy 2.2.6；pip check 通过。包源与版本见 [PyTorch 官方安装说明](https://docs.pytorch.org/get-started/previous-versions/)。没有改项目 `.venv`、系统 Python 或原有批准环境。

通过机器管理同一 API 增加批准环境 `release_gpu_smoke`，Python 为上述目录下 `bin/python`，code_root 为 `/home/dgh/Code/clearml_mini`。Worker 已回报配置 revision 20 生效且环境 ready。模板引用此环境，无需重启 API 或 Worker。

平台批次 `9df7e93c-197d-4994-b57e-90e70babc79e`（日志恢复发布验收｜GPU 宿主机算子｜3216ae0）由 Worker 实际启动并通过 CUDA 张量平方求和断言，输出 RTX 4090、可见 GPU UUID 和结果 357389824.0，退出 0、清理确认。浏览器打开详情验证通过。本轮累计 4 批次、7 个真实进程用例；新增机器可读证据 gpu-result.json。这是 GPU 算子验证，不是模型评测，也没有启用完整恢复能力。

保留追加准备过程的失败证据：系统 Python 3.10 缺少 ensurepip，改用已有 pip 的 `--python` 向独立环境安装；管理员会话过期返回 401，重新登录后配置成功；第一次额外日志断言错误地期待可见设备为数字 0，实际平台使用 GPU UUID，改为核对 UUID 表示后通过。业务算子首次平台执行即成功，没有为这些准备/断言问题修改业务代码或重复提交批次。
