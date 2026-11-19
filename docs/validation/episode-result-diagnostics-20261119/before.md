# 失败证据与修复范围

现场日志：三个回合在 runner/host_episode.py 抛出 `direct protocol episode must emit exactly one final result`，随后 `CS client startup failed: 3 of 3 business episodes had execution errors; remaining episodes were attempted`。这些业务子进程退出码为 0；原日志不能区分缺失 final、重复 final 和非对象 final。

代码审查另发现 backend/app/reports.py 的 compare() 对正常完成且逐回合上报的普通命令 success_rate=None 做减法。本地直接调用复现 `TypeError: unsupported operand type(s) for -: 'NoneType' and 'NoneType'`。已增加单元及真实测试 API 请求回归，检查返回 200 和 protocol_not_applicable。

实施首轮关联回归 3 failed / 220 passed / 4 skipped：SDK 非法结果原测试仅接受 TypeError/ValueError，新受控路径为 EpisodeExecutionError。扩大到 Runner 入口后的第二轮 5 failed / 273 passed / 28 skipped，同样是 SDK 结果文件错误的旧异常类型断言。两个原始日志均保留；测试现断言明确结果诊断码、保留退出码 0、没有成功 final/产物，未删除失败用例或放宽结果合法性要求。
