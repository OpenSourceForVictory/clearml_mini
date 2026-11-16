## ADDED Requirements
### Requirement: Explicit non-root host identity
Root-managed Workers SHALL run host business programs under a node-approved non-root identity and reject absent or changed identities.
#### Scenario: Root Worker without account
- **WHEN** a root GPU Worker has no approved business identity
- **THEN** host execution is rejected rather than running business as root
#### Scenario: Inaccessible directory
- **WHEN** the selected business identity cannot access a required path
- **THEN** admission or launch reports that identity, path and required access before business starts
### Requirement: Root onboarding
New root onboarding SHALL install system Agent and Workers and create a consistent ordinary business account without sudo privileges.
#### Scenario: Conflicting account
- **WHEN** the requested UID or GID belongs to another account
- **THEN** installation stops without changing that account
### Requirement: Legacy compatibility
Existing user-managed nodes SHALL retain their execution identity until an explicit drained migration.
#### Scenario: Upgrade legacy node
- **WHEN** a legacy user Worker receives new code
- **THEN** its user systemd execution remains compatible
### Requirement: Accelerator-aware onboarding
SSH onboarding SHALL discover the selected GPU or NPU adapter and validate driver prerequisites without silently substituting devices.
#### Scenario: NPU selection
- **WHEN** root onboarding selects NPU
- **THEN** installation uses Ascend identities and a non-root business identity with the existing NPU device access policy
