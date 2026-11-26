# 日志采集磁盘不足边界（2026-11-26）

触发条件：日志 v2 的 `runner.log_capture.capture` 已经启动业务子进程，随后诊断环或协议事件落盘抛出 `ENOSPC`。修复前异常进入 `finally`，采集器杀死仍在运行的子进程。用提交前版本在隔离临时目录注入 `Ring.write -> OSError(ENOSPC)`，业务先输出 1 MiB、随后写完成标记；得到 `old_exception OSError 28`、`old_business_finished False`。这是确定性故障注入，不是节点实际磁盘耗尽。

修复后采集器在首次诊断/协议写入失败后停止该写入通道，继续读取业务 stdout/stderr 至 EOF，返回真实子进程退出码；退出记录若也无法落盘，只写错误提示，不把业务退出码改成采集器异常。协议记录丢失仍由平台结果完整性检查处理，不等于协议成功。宿主机服务日志或容器 stderr 会出现采集故障类型。

验证：`backend/tests/test_log_capture.py` 对诊断环、协议事件、容器 stderr 尾部和退出记录注入 `ENOSPC`，对协议预算注入 `RuntimeError`，业务输出 1 MiB 后完成并留下标记；相关日志发送、宿主机监督和恢复执行组合回归最初 79 项通过。随后加入 opt-in 真实 Docker 用例：容器的 `/tmp/capture` 使用 64 KiB tmpfs，业务连续输出 3000 条协议事件并写入宿主机绑定的完成标记。旧镜像自带 Runner 首次运行退出 1，stderr 为 `OSError: [Errno 28] No space left on device`，业务未写标记；设置 `--workdir /code` 并只读绑定当前 Runner 后，容器退出 0、stderr 包含 `protocol capture unavailable: OSError`，业务标记内容为 `complete`。`RECOVERY_RUNTIME_TEST=1 .venv/bin/python -m pytest -q backend/tests/test_log_capture.py` 最终 10 通过，含相关文件的组合回归 82 通过。针对 `runner/log_capture.py` 单独插桩统计：159 条语句中 143 条覆盖（89.94%），48 条分支中 41 条覆盖（85.42%）；这不是全项目覆盖率。这个实验真实写满了容器临时文件系统，但没有走平台 Worker.execute、宿主机 systemd 或真实模型；OpenSpec 4.10 仍待完整验收。旧镜像不会自动获得源码修正，使用它执行日志 v2 时仍存在原错误。
