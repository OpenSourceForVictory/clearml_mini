## ADDED Requirements
### Requirement: HTTP-compatible request identifiers
The UI SHALL generate UUID request identifiers when randomUUID is unavailable but getRandomValues is supported.
#### Scenario: SSH registration over HTTP
- **WHEN** an administrator submits the SSH check without native randomUUID
- **THEN** a valid UUID v4 request ID is sent and registration proceeds
#### Scenario: Retrying a run
- **WHEN** a submission response is lost and the unchanged run is retried
- **THEN** the same idempotency key is reused
