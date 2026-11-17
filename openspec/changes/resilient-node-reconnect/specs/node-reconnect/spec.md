## ADDED Requirements
### Requirement: Nonblocking probe preparation
Worker SHALL register and heartbeat independently of optional probe installation; readiness jobs SHALL remain gated by actual capability.
#### Scenario: Dependency failure
- **WHEN** dependency preparation fails or waits for a lock
- **THEN** Worker can register and heartbeat and logs a safe failure category
### Requirement: Bounded resumable verification
Enrollment SHALL default to 300 seconds of verification, allow bounded configuration and authenticated verification retries without reinstalling.
#### Scenario: Timeout recovery
- **WHEN** the original actor retries verification after a timeout
- **THEN** current credentials query the same installation and complete only if node revision and release still match
#### Scenario: Changed installation
- **WHEN** the node has been re-enrolled or modified since the original operation
- **THEN** retry refuses to complete the newer installation
