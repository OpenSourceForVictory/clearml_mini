## Purpose
为平台用户提供直接在工作台内检索和阅读的操作帮助、协议接入说明与可运行样例，使业务接入不依赖查阅项目源码，同时支持维护者定位实现与同步文档。
## ADDED Requirements
### Requirement: In-platform contextual help
The platform SHALL provide searchable user guidance and contextual help for user and administration workflows without losing current form drafts.
#### Scenario: Help during editing
- **WHEN** a user opens guidance while editing a template and closes it
- **THEN** the draft and focus are retained and no mutation request is sent
#### Scenario: Member guidance
- **WHEN** a member opens the guide
- **THEN** user workflows are available and administrator-only topics are hidden
### Requirement: Business integration materials
The platform SHALL explain ordinary commands, SDK command wrapping, Python SDK and direct reporting, and provide downloadable SDK and runnable samples from the interface.
#### Scenario: Download and validate
- **WHEN** a user downloads and extracts the integration bundle
- **THEN** samples run locally without credentials and demonstrate valid input and reporting semantics
#### Scenario: Distinguish execution and evaluation
- **WHEN** a user reads integration guidance
- **THEN** guidance distinguishes exit status, episode completion, business success and final metrics
### Requirement: Maintainable guidance
Each code change SHALL update affected documentation or project Skill; the repository SHALL contain an agent entrypoint with implementation and validation references.
#### Scenario: Change protocol
- **WHEN** protocol handling changes
- **THEN** affected guide examples, SDK bundle and references are checked together
