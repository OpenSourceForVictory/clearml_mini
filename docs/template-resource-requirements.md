# 模板资源要求

客户端与服务端模板可声明 `resource_requirements`；旧模板未声明时保留原有资源池默认行为。资源不从业务参数推断。

```json
{"version":1,"cpu":{"minimum":1,"default":2},"memory":{"minimum":2,"default":4},"accelerator":{"type":"gpu","mode":"shared","model":null,"memory":{"minimum":2,"default":4}},"allow_overrides":["cpus","ram_gb","requested_vram_gb"]}
```

CPU 单位为核，内存和设备内存为 GiB；当前粒度为单加速设备。资源按模板默认→提交→集合单项解析，每层检查下限及覆盖权限，不能以后一层隐藏非法前层。未传值不等同零或 null。有效申请及来源保存在任务快照中；重试和服务重启使用快照。

`allow_overrides` 可包含 `cpus`、`ram_gb`、`accelerator_type`、`gpu_mode`、`requested_vram_gb`、`required_gpu_model`。为兼容历史接口，后两类名称沿用 GPU 命名；请求也接受中性别名 `accelerator_mode`、`accelerator_memory_gb`、`accelerator_model`，同一字段重复提供别名会拒绝。

设备共享表示内存预留准入，不提供显存硬隔离或算力比例保证；设备独占不等于整机独占，服务实例共享是另一项配置。CPU/内存额度通过容器或宿主机 cgroup 执行。NPU 必须通过后端能力与硬件验证，探测到设备不代表可执行。

## 纳管与宿主机限制

宿主机 CPU/RAM 限制要求 cgroup v2、用户 systemd 管理器及委派的 `cpu memory pids` 控制器。每个任务创建独立 systemd unit，子进程继承限制；CPU 是时间配额，不是核绑定。内存到限可能触发组内 OOM。开始业务程序前读取内核实际限制，失败则不启动业务。

SSH 纳管/重新纳管的环境检查会启动短验证任务并清理测试 unit。若缺少委派，界面提示安装时修复并要求临时 sudo 权限。确认安装后写入 `/etc/systemd/system/user@UID.service.d/60-mini-eval-resources.conf`，配置 `Delegate=cpu memory pids`、启用 linger，并刷新用户管理器后复验。不会赋予任务用户 root 身份或永久 sudo 权限；已有不同内容的配置拒绝覆盖。cgroup v1 或用户管理器不可用需先安排管理员维护，不自动改启动参数或重启机器。

手动安装可由管理员执行 `sudo python3 scripts/configure_node_resources.py --uid UID`，然后由目标用户执行 `systemctl --user daemon-reexec`、`python3 -m worker.resource_limits`。`daemon-reexec` 重新执行用户服务管理器，不重启已运行服务，但修复应在节点排空后执行。复验失败时保持 host 不可用。已有机器可排空后走原机器重新纳管流程；后台 HostEnvironments 在独立线程持续复查，升级后也重新验证，不阻塞 Agent 心跳。

Docker 不依赖用户级委派，CPU/内存使用容器运行时限制。纳管时要求 Docker 可达，runner 镜像缺失仅提示手动准备，不阻止安装；实际容器限制由执行集成测试验证。目前不提供在纳管阶段对任意 Docker 环境自动修复的承诺。

## 异构能力边界

NVIDIA 既有设备身份和旧协议兼容；NPU 任务要求协议 11、`ascend:` 稳定身份、有效遥测及明确后端能力。节点本地 `npu_runtime` 必须显式批准驱动路径和设备映射，额外开启 `allow_shared` 才发布共享能力。当前 NPU Docker 适配只映射所分配的 `/dev/davinciN` 及必要管理设备、只读驱动目录，容器内逻辑设备编号为 0。真实 910B3 已验证单设备可见及 ACL 内存申请/释放；NPU host 使用系统级 Worker 管理 unit，业务程序由明确批准的非 root 用户运行；内核设备规则拒绝访问未分配的计算设备。此后端支持经实际验证的 cgroup v1/v2，不要求把现有 v1 机器自动重启切换为 v2。完整平台跨物理节点验收仍须单独记录。

## 升级与恢复

数据库迁移 `r841a23 → s952b34` 增加模板资源契约、服务资源快照及加速类型/后端字段，不改旧 GPU 身份。先备份数据库，在隔离库演练，排空后升级 API 与 Worker，再启用新的能力。存在新资源契约或 NPU 数据时拒绝降级删除字段；回退使用升级前备份并核对任务，不向旧二进制暴露 NPU 槽位。

预留账本始终包含客户端与服务端的 CPU/RAM 和设备内存。资源不足时排队，设备共享与独占互斥；未确认清理的执行不能释放预留。预检是瞬时结果，领取时仍须事务加锁复查，不能把预检当作已占位。


## NPU 宿主机执行配置

此模式要求系统级 Worker（root 控制进程），不把 sudo 权限或密码传给业务程序。节点管理员在本地配置批准用户及 CANN 环境，例如：

```json
{"accelerator_type":"npu","gpu_index":2,"execution_modes":["host","docker"],"npu_runtime":{"approved":true,"allow_shared":true,"host":{"approved":true,"user":"HwHiAiUser","environment":{"LD_LIBRARY_PATH":"/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common"}}}}
```

用户必须存在且不能是 root；CANN、驱动和批准的 Python/业务代码需对该用户可读/可执行，工作目录及 runs 的父路径需要可遍历，执行目录由平台单独赋予该用户。不要为此放宽凭据目录。后台探测检查设备和实际内核限制；任一失败均不发布该环境为就绪。运行环境中的业务参数不能修改 ASCEND_* 设备选择变量。物理卡号只用于设备规则，CANN 在只允许一张卡时使用逻辑编号 0。上例是显式开启共享的本地配置；当前 SSH 纳管界面新安装默认关闭共享，重新纳管默认保留原配置，只有管理员明确选择才修改该字段。

CPU/RAM/pids 限制由 systemd 执行，DevicePolicy=closed 仅允许分配卡和必要管理设备，进程无 capabilities、不能新增特权。每个任务仍有独立 unit，停止/超时按 control-group 清理整个进程组。共享模式需要 allow_shared=true；调度按设备内存预留，不能承诺显存硬隔离或驱动管理接口的完全安全沙箱。

参考：[systemd 执行控制](https://freedesktop.org/software/systemd/man/latest/systemd.exec.html)、[昇腾设备可见性](https://www.hiascend.com/doc_center/source/en/CANNCommunityEdition/910/maintenref/envvar/envref_07_0028.html)。


实机验收脚本（在目标 NPU 机器的仓库副本运行）：

```bash
sudo /path/to/python3 scripts/validate_npu_host.py --device 2 --user HwHiAiUser --python /path/to/approved/python3
```

脚本启动两个独立宿主机服务，共享指定卡，各申请 1 MiB NPU 内存，验证实际 HTTP 可访问、非 root 身份和单设备可见；强制终止一个后检查另一个仍运行，最后清理全部临时 unit 和文件。CANN 需匹配主机驱动，Python 至少 3.10。生产纳管仍需管理员安装系统级 Worker 并提供上述本地批准配置，普通用户 Worker 不自动提权。
