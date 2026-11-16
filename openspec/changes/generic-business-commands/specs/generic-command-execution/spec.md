## ADDED Requirements
### Requirement: Generic business command execution
The platform SHALL support explicit exec commands for host and Docker clients and services, resolving absolute executables, cwd-relative executables and PATH names under the business identity without implicit shell parsing.
#### Scenario: Runtime and directory are independent
- **WHEN** a template specifies Bash, Python with options, or a native executable with a business directory
- **THEN** arguments are preserved, execution uses that directory and missing executables produce actionable errors
#### Scenario: Explicit shell
- **WHEN** a command uses bash -lc with a pipeline
- **THEN** shell syntax is interpreted only by that explicitly selected shell
### Requirement: Compatibility and authoring
The platform SHALL retain legacy templates and require protocol 14 for new execution fields, and provide UI-accessible guidance.
#### Scenario: Older worker
- **WHEN** an exec template is considered for a worker below protocol 14
- **THEN** admission is blocked with an upgrade message
#### Scenario: Results
- **WHEN** a generic command executes
- **THEN** ordinary-command exit status, SDK per-episode result requirements and service readiness retain their existing semantics
