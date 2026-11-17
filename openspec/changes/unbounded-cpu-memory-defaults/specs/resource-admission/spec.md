## ADDED Requirements

### Requirement: Pool CPU and memory values are defaults, not per-task ceilings
The platform SHALL use positive pool CPU and memory values only when a task has no explicit value for that axis. A template or submission MAY request more than the pool default; preflight and scheduling SHALL assess the effective request against node configured capacity, fresh host telemetry, and existing reservations.

#### Scenario: Template exceeds pool default but fits node
Given a pool default of one CPU and two GiB and a template requesting eight CPUs and sixteen GiB, when a node has sufficient configured and observed capacity, then the task is eligible for admission.

#### Scenario: Explicit request exceeds node capacity
Given a task requesting more CPU or memory than a node can supply, when preflight or scheduling runs, then that node is rejected with the relevant capacity reason.

### Requirement: Unrequested CPU and memory may be unbounded
The platform SHALL allow each pool default to be zero. When a task does not explicitly request an axis and that default is zero, preflight and scheduling SHALL reserve zero for that axis, and Worker SHALL omit that axis's runtime quota. Other resource and concurrency checks SHALL remain active.

#### Scenario: Both defaults zero
Given a pool with zero CPU and memory defaults and no task requests, when a task is allocated and launched, then its reservation is zero and neither CPU nor memory quota is configured.

#### Scenario: One axis requested
Given a zero CPU default and a positive memory request, when a task is admitted, then CPU has no quota while memory capacity, telemetry, reservation, and runtime enforcement remain active.

#### Scenario: Joint client and service admission
Given a client and managed service sharing a node, when the planner evaluates their pair, then it uses the sum of effective requests on each axis and preserves node concurrency and accelerator checks.
