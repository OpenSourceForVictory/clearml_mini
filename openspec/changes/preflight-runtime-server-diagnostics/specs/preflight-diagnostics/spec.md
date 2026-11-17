## ADDED Requirements

### Requirement: Named client runtime blockers
Preflight SHALL identify the client template's requested host environment or container image when a candidate device lacks the required execution mode, Worker protocol, or runtime readiness. It SHALL not reveal host paths or credentials.

#### Scenario: Host environment on an older Worker
Given a host template using logical environment `robotwin` and an older Worker, when preflight runs, then its candidate-device reason names `robotwin`, the required protocol and the current protocol.

#### Scenario: Container mode unavailable
Given a Docker template and a host-only device, when preflight runs, then its candidate-device reason names the requested image and says container execution is unavailable.

### Requirement: Per-device managed-service diagnostics
Managed-service preflight SHALL return each service's pool and role-matching server candidates with node name, device index, blocking reasons and recommended action. Read-only service network and port checks SHALL use the same diagnosis as the run detail.

#### Scenario: Server card blocked
Given a service candidate whose storage report is stale or whose GPU is occupied, when preflight runs, then the confirmation page identifies the service, pool, node, card and specific blocker.

#### Scenario: No server-role member
Given a selected pool without a device on a server-role machine, when preflight runs, then it reports that no server-role device is available rather than listing client-only devices as server candidates.
