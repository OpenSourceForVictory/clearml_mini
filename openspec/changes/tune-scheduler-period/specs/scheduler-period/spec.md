## MODIFIED Requirements

### Requirement: Configurable scheduler period
The scheduler SHALL wait 10 seconds after each completed or failed iteration by default, and SHALL use a positive `SCHEDULER_TICK_SECONDS` override when configured. It SHALL NOT hold the scheduling transaction lock during the wait.

#### Scenario: Default deployment
- **WHEN** the scheduler period is not explicitly configured
- **THEN** the next iteration starts after the previous iteration ends and a 10-second wait completes

#### Scenario: Explicit override
- **WHEN** `SCHEDULER_TICK_SECONDS=2` is configured
- **THEN** the next iteration uses a 2-second post-iteration wait
