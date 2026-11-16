## Purpose
支持宿主机业务在批准的代码目录内独立选择启动目录和解释器，满足相对配置文件及不同 Python 环境需求，同时保留平台独立任务数据目录和旧模板默认行为。
## ADDED Requirements
### Requirement: Independent business working directory
Host templates SHALL support an optional working_directory independent of the approved Python interpreter and platform execution data directories.
#### Scenario: Explicit directory
- **WHEN** a client or server template selects a valid directory under its approved code root
- **THEN** the business process starts there using the approved interpreter and relative files resolve there
#### Scenario: Legacy template
- **WHEN** working_directory is absent
- **THEN** execution retains the existing per-task working directory
#### Scenario: Invalid directory
- **WHEN** the directory is missing or resolves outside the approved root
- **THEN** execution is rejected before starting the business program
### Requirement: Consistent authoring and admission
The UI SHALL preserve the directory in registration, copy and read-only views; scheduling SHALL reject workers that cannot honor it.
#### Scenario: Old worker
- **WHEN** a template has working_directory and a worker uses protocol below 13
- **THEN** that worker is ineligible rather than ignoring the directory
