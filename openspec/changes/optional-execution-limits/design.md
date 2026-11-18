## Decisions（按用户追加要求修订）
保留正数/默认300和平台有限值上限。timeout_seconds=0表示不限总执行时长；服务端原行为不变。调度只跳过总时限判断；租约、启动期限、取消及清理继续有效。宿主机 RuntimeMaxSec=infinity；Docker直接启动适配入口，不调用旧镜像定时supervise，避免0立即过期。协议16保护不限请求。
资源调度预约与运行硬上限分离：resources.cpu_unlimited/memory_unlimited 为可选布尔值，缺省false。cpus/ram_gb 始终为正数预约量；用户选不限时，模板提供最低量（未明确覆盖数量时自动采用），没有模板最低量则必须明确填写正数。Run.reserved_cpu/ram_gb继续存调度预约，领取时 task.cpus/ram_gb 对不限轴传0以复用现有Worker不设置quota的行为，并附带reserved_cpus/reserved_ram_gb说明。模板允许修改cpus/ram_gb才允许对应不限标志，锁定及最低要求继续生效。GPU/NPU显存预约不变；实际超出预约量的资源不保证可用，宿主机上级cgroup限制仍有效。
不修改旧资源0的含义，不改变旧任务快照、资源池默认值或业务参数。暂无资源模板的客户端/服务端界面在选择不限时要求填写该项最低申请量。模板提交选项采用最低量作初始值，允许提高预约量。
## Validation
0时限快照、有限值上限、旧Worker阻断、租约/启动/取消保持；不限内存仍预约最低量、低于最低或锁定拒绝、容量不足仍排队；宿主机与Docker启动参数；UI预检和确认。相关专项验证，不部署或声称真机验收。
