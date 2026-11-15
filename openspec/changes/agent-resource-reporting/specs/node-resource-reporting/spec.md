# Spec Delta
## Purpose
统一节点资源采样、传输和调度消费的边界，使多GPU节点的CPU、内存及显存状态可被可信地使用，并保证慢资源探测不会拖住节点控制心跳和执行租约。
## ADDED Requirements
### Requirement: Versioned node resource source
The system SHALL negotiate a versioned Agent resource source and preserve legacy Worker reporting when that source is absent.
#### Scenario: Agent owns telemetry
- **WHEN** a current Agent session declares resource reporting
- **THEN** GPU samples are matched by device identity and Worker reports cannot overwrite them
#### Scenario: Invalid sample
- **WHEN** a sample fails, expires or has a mismatched GPU identity
- **THEN** it cannot authorize new resource allocations
### Requirement: Independent control heartbeat
The system SHALL keep resource probes and durable configuration writes outside periodic control heartbeat paths, with bounded in-flight work.
#### Scenario: Storage or command stalls
- **WHEN** a storage check or hardware command stalls
- **THEN** control heartbeats continue and affected resources become unavailable for new work
#### Scenario: Durable confirmation
- **WHEN** configuration storage stalls or fails
- **THEN** heartbeats continue without acknowledging an unapplied revision
### Requirement: Host resource admission
The system SHALL check fresh Agent host capacity and available memory in addition to existing resource reservations.
#### Scenario: Memory pressure
- **WHEN** host memory is insufficient despite configured capacity
- **THEN** new tasks wait while existing execution leases remain renewable

### Requirement: Vendor neutral inventory
The system SHALL distinguish GPU and NPU device kinds and vendors, and SHALL represent unavailable metrics as unknown rather than free resources.
#### Scenario: Ascend inventory without validated runtime
- **WHEN** an Ascend device is detected but its memory or execution adapter is not validated
- **THEN** it is reported as not execution-ready and cannot be assigned through the NVIDIA GPU execution path
#### Scenario: CPU-only host
- **WHEN** a host has no NVIDIA tools
- **THEN** the Agent still reports CPU and system memory without requiring a GPU probe
