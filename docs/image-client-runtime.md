# 自带镜像、启动变量和节点目录映射

## 用户操作

在 **CS 评测批次 → 客户端注册 → 注册自己的客户端镜像** 填写名称、版本、镜像地址、启动命令和运行配置。模板属于创建者，普通用户互相不可见；管理员可查看。模板版本不可变，修改时注册新版本。名称/版本当前全平台唯一，冲突时换一个名称或版本。

镜像需包含 Python 3.11+（`python3` 或 `python`）。平台会覆盖镜像 ENTRYPOINT 并注入标准库执行适配层；不用把平台 runner 打进镜像。用户程序仍需实现[评测客户端协议](cs-client-protocol.md)，输出最终指标和回合事件；平台不能从任意程序日志推测完成回合数。

命令采用 JSON 数组，例如：

```json
["python3", "/app/evaluate.py", "--task", "${task_name}", "--assets", "${assets:robotwin}"]
```

运行配置示例：

```json
{
  "interpreter": "python3",
  "environment": {"TASK": "${task_name}", "ASSETS": "${assets:robotwin}", "LOG_LEVEL": "info"},
  "overridable_env": ["LOG_LEVEL"],
  "work_dir": "/workspace/eval",
  "output_dir": "/results",
  "assets": {"robotwin": "/data/robotwin"}
}
```

然后在集合管理选择该客户端，导入或定义任务清单。发起集合运行时，启动变量覆盖 JSON 可填写 `{"LOG_LEVEL":"debug"}`。仅 `overridable_env` 中的键可覆盖；每次提交保存独立快照。每个清单任务的名称、回合、种子、parameters 仍来自清单。

支持占位符：`${task_name}`、`${episodes}`、`${seed}`、`${endpoint}`、`${input_path}`、`${work_dir}`、`${output_dir}`、`${assets:逻辑名}`。命令逐参数替换，不隐式执行 shell，不展开 `$HOME`、反引号等。含占位符的任意命令字符串只替换一次，任务名中的 shell 字符保持原样。

平台注入 `MINI_EVAL_INPUT`、`MINI_EVAL_TASK_NAME`、`MINI_EVAL_EPISODES`、`MINI_EVAL_SEED`、`MINI_EVAL_ENDPOINT`、`MINI_EVAL_WORK_DIR`、`MINI_EVAL_OUTPUT_DIR`。完整任务 JSON 位于 `/tmp/mini-eval-job.json`。保留 `MINI_EVAL_*`、`NVIDIA_*`、`CUDA_VISIBLE_DEVICES`、`PYTHONPATH/PYTHONHOME`，用户不可覆盖。普通变量是明文配置，不是密码/令牌保管功能。

采用 SDK 的已有项目可以在自己的入口脚本中 `from runner.sdk import run`，再调用 `run(client, config)`；工作目录切换后应使用绝对脚本路径或在镜像中安装好自己的 Python 包。示例：[client.py](../examples/image-client/client.py)、[Dockerfile](../examples/image-client/Dockerfile)。这是容器协议自检，不是 RoboTwin 模型质量评测。

## 管理员操作

1. 机器纳管并加入资源池，Worker 升级到 v5。
2. 提前在各机器安装同一镜像引用、准备可读资产目录；平台不自动拉镜像、不处理私有仓库登录。
3. 在 **管理设置 → 机器管理 → 对应机器“镜像与逻辑资产”** 配置批准镜像（每行一个）和逻辑资产宿主映射 JSON，面板在机器存储配置旁。

例如本机 `{"robotwin":"/home/dgh/datasets/robotwin"}`，R8000 `{"robotwin":"/home/huawei/datasets/robotwin"}`；客户端容器均看到 `/data/robotwin`，资产只读。用户不能在任务里指定宿主路径。工作和结果目录为每次执行单独分配，`/workspace/eval` 和 `/results` 是该执行私有目录的容器别名，不是整台机器的工作根。

每次保存带 `expected_revision`；并发修改冲突返回 409，界面轮询保留编辑草稿，点击重新载入后才能用新版本保存。节点忙碌时等待旧执行清理，未领取预留任务回队列，运行中任务/续租/恢复不受新配置阻断。空闲后持久化生效配置。恢复本机运行配置会恢复首次启动保存的 `client_images` 与可选 `asset_mappings`。

Worker 后台定期检查批准镜像是否已存在、目录是否合法以及 Docker 只读挂载是否可读。每项资源单独显示就绪或错误；缺少某资产不阻止不需要它的任务。报告过期或配置版本未确认时暂停新任务准入。任务详情中的“镜像与资产就绪情况”显示各节点缺失原因，不向普通用户展示宿主路径；配额、GPU、磁盘等约束仍另行参与调度。

## API 与兼容

- `POST /api/v1/client-templates`：登录用户创建私有模板，必须提供 `runtime`；共享模板沿用管理员接口。
- `ClientRequest.environment`：单任务与批量提交共用，仅覆盖模板允许的键。
- `GET/PUT /api/v1/admin/nodes/{id}/runtime`：管理员读取、版本化保存 `{expected_revision, config:{images,assets}}`，config=null 恢复本机配置。
- `GET /api/v1/jobs/{id}/runtime-readiness`：任务所有者/管理员查询。
- Worker v5 监督心跳提交 runtime 的 revision、applied_revision、effective、images/assets 就绪报告；服务端返回期望配置，领取返回 runtime_revision。

旧模板 runtime=null 保持旧执行方式，旧 Worker 可继续执行旧任务；自带镜像 runtime 模板必须调度给 v5。节点批准列表由平台接管后同样适用于旧客户端镜像，应保留仍需使用的引用。目录映射不保证资产内容不可变，宿主侧删除/改权限会影响任务。

迁移 `f629a11` 新增字段。升级顺序为 API/前端、随后 Worker。回退前停止新模板使用、排空执行；存在 runtime 模板或节点历史配置版本时迁移拒绝降级，避免旧 API 绕过新约束。不要为了回退直接删除历史任务或覆盖新数据；保留备份并先制定迁移处理方案。
