# 默认显式不限制业务 PID：代码与发布验证

2026-11-19，Worker 0.21.4 / 协议17。null/未配置 execution_pids_limit 时，宿主机 TasksMax=infinity、Docker --pids-limit=-1；保留显式正整数和独立探测32。业务校验不再对null读取/检查pids.max。

## 定向测试

before.log：在0.21.3上运行调整后的测试，7失败、44通过，证明旧代码省略了明确不限制参数。
regression.log：相关10文件189通过、8跳过（3个真实用户systemd、5个root托管环境测试），1条Starlette弃用警告；不是全量测试。前端tsc --noEmit、OpenSpec strict及diff检查通过。
coverage.md采用scripts.test_coverage相同配置、全源码分母及门槛统计，全部Python行28.54%/分支13.62%，门槛未达到；前端和未插桩独立进程不计入统计。

## 发布

API镜像 mini-eval-api:unlimited-pids-0214-20261119 基于现场旧API镜像，仅更新worker和runner目录，使现有在线升级入口发布新节点包。未替换API业务代码/前端，也未执行数据库迁移，API迁移头保持原版本；发布前数据库备份已完成。节点包摘要为646a34ac98621c79ed4cc825f337888b839f770dac23f29be152b5fc25642857。

local4090-151、ascend910b3-29、rtx3080-182、rtx4090-183、rtx8000-177、rtx4090-157共6机13槽经原升级器完成SUCCEEDED；全部在线、软件0.21.4、协议17且build_digest一致，最终均恢复原接单状态、held_executions=0。29无需SSH，通过在线升级通道完成。

构建首次使用FROM sha256镜像ID被BuildKit当作远端镜像解析失败，改用绑定到原镜像ID的本地标签后通过。发布脚本首次把摘要字段写成digest，导致本机升级已成功后验收脚本KeyError；确认build_digest匹配并恢复原准入后，仅继续其余5机，没有重复安装。私有构建/部署证据在.runtime/unlimited-pids-release-20261119/，不入库。

## 真实运行验证与边界

脱敏结构化证据见live-results.json：

- 本机宿主机客户端：任务327488d6-05c0-4d9a-90b2-ebb732d098a7成功，业务cgroup pids.max=max、pids.current=303，实际创建300个线程，清理确认。
- rtx3080-182容器客户端：任务2743ed53-70ef-4057-af3f-ac5285fa5f91成功，pids.max=57402、pids.current=303，实际创建300个线程，清理确认。
- 29号NPU节点宿主机服务：实例31393990-c2fa-4178-a2af-bed5061d6cff完成300线程验证，pids.max=max，通过HTTP健康检查，然后STOPPED、资源释放。

保留失败：首次容器测试7e0e26b0-3845-43f5-9e10-833e4db60925假定实际pids.max必为max，读到76745后断言失败。独立本机docker --pids-limit=-1验证发现Docker inspect PidsLimit=null，容器systemd scope TasksMax=76745。修正验收为“平台取消固定256，记录实际运行时额度”，后续容器通过300线程验证，但不宣称容器内无限制。父级和运行时systemd默认值仍可能限制任务，本次未修改Docker daemon或全机systemd配置。

第二次容器验收脚本误在本机查询实际调度到182的容器，检查脚本中断；通过既有任务ID继续核验SUCCEEDED、输出及清理，没有重复提交。最初宿主机systemctl查询也使用了API attempt id而非真实execution id且未选user manager，因此不作为成功证据；采用业务进程自身上报的实际cgroup路径和值。

以上是实际systemd/Docker与Python线程/HTTP服务冒烟，不是GPU/NPU张量或真实模型评测，不证明RoboTwin业务成功。临时本机Docker对照容器已删除，3个客户端模板和1个服务模板已停用，所有测试执行清理完成。当前用户的direct final协议错误独立于PID修改。
