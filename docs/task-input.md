# 用户程序输入与参数适配

参数契约基础模块已实现；向导、调度和执行器完整接入以OpenSpec任务进度为准，不能仅凭本地dry-run视为已上线。

客户端模板可声明`task_type: command`执行普通命令；使用`service_mode: none`无需推理地址。兼容旧API的episodes字段固定为1，但不生成或报告虚构回合，退出码0代表执行成功。使用平台映射或普通命令需要Worker协议v10（软件0.12.0）；旧Worker不会领取这些任务。普通命令不能声明episode_reporting或sdk-command。原有evaluation模板保持原评测结果契约。

普通程序可读取`MINI_EVAL_INPUT`指定的JSON文件，不需要平台账号或调度API：

```python
import json, os
with open(os.environ['MINI_EVAL_INPUT']) as f:
    config = json.load(f)
business = config['business']
task = config['task']
service = config['platform']['service']  # 无服务任务为None
output = config['platform']['paths']['output']
```

使用SDK时，`runner.task_input.read()`支持新版输入及旧CS输入转换；现有`EvaluationClient`可使用`context.business`和`context.service_endpoint`。不要自行选择机器或替换平台分配的服务地址。

模板作者可将平台字段显式映射到既有程序参数：

```json
[
  {"source":"platform.service.endpoint","target":"argv","name":"--server"},
  {"source":"task.task_name","target":"argv","name":"--task"},
  {"source":"platform.paths.output","target":"env","name":"BUSINESS_OUTPUT"}
]
```

业务字段继续使用`parameter_schema`声明类型、默认值、必填及`binding`。argv按数组传递，不经过shell；对象/数组需要显式JSON编码或repeat数组参数。平台与业务映射不能重复占用相同参数目标；环境变量不能覆盖平台保留字段及设备可见性变量。

客户端参数顺序为模板默认、集合默认、case参数、本次显式覆盖。只有`overridable=true`字段接受本次运行覆盖；对象整体替换。服务端独立解析，不受客户端同名参数影响。参数来源和有效值由解析器同时返回。

本地检查（只验证和打印，不执行程序、不连接平台）：

```sh
python -m runner.task_input input.json --dry-run
```

输入文件可使用现有CS格式，工具会转换为带`input_version=1`的platform/task/business结构。正式执行还要检查模板权限、节点资源和服务健康；本地检查不会预留机器。

## 复制与编辑API

`GET /api/v1/runtime-templates/client`和`server`返回当前用户可见的已发布模板与自有草稿。`POST /api/v1/runtime-templates/{kind}/{id}/clone`使用`name`和`version`创建私有草稿；`PUT /api/v1/runtime-templates/{kind}/{id}`提交`expected_revision`和完整`definition`修改草稿；`POST .../actions`使用`action=publish`发布。草稿不会出现在运行模板候选项中，已发布定义需要另存新版本修改。

服务端公共模板默认不向普通成员开放。管理员使用`PATCH /api/v1/admin/server-templates/{id}/access`提交`expected_revision`和`available_to_members`开放自己的模板。复制不继承公共发布权限。成员可复用已批准的宿主机模板，但不能修改宿主机命令、环境或参数映射来扩大执行权限。

`POST /api/v1/suites/{id}/clone`使用新`name`和`version`复制自己的集合为草稿，之后沿用集合草稿编辑/发布接口。所有编辑带revision检查；冲突时保留本地输入、重新读取后再提交。已有运行始终使用提交时快照。

集合可保存`client_defaults`，每项case可通过`parameters`覆盖。`service_keys`保存逻辑服务引用，具体实例分配由编排模块负责。`resources`接受cpus、ram_gb、gpu_mode、requested_vram_gb和required_gpu_model，case覆盖批次资源默认。CPU/内存不超过资源池的每任务预算；运行参数batch_size等不影响资源申请。任务领取使用实际预留的CPU/内存，预检逐项返回case_checks。

## 集合运行向导与两端编排

CS 评测批次 → 评测集合运行提供五步向导：集合、客户端、服务端、资源、确认。
可使用已有集合、临时清单或同时保存新集合。客户端和服务端分别填写业务参数；
确认页显示逐项有效值及来源。选择“平台启动服务端模板”后无需手工选择机器、端口或复制地址。
每个服务定义具有逻辑名称（默认 `default`），任务可通过 `service_keys` 限定候选定义。
未限定时可选任一兼容服务。服务共享/独占是访问策略，与 GPU 共享/独占分别设置。
普通命令模板可选择“无需服务”，其完成情况依据退出码，计划回合数显示为零。

客户端注册页的“模板复用与草稿”可复制两端模板、编辑业务 schema 和运行配置、保存草稿及发布。
向导中也可以复制修改模板；发布成功后切换到新版本，取消保留原选择。复制集合保留任务参数、
集合默认值和资源覆盖，提交时原子保存新集合与运行。已提交任务继续使用冻结的快照。

### 管理员准备

1. 升级数据库至 `r841a23` 及 API/调度器，使用支持协议 10 的 Worker。
2. 服务端机器需要 `server` 角色、资源池成员和已批准的运行环境/镜像。
3. 机器配置 → 服务自动分配网络，填写客户端可达的本机 IPv4 与 1–1001 个端口的范围。
   API 为 `PUT /api/v1/admin/nodes/{id}/service-network`，提交 `expected_revision` 和
   `config: {address, port_start, port_end}`。留空配置禁止新的自动分配，已有实例地址不变。
4. 公共服务模板默认不对成员开放。管理员通过
   `PATCH /api/v1/admin/server-templates/{id}/access` 提交 `expected_revision` 和 `available_to_members: true`。
5. 全局及用户活动执行配额至少为 2；服务端执行也消耗配额、CPU、内存、GPU 资源。

### 提交与观察

`POST /api/v1/orchestrations/preflight` 只读检查，不占用端口或启动进程。
`POST /api/v1/orchestrations` 使用同一请求体并要求 `Idempotency-Key`；同键同内容返回原批次，
同键不同内容返回 409。`client.service_mode` 为 `external`、`managed` 或 `none`。
托管模式的 `services` 数组每项包含 `key`、`template_id`、`pool_id`、`mode`、
新提交使用 `instance_count`（1–20）表示目标服务实例数；兼容旧 `max_instances` 按需上限，二者互斥。其他可选项：`parameters`、`parameter_overrides`、`environment`、`resources`。
外部模式使用 `client.endpoints`；无服务模式不填写地址。新集合使用 `new_suite`，与 `suite_id` / `cases` 互斥。

调度器在现有 Run 账本中同时预留一个服务和一个客户端的预算；客户端等待当前服务执行的新鲜健康报告，
领取时再核对。已有健康共享实例直接复用，独占实例等旧客户端清理后才能再分配。
服务健康过期或故障阻止新客户端开始；已经运行的客户端保留原绑定，按自身协议失败/超时，平台不会偷偷换址。
端口占用失败最多尝试三个不同端口，其他错误需要修正后显式恢复。GPU 调度仍遵守已有资源池与机器上限。

批次详情显示任务 → 实际服务地址 → 服务执行代次，可查看本人服务日志并停止/恢复。
默认在所有客户端结束且清理确认后停止服务；取消也遵守该清理顺序。选择保留时由用户手动停止。
自动结束批次应先重试客户端再恢复服务；仍有排队客户端时，恢复请求仍须联合分配两端预算。
保留策略下、无等待客户端时的显式恢复将重新排队服务任务。旧执行尚未清理时拒绝恢复。

回退前必须停止新编排提交并排空两端执行；不能让旧调度器接管仍在等待服务的客户端预留。
