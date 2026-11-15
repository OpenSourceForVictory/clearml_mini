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
