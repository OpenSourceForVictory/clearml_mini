## ADDED Requirements

### Requirement: Latest observation updates do not queue on global scheduling lock
Idle Worker heartbeats, Agent telemetry heartbeats, and service probe results SHALL update the latest database state without acquiring the global scheduling advisory lock. Concurrent updates to the same node or service SHALL preserve monotonic sequence and current configuration state.

#### Scenario: Independent nodes report while admission is active
Given admission holds its scheduling lock, when independent nodes report idle telemetry, then their reports may update the latest node state without waiting for that global lock.

#### Scenario: Service probe result
Given a current service probe session, when a result arrives, then its latest readiness state and sequence are stored without acquiring the global scheduling lock; stale generations remain rejected.

### Requirement: Execution transitions are bounded under lock contention
An active execution heartbeat SHALL retain lease and status synchronization. When the global lock is busy, it SHALL return a retryable response without waiting on that lock while holding a database connection.

#### Scenario: Busy scheduler
Given another transaction holds the scheduling lock, when an active execution heartbeat arrives, then the API responds with HTTP 503 and a retry hint instead of queueing for the lock.
