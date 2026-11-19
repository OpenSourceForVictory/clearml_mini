## ADDED Requirements

### Requirement: Host device environment is owned by the platform

The Worker SHALL reject reserved or unapproved task environment overrides for host execution. After merging valid business environment variables, it SHALL set the assigned GPU visibility value for GPU tasks or the restricted logical NPU visibility value for NPU tasks.

#### Scenario: Valid business variable

- **WHEN** a task overrides a template-approved business variable
- **THEN** the business value is present and the assigned device remains selected

#### Scenario: Reserved or unapproved variable

- **WHEN** a task provides a reserved device variable or a variable absent from the template allowlist
- **THEN** the Worker rejects the launch before creating its launch marker
