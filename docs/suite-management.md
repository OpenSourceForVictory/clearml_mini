# 评测集合导入与管理

入口：CS 评测批次 → 评测集合管理。集合属于创建者，其他用户包括管理员不能通过集合接口读取或修改私有集合。

## 快速创建 RoboTwin 子集

点击“从 RoboTwin 预置创建”，载入固定版本 50 任务。来源为 RoboTwin commit ea8b21121ebb3cd201ff5b3fe361944ac94eda3f 的 env_cfg/eval/all_tasks.yml，与项目已有完整清单一致，默认 demo_clean/joint。

搜索并勾选需要的任务，点击“仅保留选中任务”。再“选中当前显示任务”，设置回合数、种子和公共参数后“应用到选中 N 项”。批量参数合并并覆盖选中任务的同名键；每项仍能单独调整。选择三任务、每项两回合，预览应显示三任务六回合。

填写名称、版本、客户端模板，可以：
- 保存草稿：留在管理页，可继续编辑。
- 发布版本：固定内容，留在详情。
- 发布并运行：固定内容并转到运行页，填写推理服务地址、资源池、并发和显存后才提交任务。

保存集合不检查仿真资产、镜像部署或推理服务连通性，不代表已满足真实评测执行条件。

## 文件格式

支持不超过 1 MiB 的 JSON、YAML。旧 JSON 数组清单保持兼容；完整集合文件使用 schema_version=1：

~~~yaml
schema_version: 1
name: RoboTwin 快速检查
version: "1"
description: 两个任务，每项两回合
tags: [robotwin, smoke]
source: 自定义子集
client:
  name: cosmos-robotwin
  version: "1"
defaults:
  episodes: 2
  seed: 100000
  parameters:
    task_config: demo_clean
    control_mode: joint
cases:
  - task_name: adjust_bottle
  - task_name: beat_block_hammer
    parameters:
      max_steps: 32
~~~

client 为可选名称/版本提示，不是授权；导入后自动匹配唯一可用模板，无法匹配时由当前用户手动选择。模板名以平台实际登记为准。

缺失的 case_id 自动生成 case-NNN 并避开显式 ID；显式重复标识会报错。默认回合数为 3、种子为 0。defaults 仅补充缺失字段，任务 parameters 浅合并并优先。每集合 1–100 项，每项 1–10000 回合，参数每项不超过 16 KiB。

YAML 只允许 JSON 兼容数据；版本号和日期写为带引号字符串。拒绝重复键、别名/锚点、对象标签、非有限数、非字符串键和过深/过多节点。不执行导入文件中的代码。解析使用受限 SafeLoader，参考 [PyYAML 文档](https://pyyaml.org/wiki/PyYAMLDocumentation)。

上传后只是载入预览，不自动保存。校验错误定位到 cases.0.episodes 等字段（索引从 0 开始）；不会跳过坏任务创建半份集合。相同规范化任务清单会提示当前用户已有版本，仍允许明确使用新名称/版本保存。

## 维护与历史

列表支持名称搜索、精确标签筛选、客户端及状态筛选，每页 20 个版本。详情可搜索任务、复制为新集合、创建新版本、导出完整 JSON、查看同名版本及关联运行。展开关联运行后可进入任务详情和结果。

草稿必须是有效清单，但可以持续修改；发布后内容不可改。新版本是复制后的新记录，名称与版本组合不能重复。保存携带编辑版本；并发修改返回冲突，需要重新载入并核对，轮询不会覆盖本地草稿。

归档使集合不能新建运行，已有任务和结果不变；归档可恢复。只有未被运行引用的草稿可以删除，删除前页面确认。已发布版本通过归档整理，不硬删除。导出不包含平台凭据、内部集合/模板 ID 或运行端点，接收方可映射自己的客户端后保存。

## API

- GET /api/v1/suites/catalogs：预置清单。
- POST /api/v1/suites/preview：{"content":"JSON 或 YAML 文本"}；返回 valid/errors/document、任务和回合数、重复版本、模板匹配。无写入。
- GET /api/v1/suites/manage?page=1&q=robot&tag=smoke&state=active：管理列表；state 支持 active/draft/published/archived/all，另可传 template_id。
- POST /api/v1/suites：name/version/template_id/cases，加 status=draft 或 published、description/tags/source。旧调用省略 status 时仍创建 published。
- GET /api/v1/suites/{id}：详情和同名版本；GET /{id}/runs?page=1：关联运行。
- PUT /api/v1/suites/{id}：仅有效未归档草稿，完整定义加 expected_revision；status=published 可保存并发布。
- POST /api/v1/suites/{id}/actions：{"expected_revision":0,"action":"publish|archive|restore|delete"}。
- GET /api/v1/suites/{id}/export：可移植集合 JSON。
- 旧 GET /api/v1/suites 保留数组分页响应，只列未归档发布版本；运行用选定 suite_id 固定任务快照。

## 迁移和回退

Alembic e518f10 新增状态、归档标志、编辑版本、说明、标签和来源。旧集合回填 published/未归档/revision=0，不修改清单或历史任务。

先备份数据库/镜像/代码，再升级 API 和前端；新增 PyYAML==6.0.3 依赖及 backend/app/catalogs/robotwin2.json，镜像必须包含二者。Worker 与调度协议不变，无需重启 Worker。

降级脚本遇到草稿或归档集合会拒绝：旧 API 无法识别状态，可能把它们当作可运行集合。先妥善导出并处理这些集合，再降级；不要删除历史运行。迁移验收脚本仅在专用空库运行：MIGRATION_TEST_DATABASE_URL=... python -m scripts.check_suite_migration。
