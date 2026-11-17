# 通用目录挂载

目录挂载使用模板逻辑名称和节点映射，适用于客户端、服务端 Docker 模板；需要 Worker v9。旧模板资产挂载规则不变。宿主机模板仍使用批准环境和原有逻辑资产路径，通用设备挂载、独占设备和自动端口分配另行实现。

## 节点配置

管理设置 → 机器管理 → 查看配置 → 镜像与逻辑资产 → 批准的额外目录。填写逻辑名、该机器真实目录，按需批准写入。目录必须预先存在，平台不自动创建、修改权限或下载数据。

节点运行配置 API `PUT /api/v1/admin/nodes/{id}/runtime` 沿用 `expected_revision` 和排空切换：

```json
{
  "expected_revision": 0,
  "config": {
    "images": ["my-client:1"],
    "assets": {},
    "directories": {
      "dataset": {"path": "/mnt/eval-share/datasets", "writable": false},
      "shared_results": {"path": "/mnt/eval-share/results", "writable": true}
    }
  }
}
```

两台机器可用同名 `dataset` 分别指向不同路径。Worker 异步检查存在性、目录权限、符号链接和受保护路径；配置尚未生效、报告过期或被引用目录未就绪时不能领取任务。未使用的目录检查失败不阻塞其他任务。已运行任务保留自己的配置快照。

目录映射随容器运行配置回执按 60 秒检查报告时间；配置版本变化或具体目录检查失败仍即时阻止新任务。Worker 在线状态与该回执有效期分别判断。

节点检查验证 Worker 账户权限；镜像内的业务 UID/GID 也必须能访问该目录。可写批准不会绕过文件系统权限或自动 chmod。共享可写目录不提供任务隔离，业务应自行管理文件命名与并发；任务临时数据优先使用 work/output。

## 模板配置

运行模板管理 → 注册/复制模板 → Docker → 通用目录挂载。增加挂载名称、来源和容器路径，选择只读/必需，可展开声明预览。声明预览不包含尚未分配节点的实际宿主路径。

```json
{
  "runtime": {
    "mounts": {
      "dataset": {"source": "directories:dataset", "target": "/datasets", "read_only": true, "required": true},
      "results": {"source": "directories:shared_results", "target": "/shared-results", "read_only": false, "required": true},
      "scratch": {"source": "work", "target": "/workspace/scratch", "read_only": false, "required": true}
    }
  }
}
```

来源支持 `work`、`output`、`cache`、`assets:逻辑名`、`directories:逻辑名`。work/output 每个执行独立；cache/assets 始终只读；额外目录默认只读，读写挂载需要节点批准。每个模板最多 16 项，不允许目标彼此重叠、覆盖平台协议目录或保留路径。业务命令和环境变量可用 `${mounts:dataset}` 获取容器路径，直接协议、SDK 每回合业务命令和服务入口均支持。

`required=false` 仅允许节点缺少这个逻辑映射时跳过；已经配置但目录不可用或写权限不符仍拒绝启动。可选项的占位符仍是声明的容器路径，业务必须自行判断是否存在。模板快照中的完整声明保存到任务；Worker 在启动前按执行快照再次检查批准路径与权限，拒绝已变成符号链接或失效的目录。

新字段存储于已有 runtime JSON，无需新增数据库列。旧 Worker 不会领取非空 mounts 的模板；上线需先更新 API，再升级 Worker，最后配置目录并等待就绪。

## 验证边界

`backend/tests/test_directory_mounts.py` 覆盖契约边界、只读/写入批准、Worker 版本、配置修订、缺失映射、就绪错误脱敏、SDK 每回合展开和不同节点路径解析。

```bash
DIRECTORY_MOUNT_TEST=1 .venv/bin/python -m pytest backend/tests/test_directory_mounts.py -q
```

真实 Docker 测试使用同一测试主机上的两套不同目录配置，验证读入各自数据、只读拒写、共享目录写入和独立工作区别名。它不代表五节点已经升级，也不代替 OpenSpec 要求的两台真实节点验收。当前功能尚未部署生产。
