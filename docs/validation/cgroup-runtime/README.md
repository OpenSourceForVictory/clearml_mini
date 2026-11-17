# cgroup 兼容与 NPU 重新纳管真机验证

验证日期：2026-11-17。对象为现有 Ascend 910B3 八卡节点；本次直接通过 root SSH 运行工作区探针源码，不代表已发布 Agent/Worker。

## 资源限制

目标内核 5.10，`/sys/fs/cgroup` 文件系统为 tmpfs，实际控制器为 cgroup v1（CPU 为 cpu,cpuacct 合并挂载）。

- 宿主机：短时 systemd 系统单元，以 nobody 普通账号执行；CPUQuota=100%、MemoryMax=128M、TasksMax=32、RuntimeMaxSec=10。新 `worker.host_identity.verify_system` 调用通过。
- 容器：使用目标已有 Python 3.12 镜像，关闭网络、只读文件系统、移除 capabilities；设置 1 CPU、128 MiB 内存、32 PID。新 `worker.resource_limits.verify_current` 调用通过。
- 反向检查：保持上述实际限制，分别要求 0.5 CPU、64 MiB、16 PID，宿主机和容器均分别返回 cpu_limit_not_enforced、memory_limit_not_enforced、pids_limit_not_enforced，共六项正确拒绝。
- 四台可达 GPU 节点使用 cgroup v2；已有 runner 镜像的相同 Docker 正向探针全部通过。

探针停止并清理临时单元/容器；没有调整内核启动参数、重启机器或部署新节点版本。这些检查验证内核限制读取和准入，不是压力/OOM 测试，也不是实际模型评测。

## 重新纳管：预检失败，不能声明完成

现有服务为 root 系统服务：Agent、updater 和 npu0–npu7 Worker 均运行中。Worker 使用 `/etc/mini-eval-node/npu0.json` 等配置，程序目录为 `/opt/mini-eval-node/app`，解释器为已有 Python 3.12。该布局不同于当前 root 安装器的 `/var/lib/mini-eval/control`。

将现有节点名称、现有 API 地址及 NPU 类型传给新 `onboarding.probe.collect`，分别验证 host 和 docker 两种模式，均识别八张 NPU，但返回：

```
existing: false
configs: []
services: []
issues: [existing_installation_requires_identity_migration]
```

这是直接调用真实 SSH 环境探针的预检结果；未执行网页操作、解除纳管、身份轮换、安装替换或回连验收。调用时使用非空旧节点标记模拟重新纳管的 node_id 条件，没有向控制面提交虚构身份。不能绕过该保护按新机器覆盖安装，否则可能产生重复服务及身份/恢复记录丢失。

后续需要明确支持旧自定义布局的受控迁移/恢复，覆盖八卡身份与恢复记录保留、活动执行阻断、失败回滚和回连验收，再进行完整重新纳管验证。

原始正向、反向及预检证据保留于本地 `.runtime/cgroup-real-validation/`（不提交该目录）。本次未验证升级后的业务执行、NPU 设备隔离、完整纳管或新版本长期稳定性。

 
## 发布前回归

相关 Python 用例 91 通过、5 项依赖隔离 root/systemd 环境的测试跳过；独立真实探针结果见上文，不把跳过计为通过。SSH 向导浏览器测试 3 项通过，涵盖 v1 层级和镜像待准备提示。OpenSpec strict 校验通过；API 前端构建通过。

定向分支覆盖测量范围为 onboarding/docker_prepare.py、worker/resource_limits.py、worker/host_identity.py，综合 coverage 指标 93%；这不是全项目覆盖率。首次回归保留旧 v2-only 断言失败，更新断言后重跑通过；补测过程中出现测试文本转义错误，修正后重新收集执行。原始报告保留于 .runtime/cgroup-final-tests*.log 与 .runtime/cgroup-coverage.json。

本变更未增加旧自定义目录迁移能力，原 NPU 节点预检阻塞继续保留，不能把 cgroup 探针成功或控制面发布当成重新纳管成功。

## 本机控制面发布验收

代码 9a79e9a 已发布到本机 18000：API 镜像 mini-eval-api:cgroup-0171，独立容器执行器 mini-eval-onboarding:cgroup-0171；保留现有显式 socket 路由，同时重启实际接入的用户级 mini-eval-onboarding.service。发布前无活动/待清理执行、无活动纳管操作；备份数据库、镜像身份和 Compose overlay，未修改 schema、机器账号或节点程序。

API 与执行器健康检查通过；通过真实 API 下载节点包验证 SHA-256、Worker 0.17.1、runner 构建材料及 root 探针源码。部署环境文件中的登录密码与当前账号不匹配（401），没有重置账号；后续管理接口验收使用仅在内存保存的五分钟维护凭据，不将其记录或提交。

通过已发布 API 的 SSH 环境检查接口，以 root、已有 Python 3.12 和全部八张 NPU 对真实节点执行重新纳管预检：
- 返回 cgroup_mode=v1-or-hybrid、device_count=8。
- 状态 CHECK_FAILED，唯一原因 existing_installation_requires_identity_migration。
- 不再返回 cgroup_v2_required。
- 未确认安装、未轮换节点凭据、未替换节点服务；该旧目录的完整重新纳管仍未通过。

镜像内的容器执行器也确认已移除 root 的 v2-only 拦截；此节点实际预检经过的是保留的宿主机执行器 socket。原始结果和回退材料仅保存于 .runtime/cgroup-release/。其他机器上的独立平台部署未同步，不能认为复制仓库或本机发布会更新它们。
