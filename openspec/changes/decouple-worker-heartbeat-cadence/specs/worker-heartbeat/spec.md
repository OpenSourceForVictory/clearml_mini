## ADDED Requirements
### Requirement: Independent base heartbeat cadence
Worker SHALL default its base heartbeat to five seconds with bounded jitter and independently poll for work; execution control cadence SHALL remain unchanged.
#### Scenario: Idle worker remains responsive
- **WHEN** no base heartbeat is due
- **THEN** the worker may still poll for work within the existing normal two-second interval
#### Scenario: Configuration changes
- **WHEN** a configuration receipt changes
- **THEN** the next control iteration reports it without waiting for the periodic full-report deadline
#### Scenario: Transient API failure
- **WHEN** a heartbeat fails
- **THEN** the worker retains unsent receipt state and respects existing bounded retry rules
### Requirement: Compatible report freshness
Worker SHALL periodically refresh unchanged complete receipts within the existing server freshness window, and SHALL reject unsafe cadence settings.
#### Scenario: Stable configuration
- **WHEN** receipts are unchanged
- **THEN** full receipts are omitted until the configured full-report interval expires while resource telemetry continues on base heartbeats
