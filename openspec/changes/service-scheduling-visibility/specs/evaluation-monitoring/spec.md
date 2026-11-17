## ADDED Requirements

### Requirement: Managed service scheduling is visible in batch results

The platform SHALL show scheduling diagnosis for each managed service definition before a service instance exists, including the selected server pool, candidate devices, and admission blockers. The diagnosis SHALL be read-only and SHALL use the same persisted readiness rules as server admission.

#### Scenario: Server environment blocks allocation
- **WHEN** a managed service has no instance and all server candidates have stale storage reports
- **THEN** the batch result view shows the server pool and per-device storage blocker without claiming that a service process has started

#### Scenario: Service instance starts
- **WHEN** the scheduler creates a service instance
- **THEN** the batch result view replaces the pending diagnosis with its current startup and health state, and offers access to probe details and logs

#### Scenario: Status request fails
- **WHEN** the orchestration detail request fails temporarily
- **THEN** the batch result view reports the failure and retries without displaying stale state as current
