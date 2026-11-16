# 模板资源要求

客户端与服务端模板可声明 `resource_requirements`；旧模板未声明时保留原有资源池默认行为。资源不从业务参数推断。

```json
{"version":1,"cpu":{"minimum":1,"default":2},"memory":{"minimum":2,"default":4},"accelerator":{"type":"gpu","mode":"shared","model":null,"memory":{"minimum":2,"default":4}},"allow_overrides":["cpus","ram_gb","requested_vram_gb"]}
```

CPU 单位为核，内存和设备内存为 GiB；当前粒度为单加速设备。资源按模板默认→提交→集合单项解析，每层检查下限及覆盖权限，不能以后一层隐藏非法前层。未传值不等同零或 null。有效申请及来源保存在任务快照中；重试和服务重启使用快照。

`allow_overrides` 可包含 `cpus`、`ram_gb`、`accelerator_type`、`gpu_mode`、`requested_vram_gb`、`required_gpu_model`。为兼容历史接口，后两类名称沿用 GPU 命名；请求也接受中性别名 `accelerator_mode`、`accelerator_memory_gb`、`accelerator_model`，同一字段重复提供别名会拒绝。

设备共享表示内存预留准入，不提供显存硬隔离或算力比例保证；设备独占不等于整机独占，服务实例共享是另一项配置。CPU/内存额度通过容器或宿主机 cgroup 执行。NPU 必须通过后端能力与硬件验证，探测到设备不代表可执行。
