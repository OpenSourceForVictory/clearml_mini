# 业务参数与环境变量中的中划线

模板“业务参数”支持 task-config、control-mode、state-source 等名称。首字符为字母，后续可用字母、数字、下划线或中划线，最长 64 字符；对象属性使用相同规则。平台保留字段仍不可使用。

例如参数 task-config 的默认值为 demo_clean：
- 选择命令行参数，绑定名称填写 --task-config，程序收到两个 argv 项：--task-config、demo_clean。
- 选择环境变量，绑定名称可填写 TASK_CONFIG 或 task-config，程序通过原名称读取。
- 选择标准输入 JSON，输入文件 business 对象中保留 task-config。

动态参数映射下拉框使用原参数名。集合、运行覆盖与模板声明必须使用完全相同的键；task-config 与 task_config 是两个不同字段，不会自动转换。旧下划线字段继续有效。

固定环境变量和参数映射的环境变量名允许字母或下划线开头，后续可含中划线，最长 128 字符。允许覆盖、数量、大小、空字符及设备保留变量的限制不变。Python 使用 os.environ["task-config"] 读取。

Shell 自身不允许 export task-config=...，也不能用 $task-config 作为完整变量名读取；部分 Shell 会丢弃这类环境项。使用 Shell 脚本时建议绑定 TASK_CONFIG；这属于 Shell 兼容边界，平台不改写名称。

更新 API、前端及执行节点 Worker/Runner（Worker 0.18.3，协议 15）后生效。旧 Runner 仍可能拒绝带中划线的 schema 或环境名称；更新 API 不会替换节点代码。容器内使用旧内置 Runner 时也须更新该 Runner。此变更无需数据库迁移，不自动修改已发布模板或集合。

专项测试覆盖共享契约、真实本地子进程 argv/env、动态映射和提交；不代表 Docker/NPU 真机业务验收，不宣称全项目覆盖率达标。

## 本次验证记录（2026-11-18）

共享契约、image_runtime、task_input 与宿主机业务子进程专项共 69 项通过；类型化模板 API、批次覆盖及快照专项 7 项通过；模板编辑界面 20 项通过，追加保留变量拒绝检查后 2 项再通过。TypeScript、Vite 构建与 OpenSpec 严格校验通过。

首轮 68 项中 67 通过，1 项失败为新增测试误导入 host_runtime（真实函数为 host_config）；修正测试导入后通过。保留 Starlette 弃用与 Vite chunk 大小提示。未运行全量 python -m scripts.test_coverage，未统计或声称达到目录/功能行分支门槛；前端与独立子进程未合并覆盖率。未部署到已纳管节点。
