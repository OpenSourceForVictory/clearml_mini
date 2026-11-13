# 宿主机执行

管理员在机器详情 → 宿主机批准环境中配置命名环境，例如：

```json
{"simulation":{"python":"/opt/venv/bin/python","code_root":"/srv/simulation"}}
```

模板设置 `execution_mode=host`、`host_runtime.environment_name=simulation`；命令中的 python/python3 使用已批准解释器，脚本或原生可执行文件须位于 code_root，模块模式通过该目录的 PYTHONPATH 加载。业务依赖需事先安装，不自动 pip/conda 安装。环境名在模板中保持一致，绝对路径可以按机器不同。

节点需要 Worker v7、可用的 systemd 用户管理器、linger 与 cgroup v2。平台通过带版本的 `/admin/nodes/{id}/host-environments` 配置和 Worker 探测回执决定准入；缺失环境、旧 Worker 或过期报告不会启动 host 任务。修改环境不会改变已有执行快照。

Worker 的 `execution_modes` 可设置 `["host"]`，只运行宿主机任务并跳过 Docker 存储探测；默认同时支持 Docker/host，Docker 镜像仍需批准。宿主机检查目录读写，不承诺资产只读隔离；这是可信业务程序运行方式，不能替代容器或多用户安全沙箱。

## 生命周期

每次执行使用 `mini-eval-exec-<execution ID>.service` 用户单元，systemd 限制 CPUQuota、MemoryMax、TasksMax 和 RuntimeMaxSec，并用 control-group 停止子进程树。CUDA_VISIBLE_DEVICES 指向分配的 GPU；显存仍为调度准入估算，不能硬限制 CUDA 进程显存。

Worker 租约看门狗在失联/停机时停止所属执行。恢复依据稳定执行 ID、单元描述和本地 journal，拒绝重复启动，不使用裸 PID 杀进程。启动标记先持久化再请求启动，遇到崩溃窗口宁可失败清理，也不重复运行不确定任务。

控制记录及最多各256KiB的 stdout/stderr 保留在本地恢复目录相邻 `.host` 路径，不放到 NFS。业务 input/work/output 沿用该节点工作目录，逐回合 JSONL、产物及 API 上报与 Docker 一致。业务产物受原有数量/大小限制，拒绝符号链接和非法路径。

```bash
systemctl --user list-units 'mini-eval-exec-*'
systemctl --user status mini-eval-exec-执行ID.service
```

正常停止应通过平台取消；不要直接删除 journal 或启动同名 systemd 单元。

## 验证

`HOST_EXECUTION_TEST=1 .venv/bin/python -m pytest backend/tests/test_host_backend.py -q` 会创建短生命周期用户单元，验证进程树停止、稳定身份恢复、拒绝重复启动、超时、限额与租约失联。

`scripts/smoke_host_runtime.py` 只接受隔离 API `http://127.0.0.1:18090`，配合 `EVAL_ADMIN_PASSWORD`、`STORAGE_TEST_HOST` 运行四组合样本（Docker/host × SDK命令/direct）；使用模拟 GPU 槽位，验证真实容器、宿主机进程、网络协议、逐回合事件和报告，不代表真实模型质量验收。
