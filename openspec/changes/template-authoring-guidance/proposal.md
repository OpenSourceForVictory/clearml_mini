# Proposal

## Why

模板注册要求用户理解命令数组、参数绑定和节点目录映射，但页面只有简短提示。查看配置目前只显示原始 JSON，与填写表单的组织方式不同，用户难以理解和复核。

## What Changes

- 上层区分客户端/服务端，下层区分已注册模板/注册新模板；复制切换到注册页签，切换下层页签保留草稿。
- 注册页面增加操作步骤、按客户端/服务端和 Docker/宿主机切换的说明与完整填写示例。
- 在三类参数、命令、环境变量、资产及挂载、健康检查旁提供就近帮助。
- 查看配置按注册表单相同分组只读展示，支持关闭、复制新版本和折叠原始 JSON。
- 浏览帮助不改动草稿、不触发提交；示例明确依赖用户准备的代码、镜像或批准环境。

## Capabilities

### New Capabilities
- `runtime-templates`: 在既有未归档变更的同名能力下补充模板操作指导与结构化只读查看要求；不重复定义执行契约。

### Modified Capabilities
无。主规格尚未包含 runtime-templates；与 generic-machine-runtime-management 的同名增量要求互补。

## Impact

影响 frontend/src/templates.tsx、参数/挂载编辑器、相关样式、模板浏览器测试和 docs/runtime-templates.md。共享展示组件服务注册和查看；API、调度、Worker 和数据结构保持兼容。后台机群稳定性测试继续运行，生产发布避免中断该测试。
